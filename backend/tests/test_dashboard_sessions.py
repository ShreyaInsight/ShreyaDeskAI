import unittest
from urllib.parse import urlparse,parse_qs
from unittest.mock import Mock,patch
from fastapi.testclient import TestClient
from backend import main, dashboard_sessions as ds
from backend.tests import test_risk_api as base

class DashboardSessionTests(unittest.TestCase):
    setUp=base.RiskApiTests.setUp
    tearDown=base.RiskApiTests.tearDown
    login=base.RiskApiTests.login
    def setUp(self):
        base.RiskApiTests.setUp(self);ds.metadata.clear()
        self.kite=Mock();self.kite.profile.return_value={'user_id':'FAKE'}
        self.kite.generate_session.return_value={'access_token':'fixture-new'}
        for p in [patch.object(main,'read_saved_session',return_value={'access_token':'fixture','connected_at':'2026-09-07'}),patch.object(main,'get_kite',return_value=self.kite),patch.object(main,'KiteConnect',return_value=self.kite),patch.object(main,'save_session'),patch.dict(main.os.environ,{'KITE_API_KEY':'fixture-key','KITE_API_SECRET':'fixture-secret'})]:p.start();self.patches.append(p)
    def begin(self, client=None):
        response=(client or self.client).get('/api/kite/login',follow_redirects=False)
        self.assertEqual(response.status_code,307)
        params=parse_qs(urlparse(response.headers['location']).query)
        return parse_qs(params['redirect_params'][0])['state'][0]
    def test_pending_blocks_data_and_mutations_but_not_other_session(self):
        self.login()
        other=TestClient(main.app,base_url='https://testserver')
        other.post('/api/login',json={'username':'test-user','password':'test'},headers={'user-agent':'Second device'})
        self.begin(other)
        self.assertFalse(other.get('/api/session').json()['connected'])
        self.assertTrue(other.get('/api/session').json()['authenticated'])
        for path in ['/api/profile','/api/autotrade/positions','/api/scanner/results','/scanner/results']:
            self.assertEqual(other.get(path).status_code,403)
        response=other.post('/api/autotrade/enable',json={'mode':'live','confirm_phrase':'ENABLE LIVE TRADING'},headers={'X-CSRF-Token':other.cookies['shreyadesk_csrf']})
        self.assertEqual(response.status_code,403)
        self.assertTrue(self.client.get('/api/session').json()['connected'])
        other.close()
    def test_valid_callback_shared_connection_and_single_use_state(self):
        self.login();state=self.begin()
        self.assertEqual(self.client.get('/api/kite/callback?status=success&request_token=fixture&state=wrong').status_code,403)
        main.save_session.assert_not_called()
        response=self.client.get('/api/kite/callback',params={'state':state,'status':'success','request_token':'fixture'},follow_redirects=False)
        self.assertEqual(response.status_code,302)
        self.assertTrue(self.client.get('/api/session').json()['connected'])
        self.assertEqual(self.client.get('/api/kite/callback',params={'state':state,'status':'success','request_token':'fixture'}).status_code,403)
        main.save_session.assert_called_once()
        activity=self.client.get('/api/sessions').json()
        self.assertEqual(activity['last_kite_login']['kind'],'kite_connected')
    def test_cross_session_expired_and_uninitiated_callbacks_do_not_exchange(self):
        self.login()
        self.assertEqual(self.client.get('/api/kite/callback?status=success&request_token=fixture').status_code,403)
        state=self.begin()
        other=TestClient(main.app,base_url='https://testserver');other.post('/api/login',json={'username':'test-user','password':'test'})
        self.assertEqual(other.get('/api/kite/callback',params={'state':state,'status':'success','request_token':'fixture'}).status_code,403)
        token=self.client.cookies['shreyadesk_session'];ds.metadata[token]['oauth_expires']=0
        self.assertEqual(self.client.get('/api/kite/callback',params={'state':state,'status':'success','request_token':'fixture'}).status_code,403)
        self.kite.generate_session.assert_not_called();other.close()
    def test_real_broker_validation_and_no_store_on_errors_success_and_redirects(self):
        for response in [self.client.get('/api/session'),self.client.get('/')]:
            self.assertIn('no-store',response.headers['cache-control'])
        self.login();self.kite.profile.side_effect=RuntimeError('Expired token')
        self.assertFalse(self.client.get('/api/session').json()['connected'])
        for response in [self.client.get('/api/session'),self.client.get('/api/kite/login',follow_redirects=False)]:
            self.assertEqual(response.headers['pragma'],'no-cache')
            self.assertIn('must-revalidate',response.headers['cache-control'])
    def test_activity_records_device_without_exposing_credentials(self):
        self.client.post('/api/login',json={'username':'test-user','password':'test'},headers={'user-agent':'Example Mobile/1'})
        response=self.client.get('/api/sessions');data=response.json()
        self.assertTrue(data['active_sessions'][0]['current'])
        self.assertEqual(data['recent_logins'][0]['user_agent'],'Example Mobile/1')
        self.assertEqual(data['recent_logins'][0]['ip'],'testclient')
        self.assertNotIn(self.client.cookies['shreyadesk_session'],response.text)
        self.assertNotIn(self.client.cookies['shreyadesk_csrf'],response.text)
        self.assertNotIn('oauth_state',response.text)

    def test_callback_cannot_overwrite_a_newer_flow_or_revoked_session(self):
        self.login()
        for revoke in (False, True):
            state=self.begin()
            token=self.client.cookies['shreyadesk_session']
            def during_exchange(*args, **kwargs):
                with main.session_lock:
                    if revoke:
                        main.sessions.pop(token,None)
                        ds.metadata.pop(token,None)
                    else:
                        ds.metadata[token]['oauth_state']='a-newer-flow'
                return {'access_token':'fixture-new'}
            self.kite.generate_session.side_effect=during_exchange
            response=self.client.get('/api/kite/callback',params={'state':state,'status':'success','request_token':'fixture'},follow_redirects=False)
            self.assertEqual(response.status_code,401 if revoke else 409)
            main.save_session.assert_not_called()

    def test_cross_site_login_start_rejected_and_rate_errors_uncacheable(self):
        self.login()
        self.assertEqual(self.client.get('/api/kite/login',headers={'Sec-Fetch-Site':'cross-site'}).status_code,403)
        self.assertFalse(self.client.get('/api/session').json()['kite_pending'])
        with patch.object(main,'_rate_limit',side_effect=main.HTTPException(status_code=429,detail='Try later')):
            response=self.client.post('/api/login',json={'username':'test-user','password':'test'})
        self.assertEqual(response.status_code,429)
        self.assertIn('no-store',response.headers['cache-control'])

    def test_legacy_exchange_also_requires_session_bound_state(self):
        headers=self.login()
        payload={'api_key':'fixture-key','api_secret':'fixture-secret','request_token':'fixture'}
        self.assertEqual(self.client.post('/api/kite/login',headers=headers,json=payload).status_code,422)
        payload['state']='uninitiated'
        self.assertEqual(self.client.post('/api/kite/login',headers=headers,json=payload).status_code,403)
        payload['state']=self.begin()
        self.assertEqual(self.client.post('/api/kite/login',headers=headers,json=payload).status_code,200)
        self.assertFalse(self.client.get('/api/session').json()['kite_pending'])
