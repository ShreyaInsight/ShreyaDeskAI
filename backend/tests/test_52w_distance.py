import unittest
from unittest.mock import patch
from backend import scanner
from backend.confirmed_resolution import strategy_key

class DistanceTests(unittest.TestCase):
    def test_inclusive_optional_bounds_and_missing_values(self):
        for low, high, value, expected in [(10,20,10,True),(10,20,20,True),(10,20,9.9,False),(10,20,20.1,False),(10,None,30,True),(None,20,0,True),(0,None,0,True),(10,None,None,False),(None,None,None,True)]:
            with self.subTest(low=low,high=high,value=value):
                self.assertEqual(scanner.passes_fundamental_filters({'week_52_high_distance_pct':value},{'min_52w_high_distance_pct':low,'max_52w_high_distance_pct':high}),expected)
    def test_invalid_ranges_rejected_before_database_access(self):
        for config in [{'min_52w_high_distance_pct':-1},{'min_52w_high_distance_pct':101},{'min_52w_high_distance_pct':float('nan')},{'min_52w_high_distance_pct':30,'max_52w_high_distance_pct':20}]:
            with patch.object(scanner,'db') as db:
                with self.assertRaises(ValueError): scanner.save_config(config)
                db.assert_not_called()
    def test_disabled_filter_preserves_strategy_identity(self):
        self.assertEqual(strategy_key({}),strategy_key({'min_52w_high_distance_pct':None}))
        self.assertNotEqual(strategy_key({}),strategy_key({'min_52w_high_distance_pct':0}))
