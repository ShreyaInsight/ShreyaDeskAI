import unittest
from unittest.mock import Mock, patch
from backend.holdings import split_inventory, occ_signal
from backend import scanner
import pandas as pd


class HoldingsTests(unittest.TestCase):
    def holding(self, **kwargs):
        return dict(tradingsymbol='TEST', exchange='NSE', quantity=10, t1_quantity=0, used_quantity=0, average_price=100, **kwargs)
    def test_shared_symbol_has_no_quantity_overlap(self):
        app = [dict(id=1, symbol='TEST', quantity=3, entry_price=80)]
        result = split_inventory([self.holding()], [], app, {'NSE:TEST':{'last_price':120}})
        self.assertEqual(result['algo'][0]['quantity'],3)
        self.assertEqual(result['algo'][0]['pnl'],120)
        self.assertEqual(result['personal'][0]['quantity'],7)
        self.assertIn('Estimated',result['personal'][0]['note'])
    def test_today_buy_and_used_holdings_not_double_counted(self):
        h=self.holding(); h['used_quantity']=4
        p=dict(tradingsymbol='TEST', exchange='NSE', product='CNC', quantity=-4)
        r=split_inventory([h],[p],[],{})
        self.assertEqual(r['personal'][0]['quantity'],6)
        p.update(quantity=2,average_price=110)
        self.assertEqual(split_inventory([h],[p],[],{})['personal'][0]['quantity'],8)
    def test_pending_settlement_and_missing_quotes(self):
        h=self.holding();h.update(quantity=0,t1_quantity=5)
        r=split_inventory([h],[],[],{})['personal'][0]
        self.assertEqual(r['quantity'],5); self.assertIsNone(r['pnl'])
    def test_mismatch_is_visible_no_negative_personal_inventory(self):
        app=[dict(id=1,symbol='TEST',quantity=20,entry_price=80)]
        r=split_inventory([self.holding()],[],app,{})
        self.assertTrue(r['warnings']);self.assertEqual(r['personal'],[])
    def test_empty(self):
        self.assertEqual(split_inventory([],[],[],{}),dict(algo=[],personal=[],warnings=[]))
    def test_signal_uses_scanner_frame_without_scan_or_execution(self):
        conn=Mock();conn.__enter__=Mock(return_value=conn);conn.__exit__=Mock(return_value=False)
        conn.execute.return_value.fetchone.return_value=None
        frame=pd.DataFrame([dict(buy=True,exit=False,date=pd.Timestamp('2026-09-01')),dict(buy=False,exit=True,date=pd.Timestamp('2026-09-03'))])
        kite=Mock();kite.historical_data.return_value=[{'fake':'candles'}]
        with patch('backend.holdings.sqlite3.connect',return_value=conn), patch('backend.main.instrument_tokens',return_value={'TEST':1}), patch.object(scanner,'prepare_frame',return_value=frame) as prepare, patch.object(scanner,'run_scan') as scan:
            r=occ_signal(kite,'TEST','NSE')
            self.assertEqual(r,dict(signal_type='EXIT',trigger_date='2026-09-03'))
            prepare.assert_called_once_with(kite.historical_data.return_value,scanner.DEFAULT_CONFIG)
            scan.assert_not_called()
    def test_supertrend_dispatch_uses_completed_bars_and_saved_parameters(self):
        import json
        from backend import supertrend, supertrend_scan
        cfg={**scanner.DEFAULT_CONFIG,'strategy':'supertrend','st_atr_length':7,'st_factor':2.5}
        conn=Mock();conn.__enter__=Mock(return_value=conn);conn.__exit__=Mock(return_value=False)
        conn.execute.return_value.fetchone.return_value=(json.dumps(cfg),)
        kite=Mock();kite.historical_data.return_value=[{'raw':'history'}]
        completed=[{'completed':'bar'}]
        event={'bullFlip':True,'bearFlip':False,'date':'2026-09-09'}
        with patch('backend.holdings.sqlite3.connect',return_value=conn), patch('backend.main.instrument_tokens',return_value={'TEST':1}), patch.object(supertrend,'finalized',return_value=completed) as finalize, patch.object(supertrend,'calculate',return_value=[event]) as calculate, patch.object(supertrend_scan,'filtered_flips',return_value=[event]) as flips, patch.object(scanner,'prepare_frame') as occ, patch.object(scanner,'run_scan') as scan:
            result=occ_signal(kite,'TEST','NSE')
            self.assertEqual(result,dict(strategy='supertrend',signal_type='BUY',trigger_date='2026-09-09'))
            finalize.assert_called_once_with(kite.historical_data.return_value)
            calculate.assert_called_once_with(completed,7,2.5)
            flips.assert_called_once_with([event],cfg)
            occ.assert_not_called();scan.assert_not_called()
