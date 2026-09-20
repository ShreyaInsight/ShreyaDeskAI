import hashlib
import unittest
from datetime import date,datetime
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch,Mock
import pandas as pd
from backend import scanner,autotrade as a,confirmed_resolution as c,confirmed_execution as execution
from backend.tests import test_autotrade_safety as safety
Clock=safety.Clock

SESSIONS=tuple(date(2026,9,d) for d in [2,3,4,7,8,9,10,11,14])
CFG={**scanner.DEFAULT_CONFIG,'use_alternate_resolution':True,'alternate_mode':'confirmed','length':1,'ma_type':'SMA','_alternate_sessions':SESSIONS}

def candles():
    return [dict(date=f'2026-09-{d:02d}T00:00:00+05:30',open=o,close=v,high=max(o,v)+1,low=min(o,v)-1,volume=100) for d,o,v in [(2,100,99),(3,99,98),(4,98,97),(7,96,97),(8,97,99),(9,99,101)]]

class ConfirmedCalculationTests(unittest.TestCase):
    def test_comparison_is_byte_identical_to_legacy_snapshot(self):
        # Frozen after comparing against the pre-addition prepare_frame body.
        actual=scanner.prepare_frame(candles(),{**CFG,'alternate_mode':'comparison'})
        self.assertEqual(hashlib.sha256(actual.to_json(date_format='iso').encode()).hexdigest(),
                         '150db7c4d58d48685de6f5b9dae997b16bd684008a75b88edb73291fb516e056')
        self.assertEqual(actual.loc[actual.buy,'date'].dt.date.tolist(),[date(2026,9,7)])
    def test_no_third_day_or_developing_candle_can_confirm(self):
        for hour in [10,15,18]:
            frame=c.prepare(candles(),CFG,datetime(2026,9,9,hour,tzinfo=a.IST))
            self.assertFalse(frame.buy.any())
            self.assertEqual(frame['date'].dt.date.max(),date(2026,9,4))
    def test_next_session_confirms_only_at_completed_block_end(self):
        frame=c.prepare(candles(),CFG,datetime(2026,9,10,10,tzinfo=a.IST))
        self.assertEqual(frame.loc[frame.buy,'date'].dt.date.tolist(),[date(2026,9,9)])
        historical=scanner.prepare_frame(candles(),{**CFG,'alternate_mode':'comparison'})
        self.assertEqual(frame.iloc[-1].close_ma,historical.iloc[-1].close_ma)
        self.assertEqual(frame.iloc[-1].open_ma,historical.iloc[-1].open_ma)
    def test_today_price_cannot_change_confirmed_ma_or_signal(self):
        now=datetime(2026,9,10,10,tzinfo=a.IST)
        extra={**candles()[-1],'date':'2026-09-10T00:00:00+05:30','close':100000}
        pd.testing.assert_frame_equal(c.prepare(candles(),CFG,now),c.prepare(candles()+[extra],CFG,now))
    def test_expired_crossovers_and_old_zyduswell_display_are_ineligible(self):
        quote={'timestamp':'2026-09-10 10:00:00','last_trade_time':'2026-09-10 09:59:59'}
        now=datetime(2026,9,10,10,tzinfo=a.IST)
        self.assertTrue(c.eligibility(date(2026,9,9),date(2026,9,9),SESSIONS,quote,now)[0])
        self.assertFalse(c.eligibility(date(2026,9,7),date(2026,9,9),SESSIONS,quote,now)[0])
        quote={k:'2026-09-11 10:00:00' for k in quote}
        self.assertFalse(c.eligibility(date(2026,9,9),date(2026,9,9),SESSIONS,quote,datetime(2026,9,11,10,tzinfo=a.IST))[0])
    def test_transition_does_not_dispatch_comparison_signal(self):
        with patch.object(a,'_record_live_order') as order,patch.object(a,'get_config') as config:
            a.process_signals([{'symbol':'ZYDUSWELL','signal_type':'BUY','comparison_only':True}],72)
        order.assert_not_called();config.assert_not_called()
    def test_interior_history_gaps_fail_closed(self):
        bars=candles()+[{**candles()[-1],'date':f'2026-09-{d}T00:00:00+05:30'} for d in [10,11,14]]
        bars.pop(4)
        with self.assertRaises(c.IncompleteHistory):c.prepare(bars,CFG,datetime(2026,9,15,10,tzinfo=a.IST))

