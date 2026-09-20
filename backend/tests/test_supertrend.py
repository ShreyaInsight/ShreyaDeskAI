import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, Mock
from backend import scanner as s, supertrend as st, supertrend_scan as scan, strategies


def bars():
    # Seven real daily sessions, not fabricated weekend candles.
    days=['2026-08-24','2026-08-25','2026-08-26','2026-08-27','2026-08-28','2026-08-31','2026-09-01']
    return [dict(date=day+'T00:00:00+05:30',open=c,high=c+1,low=c-1,close=c,volume=100) for day,c in zip(days,[10,10,10,14,15,10,9])]


class SupertrendTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.p=patch.object(s,'DB_PATH',Path(self.temp.name)/'scanner.db');self.p.start()
    def tearDown(self):self.p.stop();self.temp.cleanup()
    def test_wilder_atr_locked_bands_and_both_flips(self):
        result=st.calculate(bars(),3,1)
        self.assertEqual([r['direction'] for r in result],[1,1,1,-1,-1,1,1])
        self.assertEqual([i for i,r in enumerate(result) if r['bullFlip']],[3])
        self.assertEqual([i for i,r in enumerate(result) if r['bearFlip']],[5])
        self.assertIsNone(result[1]['supertrend_value'])
        self.assertEqual(result[2]['supertrend_value'],12)
        self.assertEqual(result[3]['supertrend_value'],11)
        self.assertAlmostEqual(result[4]['supertrend_value'],37/3)
        self.assertAlmostEqual(result[5]['supertrend_value'],124/9)
    def test_equal_band_does_not_flip_and_partial_candle_is_ignored(self):
        source=bars()[:3]+[dict(date='2026-09-09T00:00:00+05:30',open=12,high=13,low=11,close=12,volume=100)]
        self.assertFalse(st.calculate(source,3,1)[-1]['bullFlip'])
        before=st.finalized(source,datetime(2026,9,9,15,29,tzinfo=s.IST))
        self.assertEqual(len(before),3)
        self.assertEqual(len(st.finalized(source,datetime(2026,9,9,15,30,tzinfo=s.IST))),4)
    def test_caches_identical_completed_candles_and_ignores_occ_settings(self):
        cfg={**s.DEFAULT_CONFIG,'strategy':'supertrend','st_atr_length':3,'st_factor':1}
        first=st.cached_bars('APP',bars(),cfg)
        with patch.object(st,'calculate',side_effect=AssertionError('must use cache')):
            self.assertEqual(first,st.cached_bars('APP',bars(),{**cfg,'length':150,'sl_pct':50,'use_alternate_resolution':True}))
        self.assertEqual(scan.strategy_key(cfg),scan.strategy_key({**cfg,'length':150}))
        for key in scan.SHARED_KEYS:
            self.assertNotEqual(scan.strategy_key(cfg),scan.strategy_key({**cfg,key:123}))
    def test_strategy_results_stay_separate_when_old_scan_finishes_late(self):
        occ={**s.DEFAULT_CONFIG,'watchlist':['APP']}
        sup={**occ,'strategy':'supertrend','supertrend_filter_version':1}
        conn=s.db()
        for cfg,symbol in [(occ,'OCC'),(sup,'ST'),(occ,'LATE_OCC')]:
            run=conn.execute('INSERT INTO scanner_runs(ran_at,payload) VALUES (?,?)',('2026-09-09',json.dumps({'config':cfg}))).lastrowid
            row={'symbol':symbol,'strategy':cfg['strategy'],'signal_type':'BUY','trigger_date':'2026-09-08'}
            conn.execute('INSERT INTO scanner_signals(symbol,signal_type,trigger_date,payload,run_id) VALUES (?,?,?,?,?)',(symbol,'BUY','2026-09-08',json.dumps(row),run))
        conn.execute('INSERT INTO scanner_config VALUES(1,?)',(json.dumps(sup),));conn.commit();conn.close()
        self.assertEqual([r['symbol'] for r in strategies.results_snapshot()['results']],['ST'])
        conn=s.db();conn.execute('UPDATE scanner_config SET payload=?',(json.dumps(occ),));conn.commit();conn.close()
        self.assertEqual([r['symbol'] for r in strategies.results_snapshot()['results']],['LATE_OCC'])
    def test_shared_filters_run_before_persistence_and_execution(self):
        from contextlib import ExitStack
        cfg={**s.DEFAULT_CONFIG,'strategy':'supertrend','st_atr_length':3,'st_factor':1}
        def run(overrides, permit=True):
            with ExitStack() as stack:
                for obj,method,value in [(s,'get_config',{**cfg,**overrides}),(s,'get_kite',Mock()),
                    (s,'scan_universe',({'APP':'App'},{'APP':1},['APP'])),
                    (scan.cache,'warm_full_universe',None),(scan.cache,'refresh_history',None),
                    (scan,'refresh_close',None),(scan.cache,'read_histories',{'APP':bars()}),
                    (scan.cache,'live_quotes',{'NSE:APP':{'last_price':10}}),
                    (s,'previous_fundamentals',{'pe_ratio':20}),
                    (s.fundamentals_executor,'submit',None),(scan,'eligible',(False,'test'))]:
                    stack.enter_context(patch.object(obj,method,return_value=value))
                stack.enter_context(patch.object(s,'enrich_results_fundamentals',side_effect=lambda rows:[r.update(pe_ratio=20) for r in rows]))
                execute=stack.enter_context(patch.object(scan,'process'))
                result=scan.run(execute=permit)
                if permit:self.assertEqual(execute.call_args.args[0],result['results'])
                else:execute.assert_not_called()
                return result['results']
        result=run({})
        self.assertEqual(len(run({},permit=False)),1)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['stop_loss'],9.8)
        self.assertEqual(result[0]['take_profit'],10.5)
        for bounds in [{'volume_min':101},{'volume_max':99},{'volume_above_30d_average':True},
                       {'rsi_min':0},{'pe_filter_operator':'above','pe_filter_value':21},
                       {'min_52w_high_distance_pct':90},{'max_52w_high_distance_pct':1},
                       {'max_signal_age_days':1}]:
            with self.subTest(bounds=bounds):self.assertEqual(run(bounds),[])
        self.assertEqual(len(run({'volume_min':100,'volume_max':100,'pe_filter_operator':'below','pe_filter_value':21})),1)
    def test_adx_does_not_mutate_chart_or_block_bear_flips(self):
        source=st.calculate(bars(),3,1)
        original=json.dumps(source)
        with patch.object(s,'adx',return_value=s.pd.Series([0]*len(source))):
            flips=scan.filtered_flips(source,{**s.DEFAULT_CONFIG,'adx_enabled':True})
        self.assertTrue(flips)
        self.assertTrue(all(b['bearFlip'] for b in flips))
        self.assertEqual(original,json.dumps(source))
    def test_old_unfiltered_runs_and_changed_filters_are_not_reused(self):
        cfg={**s.DEFAULT_CONFIG,'strategy':'supertrend'}
        self.assertFalse(strategies.matches(cfg,cfg))
        self.assertTrue(strategies.matches({**cfg,'supertrend_filter_version':1},cfg))
        self.assertFalse(strategies.matches({**cfg,'supertrend_filter_version':1},{**cfg,'volume_min':100}))
    def test_after_close_fetch_is_cached_and_handles_mixed_dates(self):
        broker=Mock();broker.historical_data.return_value=[dict(date='2026-09-09T00:00:00+05:30',open=10,high=11,low=9,close=10,volume=100)]
        with patch.object(s,'get_kite',return_value=broker),patch.object(scan.cache.history_limit,'acquire'):
            st.refresh_close({'APP':1},datetime(2026,9,9,15,29,tzinfo=s.IST))
            broker.historical_data.assert_not_called()
            st.refresh_close({'APP':1},datetime(2026,9,9,15,31,tzinfo=s.IST))
            st.refresh_close({'APP':1},datetime(2026,9,9,16,tzinfo=s.IST))
            self.assertEqual(broker.historical_data.call_count,1)
        mixed=bars()+[dict(date='2026-09-09',open=10,high=11,low=9,close=10,volume=100)]
        self.assertEqual(len(st.finalized(mixed,datetime(2026,9,9,16,tzinfo=s.IST))),8)

    def test_parameter_validation(self):
        for length,factor in [(0,3),(2.5,3),(10,0),(10,float('nan'))]:
            with self.assertRaises(ValueError):st.parameters({'st_atr_length':length,'st_factor':factor})
    def test_eligibility_only_next_session(self):
        q={'timestamp':'2026-09-09 10:00:00','last_trade_time':'2026-09-09 10:00:00'}
        with patch.object(scan.cache,'read_histories',return_value={'NIFTY 50':[{'date':'2026-09-08T00:00:00+05:30'}]}):
            current=datetime(2026,9,9,10,tzinfo=s.IST)
            self.assertTrue(scan.eligible('2026-09-08','2026-09-08',q,current)[0])
            self.assertFalse(scan.eligible('2026-09-07','2026-09-08',q,current)[0])
            self.assertFalse(scan.eligible('2026-09-08','2026-09-08',{},current)[0])

