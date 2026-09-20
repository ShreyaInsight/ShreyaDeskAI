import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch
from backend import scanner, market_cache as m

class MarketCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.patch=patch.object(scanner,'DB_PATH',Path(self.temp.name)/'cache.db');self.patch.start()
    def tearDown(self): self.patch.stop();self.temp.cleanup()
    def test_official_allowlist_plus_segment_and_debt_defenses(self):
        official=[{'SYMBOL':s,'SERIES':'EQ'} for s in ['REAL','INDEX','DEBT-SG','BONDCO','MISSING','REAL-RE1']]+[{'SYMBOL':'REAL2','SERIES':'BE'}]
        def row(s,**kw):return dict(tradingsymbol=s,instrument_token=1,exchange='NSE',segment='NSE',instrument_type='EQ',name='COMPANY',**kw)
        rows=[row('REAL'),row('UNLISTED'),row('DEBT-SG'),row('REAL2-BE'),row('REAL-RE1')]
        rows.append({**row('INDEX'),'segment':'INDICES'});rows.append({**row('BONDCO'),'name':'GOVT BOND'})
        self.assertEqual(set(m.match_equities(rows,official)),{'REAL','REAL2-BE'})
    def test_daily_cache_reuse_and_next_day_refetch(self):
        broker=Mock();broker.historical_data.return_value=[dict(date='2026-09-04T00:00:00+05:30',open=100,high=105,low=95,close=101,volume=10)]
        with patch('backend.main.get_kite',return_value=broker),patch.object(m.history_limit,'acquire') as throttle,patch.object(m,'today',return_value=date(2026,9,7)):
            m.refresh_history({'REAL':1},date(2026,1,1));m.refresh_history({'REAL':1},date(2026,1,1))
            self.assertEqual(broker.historical_data.call_count,1);self.assertEqual(throttle.call_count,1)
            self.assertEqual(len(m.read_histories(['REAL'],date(2026,1,1))['REAL']),1)
        with patch('backend.main.get_kite',return_value=broker),patch.object(m.history_limit,'acquire'),patch.object(m,'today',return_value=date(2026,9,8)):
            m.refresh_history({'REAL':1},date(2026,1,1))
        self.assertEqual(broker.historical_data.call_count,2)
    def test_occ_preparation_also_covers_supertrend_history(self):
        from datetime import timedelta
        cfg={**scanner.DEFAULT_CONFIG,'strategy':'occ'}
        broker=Mock()
        broker.historical_data.return_value=[]
        current=date(2026,9,10)
        with patch('backend.main.get_kite',return_value=broker), patch.object(scanner,'get_config',side_effect=lambda:cfg), patch.object(m,'today',return_value=current), patch.object(m.history_limit,'acquire'), patch.object(m,'equity_universe',return_value={'REAL':{'instrument_token':1}}), patch.object(m,'instruments',return_value=[]):
            m.warm_full_universe()
            cfg['strategy']='supertrend'
            m.warm_full_universe()
            m.refresh_history({'REAL':1},current-timedelta(days=max(cfg['lookback_days'],365)+cfg['st_atr_length']*10))
        self.assertEqual(broker.historical_data.call_count,1)
        requested=broker.historical_data.call_args.args[1]
        self.assertLessEqual(requested,current-timedelta(days=465))

    def test_overlay_uses_ltp_not_previous_close_and_does_not_mutate(self):
        bars=[{'date':'2026-09-03T00:00:00+05:30','close':100}]
        quote=dict(timestamp='2026-09-04 11:00:00',last_trade_time='2026-09-04 10:59:59',last_price=110,volume=50,ohlc=dict(open=101,high=112,low=99,close=100))
        result=m.overlay_quote(bars,quote,datetime(2026,9,4,11,tzinfo=scanner.IST))
        self.assertEqual(result[-1]['close'],110);self.assertEqual(result[-1]['volume'],50);self.assertEqual(len(bars),1)
        self.assertEqual(m.overlay_quote(bars,quote,datetime(2026,9,6,11,tzinfo=scanner.IST)),bars)
    def test_quotes_are_batched_and_rate_limited(self):
        broker=Mock();broker.quote.return_value={}
        with patch.object(m.quote_limit,'acquire') as limiter:m.live_quotes(broker,[str(i) for i in range(1201)])
        self.assertEqual([len(c.args[0]) for c in broker.quote.call_args_list],[500,500,201]);self.assertEqual(limiter.call_count,3)
    def test_failed_refresh_cannot_mark_cache_fresh(self):
        broker=Mock();broker.historical_data.side_effect=TimeoutError()
        with patch('backend.main.get_kite',return_value=broker),patch.object(m.history_limit,'acquire'),patch.object(m,'today',return_value=date(2026,9,7)):
            with self.assertRaises(RuntimeError):m.refresh_history({'REAL':1},date(2026,1,1))
        with m.db() as c:self.assertEqual(c.execute('select count(*) from scanner_history_state').fetchone()[0],0)

    def test_invalid_quote_fields_have_specific_errors(self):
        current=datetime(2026,9,7,11,tzinfo=scanner.IST)
        good=dict(timestamp='2026-09-07 11:00:00',last_trade_time='2026-09-07 10:59:59',last_price=110,volume=0,ohlc=dict(open=100,high=112,low=99))
        for field,value in [('open',0),('high',None),('low','bad'),('close',float('nan')),('volume',-1),('volume',True)]:
            quote={**good,'ohlc':dict(good['ohlc'])}
            if field in quote['ohlc']:quote['ohlc'][field]=value
            else:quote['last_price' if field=='close' else field]=value
            with self.subTest(field=field,value=value),self.assertRaisesRegex(m.InvalidQuote,field):
                m.overlay_quote([],quote,current)
        self.assertEqual(m.overlay_quote([],good,current)[0]['volume'],0)
        with self.assertRaises(m.InvalidQuote):m.overlay_quote([],{**good,'timestamp':'broken'},current)
        with self.assertRaises(m.InvalidQuote):m.overlay_quote([],{**good,'ohlc':None},current)

    def test_scan_skips_bad_quote_and_keeps_good_signal(self):
        from contextlib import ExitStack
        bars=[dict(date=f'2026-09-0{i+1} 00:00:00+05:30',open=100,high=102,low=98,close=c,volume=100) for i,c in enumerate([99,101])]
        cfg={**scanner.DEFAULT_CONFIG,'ma_type':'SMA','length':1}
        with ExitStack() as stack:
            for target,name,value in [(scanner,'get_config',cfg),(scanner,'get_kite',Mock()),(scanner,'scan_universe',({'GOOD':'Good','BAD':'Bad'},{'GOOD':1,'BAD':2},['GOOD','BAD'])),(m,'warm_full_universe',None),(m,'refresh_history',None),(m,'read_histories',{'GOOD':bars,'BAD':bars}),(m,'live_quotes',{'NSE:GOOD':{},'NSE:BAD':{}})]:
                stack.enter_context(patch.object(target,name,return_value=value))
            overlay=stack.enter_context(patch.object(m,'overlay_quote',side_effect=[bars,m.InvalidQuote('Invalid live quote field: open')]))
            dispatch=stack.enter_context(patch('backend.autotrade.process_signals'))
            stack.enter_context(patch.object(scanner.fundamentals_executor,'submit'))
            result=scanner.run_scan()
            self.assertEqual([r['symbol'] for r in result['results']],['GOOD'])
            self.assertEqual(result['skipped_quotes'],[{'symbol':'BAD','reason':'Invalid live quote field: open'}])
            self.assertEqual(scanner.latest_run()['skipped_quotes'],result['skipped_quotes'])
            self.assertEqual([r['symbol'] for r in dispatch.call_args.args[0]],['GOOD'])
            overlay.side_effect=m.InvalidQuote('Missing quote')
            with self.assertRaisesRegex(RuntimeError,'previous results preserved'):scanner.run_scan()
            self.assertEqual(scanner.latest_run()['run_id'],result['run_id'])
