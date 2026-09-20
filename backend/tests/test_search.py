import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from backend import scanner as s, search, market_cache

class SearchTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=patch.object(s,'DB_PATH',Path(self.tmp.name)/'db');self.p.start()
  c=s.db();c.execute('INSERT OR REPLACE INTO scanner_config(id,payload) VALUES (1,?)',(json.dumps({**s.DEFAULT_CONFIG,'strategy':'supertrend','use_alternate_resolution':False}),));c.commit();c.close()
 def tearDown(self):self.p.stop();self.tmp.cleanup()
 def test_weights_are_isolated_and_missing_values_have_coverage(self):
  before=s.DB_PATH.read_bytes()
  cfg=json.loads(json.dumps(search.DEFAULT_WEIGHTS));cfg['long']['ROE']=[100,0,20]
  search.weights(cfg)
  result=search.score({'ROE':10},cfg)['long']
  self.assertEqual(result['value'],50);self.assertLess(result['coverage'],100)
  c=s.db();stored=json.loads(c.execute('select payload from scanner_config where id=1').fetchone()[0]);c.close()
  self.assertEqual(stored['strategy'],'supertrend')
  cfg['short']['RSI']=[10,1,1]
  with self.assertRaises(ValueError):search.weights(cfg)
 def test_daily_provider_cache_and_field_statuses(self):
  with patch.object(s,'screener_fundamentals',return_value={'_status':'SUCCESS','_ratios':{'Stock P/E':20,'ROE':15}}) as fetch:
   first=search.fundamentals('TEST');search.fundamentals('TEST')
  fetch.assert_called_once_with('TEST',detailed=True)
  self.assertEqual(first['fields']['Stock P/E']['value'],20)
  self.assertEqual(first['fields']['PEG Ratio']['status'],'UNAVAILABLE')
 def test_search_reuses_math_without_running_scanner_or_orders(self):
  from backend.tests.test_supertrend import bars
  with patch.object(search,'symbols',return_value=[{'symbol':'TEST'}]),patch.object(market_cache,'read_histories',return_value={'TEST':bars()}),patch.object(s,'screener_fundamentals',return_value={'_status':'SUCCESS','_ratios':{'ROE':20}}),patch.object(s,'run_scan',side_effect=AssertionError('No scans')),patch('backend.autotrade.process_signals',side_effect=AssertionError('No orders')):
   result=search.analyse('TEST')
  self.assertEqual(result['symbol'],'TEST');self.assertTrue(result['bars'])
  self.assertEqual(result['fields']['RSI']['source'],'cached daily candles')
  json.dumps(result,allow_nan=False)
 def test_default_provider_behavior_unchanged(self):
  response=Mock();response.text='<ul id="top-ratios"><li><span class="name">Stock P/E</span><span class="value"><span class="number">12</span></span></li></ul>'
  with patch.object(s,'fundamentals_cache',{}),patch.object(s.requests,'get',return_value=response):
   self.assertEqual(s.screener_fundamentals('TEST'),{'eps':None,'pe_ratio':12})
