import unittest
from unittest.mock import Mock, patch
from backend import scanner

class ScannerProgressTests(unittest.TestCase):
    def test_failed_cache_preparation_preserves_previous_results(self):
        with patch.object(scanner,'get_config',return_value=scanner.DEFAULT_CONFIG.copy()),patch.object(scanner,'get_kite'),patch.object(scanner,'scan_universe',return_value=({'A':'A'},{'A':1},['A'])),patch('backend.market_cache.warm_full_universe',side_effect=RuntimeError('cache failed')),patch.object(scanner,'db') as db:
            with self.assertRaisesRegex(RuntimeError,'cache failed'):scanner.run_scan(progress=Mock())
            db.assert_not_called()