class ConfirmedOrderTests(unittest.TestCase):
    def setUp(self):
        safety.SafetyTests.setUp(self)
        strategy_patch=patch.object(scanner,'get_config',return_value=CFG)
        strategy_patch.start();self.patches.append(strategy_patch)
    tearDown=safety.SafetyTests.tearDown
    configure=safety.SafetyTests.configure
    def submit(self):
        return a._record_live_order('ZYDUSWELL','confirmed 3D signal','BUY',10,100,confirmed_key='2026-09-04',confirmed_strategy=c.strategy_key(CFG))
    def test_concurrent_scans_reserve_one_crossover_and_one_buy(self):
        with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(lambda _:self.submit(),range(3)))
        self.assertEqual(len(self.broker.calls),1)
        conn=a.connection()
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM autotrade_confirmed_events').fetchone()[0],1)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM autotrade_buy_reservations').fetchone()[0],1);conn.close()
    def test_restart_and_unknown_broker_acceptance_never_replay(self):
        self.broker.timeout=True;self.submit()
        a.startup_safety_reset();self.configure()
        self.submit();self.assertEqual(len(self.broker.calls),1)
    def test_symbol_cap_still_blocks_without_consuming_event(self):
        self.broker.price=150;self.submit()
        self.assertEqual(self.broker.calls,[])
        conn=a.connection();self.assertEqual(conn.execute('SELECT COUNT(*) FROM autotrade_confirmed_events').fetchone()[0],0);conn.close()
    def test_confirmed_dispatch_uses_existing_limit_risk_path_and_fresh_price(self):
        Clock.value=datetime(2026,9,10,10,tzinfo=a.IST)
        signal=dict(symbol='ZYDUSWELL',signal_type='BUY',occ_mode='confirmed_3d',comparison_only=False,confirmed_eligible=True,confirmed_block_end='2026-09-09',execution_session='2026-09-10',strategy_key=c.strategy_key(CFG),trigger_price=1)
        quote={'NSE:ZYDUSWELL':dict(timestamp='2026-09-10 10:00:00',last_trade_time='2026-09-10 10:00:00',last_price=100,volume=100,ohlc=dict(open=100,high=101,low=99))}
        with patch.object(scanner,'get_config',return_value=CFG),patch.object(execution,'datetime',Clock),patch('backend.market_cache.live_quotes',return_value=quote),patch('backend.alternate_resolution.session_calendar',return_value=SESSIONS),patch.object(a,'_record_live_order') as order:
            a.process_signals([signal],72)
            self.assertEqual(order.call_args.args[:5],('ZYDUSWELL','confirmed 3D signal','BUY',10,100))
            self.assertEqual(order.call_args.kwargs['confirmed_key'],'2026-09-09')
            order.reset_mock();signal['confirmed_eligible']=False
            a.process_signals([signal],73);order.assert_not_called()

    def test_calculated_crossover_submits_only_next_session_once(self):
        from backend import market_cache
        days=['2026-08-28','2026-08-31','2026-09-01','2026-09-02','2026-09-03','2026-09-04']
        bars=[{**bar,'date':day+'T00:00:00+05:30'} for bar,day in zip(candles(),days)]
        sessions=tuple(date.fromisoformat(d) for d in days)
        cfg={**CFG,'_alternate_sessions':sessions}
        quote={'NSE:ZYDUSWELL':dict(timestamp='2026-09-07 10:00:00',last_trade_time='2026-09-07 10:00:00',last_price=100,volume=100,ohlc=dict(open=100,high=101,low=99))}
        with patch.object(scanner,'get_config',return_value=cfg),patch.object(execution,'datetime',Clock),patch.object(market_cache,'live_quotes',return_value=quote),patch('backend.alternate_resolution.session_calendar',return_value=sessions):
            for current in [datetime(2026,9,4,10,tzinfo=a.IST),datetime(2026,9,7,10,tzinfo=a.IST),datetime(2026,9,7,11,tzinfo=a.IST)]:
                Clock.value=current
                frame=c.prepare(bars,cfg,current)
                events=frame[frame.buy]
                signals=[]
                if not events.empty:
                    end=events.iloc[-1]['date'].date()
                    eligible,reason=c.eligibility(end,frame.iloc[-1]['date'].date(),sessions,quote['NSE:ZYDUSWELL'],current)
                    signals=[dict(symbol='ZYDUSWELL',signal_type='BUY',occ_mode='confirmed_3d',confirmed_eligible=eligible,confirmed_reason=reason,confirmed_block_end=end.isoformat(),execution_session=current.date().isoformat(),strategy_key=c.strategy_key(cfg))]
                a.process_signals(signals,72)
                self.assertEqual(len(self.broker.calls),0 if current.day==4 else 1)
            self.assertEqual(self.broker.calls[0]['order_type'],'LIMIT')
            self.assertEqual(self.broker.calls[0]['price'],100)

    def test_paper_crossover_is_persistent_and_never_repeats(self):
        self.configure(mode='paper')
        signal=dict(symbol='ZYDUSWELL',signal_type='BUY',trigger_price=100,_confirmed_key='2026-09-04')
        a.process_paper_signals([signal],72);a.process_paper_signals([signal],73)
        conn=a.connection()
        self.assertEqual(conn.execute("SELECT count(*) FROM autotrade_positions WHERE mode='paper'").fetchone()[0],1)
        self.assertEqual(conn.execute('SELECT count(*) FROM autotrade_confirmed_events').fetchone()[0],1)
        self.assertEqual(conn.execute('SELECT count(*) FROM autotrade_buy_reservations').fetchone()[0],0);conn.close()
        self.assertEqual(self.broker.calls,[])

    def test_mode_change_before_submission_blocks_order(self):
        with patch.object(scanner,'get_config',return_value={**CFG,'alternate_mode':'comparison'}):
            a._record_live_order('ZYDUSWELL','confirmed 3D signal','BUY',10,100,confirmed_key='2026-09-04',confirmed_strategy=c.strategy_key(CFG))
        self.assertEqual(self.broker.calls,[])
