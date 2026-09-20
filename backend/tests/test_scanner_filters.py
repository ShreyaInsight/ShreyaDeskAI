import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from backend import scanner


class ScannerFilterTests(unittest.TestCase):
    def test_full_nse_ignores_index_and_watchlist(self):
        kite=Mock();kite.instruments.return_value=[{'tradingsymbol':'AAA','instrument_type':'EQ','instrument_token':1,'name':'A'}, {'tradingsymbol':'INDEX','instrument_type':'INDEX','instrument_token':2}, {'tradingsymbol':'FUT','instrument_type':'FUT','instrument_token':3}]
        with patch.object(scanner,'index_symbols') as index, patch('backend.market_cache.equity_universe',return_value={'AAA':kite.instruments.return_value[0]}):
            names,tokens,watchlist=scanner.scan_universe({**scanner.DEFAULT_CONFIG,'universe_mode':'full_nse','watchlist':['OLD']},kite)
        self.assertEqual(watchlist,['AAA']);self.assertEqual(tokens,{'AAA':1});index.assert_not_called()
    def test_index_mode_preserves_existing_behavior(self):
        with patch.object(scanner,'index_symbols',return_value={'AAA':'A'}),patch.object(scanner,'instrument_tokens',return_value={'AAA':1}):
            self.assertEqual(scanner.scan_universe(scanner.DEFAULT_CONFIG,Mock())[2],['AAA'])
    def test_optional_bounds_and_inclusive_edges(self):
        m={'current_volume':100,'previous_30d_avg_volume':90}
        for cfg in ({},{'volume_min':100},{'volume_max':100},{'volume_min':100,'volume_max':100}):self.assertTrue(scanner.passes_volume_filters(m,cfg))
        for cfg in ({'volume_min':101},{'volume_max':99}):self.assertFalse(scanner.passes_volume_filters(m,cfg))
    def test_average_excludes_current_and_requires_thirty_prior_sessions(self):
        candles=[{'volume':10}]*30+[{'volume':1000}]
        m=scanner.volume_metrics(candles)
        self.assertEqual(m,{'current_volume':1000,'previous_30d_avg_volume':10})
        self.assertTrue(scanner.passes_volume_filters(m,{'volume_above_30d_average':True}))
        self.assertFalse(scanner.passes_volume_filters(scanner.volume_metrics(candles[1:]),{'volume_above_30d_average':True}))
    def test_equality_does_not_pass_relative_rule(self):
        self.assertFalse(scanner.passes_volume_filters(scanner.volume_metrics([{'volume':10}]*31),{'volume_above_30d_average':True}))
    def test_missing_or_invalid_volume_fails_enabled_filters_only(self):
        for value in (None,float('nan'),-1):
            m=scanner.volume_metrics([{'volume':value}])
            self.assertTrue(scanner.passes_volume_filters(m,{}))
            self.assertFalse(scanner.passes_volume_filters(m,{'volume_min':0}))
    def test_config_roundtrip_and_invalid_range(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(scanner,'DB_PATH',Path(directory)/'test.db'),patch.object(scanner,'index_symbols',return_value={'AAA':'A'}) as index:
            cfg=scanner.save_config({**scanner.DEFAULT_CONFIG,'index_name':'NIFTY500'})
            full=scanner.save_config({**cfg,'universe_mode':'full_nse','volume_min':100,'volume_max':1000})
            self.assertEqual(index.call_count,1)
            self.assertEqual(scanner.get_config()['index_name'],'NIFTY500')
            restored=scanner.save_config({**full,'universe_mode':'index'})
            self.assertEqual(restored['index_name'],'NIFTY500');self.assertEqual(restored['volume_min'],100)
            with self.assertRaises(ValueError):scanner.save_config({**restored,'volume_min':1001})
