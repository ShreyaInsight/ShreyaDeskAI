import json
import unittest
from datetime import timedelta
from unittest.mock import patch
from backend import risk, market_cache as cache, scanner, broker_identity as identity
from backend.tests import test_autotrade_safety as safety
Clock=safety.Clock

class CacheRecoveryTests(unittest.TestCase):
 def setUp(self):
  self.fixture=safety.SafetyTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
  self.p=patch.object(scanner,'DB_PATH',risk.trade.DB_PATH);self.p.start();self.addCleanup(self.p.stop)
  self.fixture.position('APP',quantity=2,entry=100)
  self.baseline=risk.load()['cumulative_loss_baseline']
  self.day=Clock.value.date()
  risk.check(self.fixture.broker,block_buy=False)
  Clock.value+=timedelta(days=1)
 def candle(self, fetched=None):
  with cache.db() as c:
   c.execute('INSERT OR REPLACE INTO scanner_daily_candles VALUES (?,?,?)',('APP',str(self.day),json.dumps(dict(date=str(self.day),open=100,high=105,low=90,close=95,volume=100))))
   c.execute('INSERT OR REPLACE INTO scanner_history_state VALUES (?,?,?,?)',('APP',str(fetched or Clock.value.date()),str(self.day),1))
 def row(self):
  c=risk.connection();row=dict(c.execute('SELECT * FROM autotrade_app_pnl_days WHERE baseline=? AND day=?',(self.baseline,str(self.day))).fetchone());c.close();return row
 def test_finalized_cached_close_recovers_intraday_only_day(self):
  self.candle();risk.check(self.fixture.broker,block_buy=False)
  row=self.row();self.assertEqual(row['source'],'cache_recovery');self.assertEqual(row['finalized'],1)
  trades=json.loads(row['trades']);self.assertEqual(trades[-1]['pnl'],-10)
  self.assertIsNone(risk.load()['history_error']);self.assertEqual(risk.load()['cumulative_loss_baseline'],self.baseline)
 def test_intraday_cache_does_not_count_as_final(self):
  self.candle(fetched=self.day);risk.check(self.fixture.broker,block_buy=False)
  self.assertEqual(self.row()['finalized'],0);self.assertIn(str(self.day),risk.load()['history_error'])
 def test_missing_candle_leaves_gap_blocked(self):
  risk.check(self.fixture.broker,block_buy=False)
  self.assertEqual(self.row()['finalized'],0);self.assertIn(str(self.day),risk.load()['history_error'])
 def test_missing_other_symbol_prevents_partial_day_recovery(self):
  Clock.value-=timedelta(days=1);self.fixture.position('OTHER');Clock.value+=timedelta(days=1)
  self.candle();risk.check(self.fixture.broker,block_buy=False)
  self.assertEqual(self.row()['finalized'],0)
 def test_recovery_does_not_enable_or_clear_latches(self):
  self.candle();state=risk.load();state['manual_latched']=True;risk.save(state);risk.trade.pause()
  risk.check(self.fixture.broker,block_buy=False)
  self.assertTrue(risk.load()['manual_latched']);self.assertTrue(risk.trade.get_config()['paused'])
 def test_transient_identity_warning_clears_after_valid_check_only(self):
  with patch.object(self.fixture.broker,'profile',side_effect=TimeoutError()):
   with self.assertRaises(identity.AccountIdentityError):identity.verify(self.fixture.broker)
  self.assertIsNotNone(identity.state()['error'])
  identity.verify(self.fixture.broker)
  self.assertIsNone(identity.state()['error']);self.assertTrue(risk.trade.get_config()['paused'])
 def test_account_switch_warning_is_not_cleared_by_successful_profile(self):
  identity.alert('OAuth account change awaits confirmation')
  identity.verify(self.fixture.broker)
  self.assertEqual(identity.state()['error'],'OAuth account change awaits confirmation')

 def test_recovered_loss_breach_latches_even_if_current_price_recovered(self):
  self.candle();self.fixture.configure(max_cumulative_loss=5)
  risk.check(self.fixture.broker,block_buy=False)
  self.assertTrue(risk.load()['loss_latched']);self.assertTrue(risk.trade.get_config()['paused'])

 def test_recovered_loss_evidence_persists_until_manual_baseline_reset(self):
  self.candle();risk.check(self.fixture.broker,block_buy=False)
  self.assertEqual(risk.load()['recovered_min_pnl'],-10)
  self.fixture.configure(max_cumulative_loss=5)
  risk.check(self.fixture.broker,block_buy=False)
  self.assertTrue(risk.load()['loss_latched'])
  risk.clear(risk.CLEAR_CONFIRMATION)
  self.assertIsNone(risk.load().get('recovered_min_pnl'))
 def test_cached_prices_do_not_bypass_unresolved_orders(self):
  self.candle()
  with patch.object(risk.trade,'unresolved_orders',return_value=[{'id':123}]):
   risk.check(self.fixture.broker,block_buy=False)
  self.assertEqual(self.row()['finalized'],0)
  self.assertIn(str(self.day),risk.load()['history_error'])
