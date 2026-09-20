from unittest.mock import patch
from backend import main,broker_identity as identity,autotrade as a,risk
from backend.tests import test_dashboard_sessions as base

class AccountSwitchApiTests(base.DashboardSessionTests):
 def candidate(self):
  headers=self.login();identity.save({'account_id':'ORIGINAL'})
  self.kite.profile.return_value={'user_id':'NEW'}
  state=self.begin()
  response=self.client.get('/api/kite/callback',params={'state':state,'status':'success','request_token':'fixture'},follow_redirects=False)
  self.assertEqual(response.status_code,302);main.save_session.assert_not_called()
  return headers
 def test_candidate_is_private_and_keep_retains_original(self):
  headers=self.candidate();response=self.client.get('/api/session');data=response.json()
  self.assertEqual(data['account_change']['from_account'],'ORIGINAL');self.assertEqual(data['account_change']['to_account'],'NEW')
  self.assertNotIn('fixture-new',response.text)
  path='/api/broker-account/switch'
  self.assertEqual(self.client.post(path,json={'action':'keep'}).status_code,403)
  self.assertEqual(self.client.post(path,json={'action':'keep'},headers=headers).status_code,200)
  main.save_session.assert_not_called();self.assertIsNone(self.client.get('/api/session').json()['account_change'])
 def test_explicit_empty_account_switch_requires_phrase_and_baseline(self):
  headers=self.candidate();path='/api/broker-account/switch'
  self.assertEqual(self.client.post(path,json={'action':'switch','confirm_phrase':'yes'},headers=headers).status_code,409)
  self.assertEqual(self.client.post(path,json={'action':'switch','confirm_phrase':'SWITCH BROKER ACCOUNT TO NEW'},headers=headers).status_code,200)
  self.assertEqual(identity.owner(),'NEW');self.assertTrue(a.get_config()['paused']);self.assertTrue(risk.load()['baseline_requires_reset'])
  self.assertEqual(main.save_session.call_args.args[0]['account_id'],'NEW')
 def test_switch_rejects_open_exposure(self):
  headers=self.candidate();c=a.connection()
  c.execute("INSERT INTO autotrade_positions(symbol,mode,status,entry_time,entry_price,quantity,current_sl,current_tp,account_id) VALUES ('APP','live','OPEN',?,100,1,90,120,'ORIGINAL')",(a.now(),));c.commit();c.close()
  response=self.client.post('/api/broker-account/switch',json={'action':'switch','confirm_phrase':'SWITCH BROKER ACCOUNT TO NEW'},headers=headers)
  self.assertEqual(response.status_code,409);self.assertEqual(identity.owner(),'ORIGINAL');main.save_session.assert_not_called()
 def test_failed_token_persistence_restores_workspace_owner(self):
  headers=self.candidate();main.save_session.side_effect=OSError('disk unavailable')
  # Application returns a server error; ownership must not move on partial commit.
  with self.assertRaises(OSError):
   self.client.post('/api/broker-account/switch',json={'action':'switch','confirm_phrase':'SWITCH BROKER ACCOUNT TO NEW'},headers=headers)
  self.assertEqual(identity.owner(),'ORIGINAL')
 def test_legacy_binding_requires_auth_csrf_and_verified_account(self):
  self.assertEqual(self.client.post('/api/broker-account/bind-legacy',json={}).status_code,401)
  headers=self.login()
  self.assertEqual(self.client.post('/api/broker-account/bind-legacy',json={}).status_code,403)
  self.assertEqual(self.client.post('/api/broker-account/bind-legacy',headers=headers,json={'account_id':'OTHER','confirm_phrase':'BIND LEGACY RECORDS TO OTHER'}).status_code,409)
