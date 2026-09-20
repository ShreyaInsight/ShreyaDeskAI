import copy
import unittest
from unittest.mock import patch
from backend import autotrade as a, gtt, risk, broker_identity as identity
from backend.tests import test_gtt as fixtures

class IdentityTests(unittest.TestCase):
 def setUp(self):
  self.f=fixtures.GTTTests();self.f.setUp();self.addCleanup(self.f.tearDown)
 def test_new_buy_order_position_and_gtt_are_bound(self):
  oid,pid=self.f.buy()
  c=a.connection();order=dict(c.execute('SELECT * FROM autotrade_orders WHERE id=?',(oid,)).fetchone());c.close()
  self.assertEqual(order['account_id'],'TEST');self.assertEqual(gtt.position(pid)['account_id'],'TEST');self.assertEqual(gtt.get(pid)['account_id'],'TEST')
 def test_wrong_account_cannot_create_protection(self):
  self.f.configure(exit_rule='scanner_exit');_,pid=self.f.buy();self.f.configure(exit_rule='whichever_first')
  with patch.object(self.f.broker,'profile',return_value={'user_id':'OTHER'}),patch.object(self.f.broker,'place_gtt') as place:
   with self.assertRaises(identity.AccountIdentityError):gtt.ensure(pid,self.f.broker)
   place.assert_not_called()
  self.assertTrue(a.get_config()['paused']);self.assertIn('mismatch',identity.public()['error'])
 def test_wrong_account_cannot_read_order_history_or_account_fills(self):
  oid,pid=self.f.buy();before=gtt.position(pid)
  with patch.object(self.f.broker,'profile',return_value={'user_id':'OTHER'}),patch.object(self.f.broker,'order_history') as history:
   a.reconcile_order(oid);history.assert_not_called()
  self.assertEqual(gtt.position(pid),before)
 def test_wrong_account_blocks_all_gtt_entrypoints_before_remote_calls(self):
  _,pid=self.f.buy()
  with patch.object(self.f.broker,'profile',return_value={'user_id':'OTHER'}),patch.object(self.f.broker,'get_gtts') as book,patch.object(self.f.broker,'get_gtt') as read,patch.object(self.f.broker,'delete_gtt') as delete,patch.object(self.f.broker,'modify_gtt',create=True) as modify:
   gtt.reconcile_all();book.assert_not_called()
   with self.assertRaises(identity.AccountIdentityError):gtt.cancel_before_exit(pid,'manual close',self.f.broker)
   with self.assertRaises(identity.AccountIdentityError):gtt.resolve(pid,None,'RECONCILE GTT')
   result=gtt.apply_existing('APPLY LIVE SL TP');self.assertEqual(result['results'][0]['status'],'FAILED')
   read.assert_not_called();delete.assert_not_called();modify.assert_not_called()
 def test_stored_record_owner_mismatch_blocks_even_matching_workspace(self):
  oid,pid=self.f.buy();c=a.connection();c.execute("UPDATE autotrade_gtts SET account_id='OTHER' WHERE position_id=?",(pid,));c.commit();c.close()
  with patch.object(self.f.broker,'get_gtts') as book:
   gtt.reconcile_all();book.assert_not_called()
 def test_legacy_is_not_bound_automatically_and_requires_exact_confirmation(self):
  oid,pid=self.f.buy();c=a.connection()
  for table in ['autotrade_orders','autotrade_positions','autotrade_gtts']:c.execute(f'UPDATE {table} SET account_id=NULL')
  c.commit();c.close()
  with self.assertRaises(identity.AccountIdentityError):gtt.ensure(pid,self.f.broker)
  self.assertIsNone(gtt.position(pid)['account_id'])
  with self.assertRaises(ValueError):identity.bind_legacy('TEST','yes')
  with self.assertRaises(ValueError):identity.bind_legacy('OTHER','BIND LEGACY RECORDS TO OTHER')
  identity.bind_legacy('TEST','BIND LEGACY RECORDS TO TEST')
  self.assertEqual(gtt.position(pid)['account_id'],'TEST');self.assertEqual(identity.legacy_counts()['total'],0)
  self.assertTrue(a.get_config()['paused'])
 def test_account_switch_blocked_with_exposure(self):
  self.f.buy()
  self.assertFalse(identity.accept_connection({'account_id':'OTHER'}))
  with self.assertRaises(ValueError):identity.finish_switch({'account_id':'OTHER'},'SWITCH BROKER ACCOUNT TO OTHER')
  self.assertEqual(identity.owner(),'TEST')
 def test_intentional_switch_preserves_history_and_requires_new_baseline(self):
  _,pid=self.f.buy();self.f.broker.history={'status':'COMPLETE','filled_quantity':2,'average_price':100}
  a._record_live_order('TEST','manual close','SELL',2,None,position_id=pid)
  self.assertFalse(any(identity.blockers().values()))
  identity.finish_switch({'account_id':'OTHER'},'SWITCH BROKER ACCOUNT TO OTHER')
  self.assertEqual(gtt.position(pid)['account_id'],'TEST');self.assertEqual(identity.owner(),'OTHER')
  self.assertTrue(risk.load()['baseline_requires_reset']);self.assertTrue(a.get_config()['paused'])
  with patch.object(self.f.broker,'profile',return_value={'user_id':'OTHER'}):
   risk.clear(risk.CLEAR_CONFIRMATION)
   self.assertEqual(risk.app_positions(),[])
 def test_matching_account_still_reconciles_triggered_exit(self):
  _,pid=self.f.buy();self.f.broker.trigger('101')
  self.f.broker.histories['gtt-sell']={'status':'COMPLETE','filled_quantity':2,'average_price':90}
  gtt.reconcile_all()
  self.assertEqual(gtt.position(pid)['status'],'CLOSED')
  c=a.connection();row=c.execute("SELECT account_id FROM autotrade_orders WHERE kite_order_id='gtt-sell'").fetchone();c.close();self.assertEqual(row[0],'TEST')
 def test_identity_unavailable_blocks_mutations(self):
  _,pid=self.f.buy()
  with patch.object(self.f.broker,'profile',side_effect=TimeoutError()),patch.object(self.f.broker,'delete_gtt') as delete:
   with self.assertRaises(identity.AccountIdentityError):gtt.cancel_before_exit(pid,'manual close',self.f.broker)
   delete.assert_not_called()
 def test_binding_rechecks_stale_risk_error_without_resetting_baseline(self):
  self.f.buy();before=risk.load();c=a.connection()
  for table in ['autotrade_orders','autotrade_positions','autotrade_gtts']:c.execute(f'UPDATE {table} SET account_id=NULL')
  c.commit();c.close()
  risk.check(self.f.broker,block_buy=False)
  self.assertIn('unverified ownership',risk.load()['history_error'])
  result=identity.bind_legacy('TEST','BIND LEGACY RECORDS TO TEST')
  self.assertEqual(result['legacy']['total'],0);self.assertIsNone(result['risk_history_error'])
  self.assertEqual(risk.load()['cumulative_loss_baseline'],before['cumulative_loss_baseline'])
  self.assertTrue(a.get_config()['paused']);self.assertFalse(a.get_config()['enabled'])
 def test_binding_keeps_unrelated_risk_failure_visible(self):
  self.f.buy();c=a.connection()
  for table in ['autotrade_orders','autotrade_positions','autotrade_gtts']:c.execute(f'UPDATE {table} SET account_id=NULL')
  c.commit();c.close()
  with patch.object(self.f.broker,'ltp',side_effect=RuntimeError('Quotes unavailable')):
   result=identity.bind_legacy('TEST','BIND LEGACY RECORDS TO TEST')
  self.assertEqual(result['legacy']['total'],0)
  self.assertIn('Quotes unavailable',result['risk_history_error'])
  self.assertTrue(a.get_config()['paused'])