from backend.tests import test_autotrade_safety as safety
from backend import autotrade as a
from concurrent.futures import ThreadPoolExecutor


class SupertrendExecutionTests(unittest.TestCase):
    configure=safety.SafetyTests.configure
    tearDown=safety.SafetyTests.tearDown
    def setUp(self):
        safety.SafetyTests.setUp(self)
        self.cfg={**s.DEFAULT_CONFIG,'strategy':'supertrend'}
        for p in [patch.object(s,'get_config',return_value=self.cfg),patch.object(s,'DB_PATH',a.DB_PATH)]:p.start();self.patches.append(p)
    def submit(self):
        return a._record_live_order('STAPP','Supertrend signal','BUY',10,100,confirmed_key='st:2026-09-04',confirmed_strategy=scan.strategy_key(self.cfg))
    def test_concurrent_and_restart_duplicate_protection(self):
        with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(lambda _:self.submit(),range(3)))
        self.assertEqual(len(self.broker.calls),1)
        self.assertEqual(self.broker.calls[0]['order_type'],'LIMIT')
        self.assertTrue(self.broker.calls[0]['tag'].startswith('st'))
        a.startup_safety_reset();self.configure();self.submit()
        self.assertEqual(len(self.broker.calls),1)
    def test_symbol_cap_and_strategy_switch_block_submission(self):
        self.broker.price=150;self.submit();self.assertEqual(self.broker.calls,[])
        self.broker.price=100;self.cfg['strategy']='occ';self.submit();self.assertEqual(self.broker.calls,[])
    def test_dispatch_uses_fresh_price_and_never_opens_short(self):
        current=safety.Clock.now(a.IST)
        row=dict(symbol='STAPP',strategy='supertrend',signal_type='BUY',strategy_key=scan.strategy_key(self.cfg),confirmed_eligible=True,execution_session=current.date().isoformat(),trigger_date='2026-09-04',indicator_date='2026-09-04',trigger_price=1)
        quote={'timestamp':current.isoformat(),'last_trade_time':current.isoformat(),'last_price':100,'ohlc':{'open':100,'high':101,'low':99},'volume':100}
        with patch.object(scan,'datetime',safety.Clock),patch.object(scan,'eligible',return_value=(True,'ok')),patch.object(scan.cache,'live_quotes',return_value={'NSE:STAPP':quote}),patch.object(a,'_market_is_open',return_value=True):
            scan.process([row],1)
            scan.process([{**row,'signal_type':'EXIT'}],1)
        self.assertEqual(len(self.broker.calls),1)
        self.assertEqual(self.broker.calls[0]['price'],100)
        self.assertEqual(self.broker.calls[0]['quantity'],10)
    def test_supertrend_exit_cannot_close_occ_position(self):
        pid=safety.SafetyTests.position(self,symbol='STAPP')
        a._record_live_order('STAPP','Supertrend exit','SELL',10,100,position_id=pid,confirmed_key='st:2026-09-04',confirmed_strategy=scan.strategy_key(self.cfg))
        self.assertEqual(self.broker.calls,[])
    def test_owned_sell_keeps_cap_exemption_and_cancels_gtt(self):
        conn=s.db()
        run_id=conn.execute('INSERT INTO scanner_runs(ran_at,payload) VALUES (?,?)',(a.now(),json.dumps({'config':self.cfg}))).lastrowid
        conn.commit();conn.close()
        pid=safety.SafetyTests.position(self,symbol='STAPP',quantity=1)
        conn=a.connection();conn.execute('UPDATE autotrade_positions SET scan_run_id=? WHERE id=?',(run_id,pid));conn.commit();conn.close()
        self.broker.price=1500
        with patch('backend.gtt.cancel_before_exit') as cancel:
            a._record_live_order('STAPP','Supertrend exit','SELL',1,1500,position_id=pid,confirmed_key='st:2026-09-04',confirmed_strategy=scan.strategy_key(self.cfg))
            cancel.assert_called_once()
        self.assertEqual(self.broker.calls[0]['transaction_type'],'SELL')
        self.assertEqual(self.broker.calls[0]['price'],1500)
