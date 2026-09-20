import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from apscheduler.schedulers.background import BackgroundScheduler
with patch.object(BackgroundScheduler, 'start'):
    from backend import scanner
from backend.rsi import daily_rsi, passes_rsi_filter
from backend.confirmed_resolution import strategy_key

class RSITests(unittest.TestCase):
    def test_wilder_reference_and_recursive_update(self):
        # Standard Wilder worked example: first RSI uses 14 changes / 15 closes.
        closes = [44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,46.08,45.89,46.03,45.61,46.28,46.28,46.00,46.03]
        self.assertAlmostEqual(daily_rsi([{'close':x} for x in closes[:15]]),70.464135,places=5)
        self.assertAlmostEqual(daily_rsi([{'close':x} for x in closes]),66.480942,places=5)
    def test_short_invalid_and_one_direction(self):
        self.assertIsNone(daily_rsi([{'close':10}]*14))
        self.assertIsNone(daily_rsi([{'close':10}]*14+[{'close':float('nan')}]))
        self.assertEqual(daily_rsi([{'close':x} for x in range(1,16)]),100)
        self.assertEqual(daily_rsi([{'close':x} for x in range(15,0,-1)]),0)
        self.assertEqual(daily_rsi([{'close':10}]*15),100)
    def test_optional_inclusive_bounds_and_unrounded_values(self):
        self.assertTrue(passes_rsi_filter(None,{}))
        self.assertFalse(passes_rsi_filter(None,{'rsi_min':0}))
        self.assertTrue(passes_rsi_filter(30,{'rsi_min':30,'rsi_max':30}))
        self.assertFalse(passes_rsi_filter(29.9999,{'rsi_min':30}))
        self.assertFalse(passes_rsi_filter(70.0001,{'rsi_max':70}))
        self.assertTrue(passes_rsi_filter(0,{'rsi_max':70}))
    def test_api_schema_preserves_bounds_and_rejects_out_of_range(self):
        from backend.main import ScannerConfigRequest
        from pydantic import ValidationError
        self.assertEqual(ScannerConfigRequest(rsi_min=0, rsi_max=100).model_dump()['rsi_min'], 0)
        for value in (-1, 101, float('nan'), float('inf')):
            with self.assertRaises(ValidationError):ScannerConfigRequest(rsi_min=value)

    def test_settings_persist_validate_clear_and_invalidate_strategy(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(scanner,'DB_PATH',Path(directory)/'test.db'):
            config={**scanner.DEFAULT_CONFIG,'universe_mode':'full_nse','rsi_min':30,'rsi_max':70}
            scanner.save_config(config)
            self.assertEqual(scanner.get_config()['rsi_min'],30)
            self.assertEqual(scanner.get_config()['rsi_max'],70)
            for changes in ({'rsi_min':71},{'rsi_min':-1},{'rsi_max':101},{'rsi_min':float('inf')},{'rsi_max':True}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):scanner.save_config({**config,**changes})
            cleared=scanner.save_config({**config,'rsi_min':None,'rsi_max':None})
            self.assertIsNone(scanner.get_config()['rsi_min'])
            self.assertNotEqual(strategy_key(config),strategy_key(cleared))
    def test_scanner_uses_daily_rsi_in_all_occ_modes_and_filters_before_dispatch(self):
        from contextlib import ExitStack
        from unittest.mock import Mock
        import pandas as pd
        from backend import market_cache
        daily = pd.read_csv(Path(__file__).resolve().parents[2]/'NSE_ANGELONE, 1D.csv').rename(columns={'Volume':'volume'})
        daily['date'] = pd.to_datetime(daily.time,unit='s',utc=True).dt.tz_convert('Asia/Kolkata')
        bars=daily.to_dict('records'); expected=daily_rsi(bars)
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(scanner,'DB_PATH',Path(directory)/'test.db'))
            for target,name,value in [(scanner,'get_kite',Mock()),(scanner,'scan_universe',({'ANGELONE':'Angel One'},{'ANGELONE':1},['ANGELONE'])),(market_cache,'warm_full_universe',None),(market_cache,'refresh_history',None),(market_cache,'read_histories',{'ANGELONE':bars}),(market_cache,'live_quotes',{'NSE:ANGELONE':{}}),(market_cache,'overlay_quote',bars),(scanner,'previous_fundamentals',{'pe_ratio':None})]:
                stack.enter_context(patch.object(target,name,return_value=value))
            stack.enter_context(patch('backend.alternate_resolution.session_calendar',return_value=tuple(daily.date.dt.date)))
            dispatch=stack.enter_context(patch('backend.autotrade.process_signals'))
            stack.enter_context(patch('backend.autotrade.get_config',return_value={'mode':'paper'}))
            stack.enter_context(patch('backend.analytics.audit_skip'))
            stack.enter_context(patch.object(scanner.fundamentals_executor,'submit'))
            for alternate, mode in [(False,'comparison'),(True,'comparison'),(True,'confirmed')]:
                cfg={**scanner.DEFAULT_CONFIG,'use_alternate_resolution':alternate,'alternate_mode':mode}
                with patch.object(scanner,'get_config',side_effect=lambda:cfg.copy()):
                    result=scanner.run_scan()['results']
                    self.assertEqual(len(result),1)
                    self.assertAlmostEqual(result[0]['rsi_14_1d'],expected)
                    self.assertEqual(result[0]['rsi_date'],'2026-09-07')
                    cfg['rsi_min']=min(100,expected+1)
                    self.assertEqual(scanner.run_scan()['results'],[])
                    if mode=='confirmed' or not alternate:self.assertEqual(dispatch.call_args.args[0],[])
