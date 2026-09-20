"""HTTP boundary tests. Install backend/requirements-dev.txt to run these."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from backend import main, autotrade as a, risk
from backend.tests.test_autotrade_safety import Broker


class RiskApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patches = [patch('backend.telegram_notifications.start'),patch.object(a, "DB_PATH", Path(self.temp.name)/"api.db"),
                        patch.object(a, "get_kite", return_value=Broker()),
                        patch.object(a, "scheduler", Mock(running=True)),
                        patch.object(main, "_password_matches", return_value=True),
                        patch.dict(main.os.environ, {"SHREYADESK_USERNAME": "test-user"})]
        for p in self.patches: p.start()
        main.sessions.clear(); main.rate_limits.clear()
        self.client = TestClient(main.app, base_url="https://testserver")
        self.client.__enter__()
    def tearDown(self):
        self.client.__exit__(None, None, None)
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()
    def login(self):
        self.assertEqual(self.client.post("/api/login", json={"username":"test-user", "password":"test"}).status_code, 200)
        return {"X-CSRF-Token": self.client.cookies["shreyadesk_csrf"]}
    def test_gtt_recovery_requires_authentication_csrf_and_typed_confirmation(self):
        path='/api/autotrade/positions/1/gtt/reconcile'
        self.assertEqual(self.client.post(path,json={}).status_code,401)
        headers=self.login()
        self.assertEqual(self.client.post(path,json={'confirm_phrase':'RECONCILE GTT'}).status_code,403)
        self.assertEqual(self.client.post(path,headers=headers,json={'confirm_phrase':'yes'}).status_code,409)
        with patch('backend.gtt.resolve',return_value={'gtt_status':'ACTIVE'}) as resolve:
            response=self.client.post(path,headers=headers,json={'confirm_phrase':'RECONCILE GTT','trigger_id':101})
            self.assertEqual(response.status_code,200)
            resolve.assert_called_once_with(1,101,'RECONCILE GTT')

    def test_mutations_require_authentication(self):
        for path in ["/api/autotrade/kill-switch", "/api/autotrade/kill-switch/clear", "/api/autotrade/risk/reconcile-day"]:
            self.assertEqual(self.client.post(path, json={}).status_code, 401)
    def test_clear_requires_csrf_even_with_missing_cookie(self):
        self.login()
        self.assertEqual(self.client.post("/api/autotrade/kill-switch/clear", json={"confirm_phrase":risk.CLEAR_CONFIRMATION}).status_code,403)
        self.client.cookies.delete("shreyadesk_csrf")
        self.assertEqual(self.client.post("/api/autotrade/kill-switch", json={}).status_code,403)
    def test_clear_phrase_and_persisted_paused_response(self):
        headers = self.login()
        self.assertEqual(self.client.post("/api/autotrade/kill-switch/clear", headers=headers, json={"confirm_phrase":"yes"}).status_code,422)
        response = self.client.post("/api/autotrade/kill-switch/clear", headers=headers, json={"confirm_phrase":risk.CLEAR_CONFIRMATION})
        self.assertEqual(response.status_code,200)
        self.assertIsNotNone(response.json()["cumulative_loss_baseline"])
        self.assertFalse(response.json()["enabled"])
        self.assertTrue(response.json()["paused"])
        result = self.client.post("/api/autotrade/kill-switch", headers=headers)
        self.assertTrue(result.json()["manual_kill_switch"])
        result = self.client.post("/api/autotrade/config", headers=headers, json={"kill_switch":False, "enabled":True, "paused":False})
        self.assertTrue(result.json()["kill_switch"])
        self.assertFalse(result.json()["enabled"])
    def test_invalid_history_requires_validation(self):
        headers = self.login()
        response = self.client.post("/api/autotrade/risk/reconcile-day", headers=headers, json={"day":"not-a-date", "value":0,"confirm_phrase":risk.RECONCILE_CONFIRMATION})
        self.assertEqual(response.status_code,422)

    def test_portfolio_total_payload_is_rejected(self):
        headers = self.login()
        response = self.client.post("/api/autotrade/risk/reconcile-day", headers=headers,
                                    json={"day":"2026-09-07","value":513.98,"prices":[],"confirm_phrase":risk.RECONCILE_CONFIRMATION})
        self.assertEqual(response.status_code,422)

    def test_trade_history_lookup_requires_authentication(self):
        self.assertEqual(self.client.get("/api/autotrade/risk/history-positions?day=2026-09-07").status_code,401)
        self.login()
        self.assertEqual(self.client.get("/api/autotrade/risk/history-positions?day=2026-09-07").json(), {"positions":[]})

    def test_valid_reconciliation_stores_only_identified_app_trade(self):
        from datetime import datetime, timedelta
        from backend.tests.test_autotrade_safety import Clock
        headers = self.login()
        with patch.object(a, "datetime", Clock), patch.object(risk, "datetime", Clock):
            Clock.value = datetime(2026,9,7,10,tzinfo=a.IST)
            risk.clear(risk.CLEAR_CONFIRMATION)
            conn = a.connection()
            cursor = conn.execute("INSERT INTO autotrade_positions(symbol,mode,status,entry_time,entry_price,quantity,current_sl,current_tp) VALUES ('APP','live','OPEN',?,100,10,90,110)", (a.now(),))
            position_id = cursor.lastrowid
            conn.commit(); conn.close()
            Clock.value += timedelta(days=1)
            listing = self.client.get("/api/autotrade/risk/history-positions?day=2026-09-07").json()
            self.assertEqual(listing["positions"][0]["position_id"],position_id)
            response = self.client.post("/api/autotrade/risk/reconcile-day",headers=headers,json={"day":"2026-09-07","prices":[{"position_id":position_id,"price":90}],"confirm_phrase":risk.RECONCILE_CONFIRMATION})
            self.assertEqual(response.status_code,200)
            self.assertFalse(response.json()["enabled"])
            import json
            conn = risk.connection()
            trades = json.loads(conn.execute("SELECT trades FROM autotrade_app_pnl_days WHERE source='manual'").fetchone()[0])
            conn.close()
            self.assertEqual(trades[0]["position_id"],position_id)
            self.assertEqual(trades[0]["pnl"],-100)


    def test_analytics_is_authenticated_and_mode_validated(self):
        self.assertEqual(self.client.get('/api/analytics').status_code,401)
        self.login()
        self.assertEqual(self.client.get('/api/analytics?mode=both').status_code,422)
        result=self.client.get('/api/analytics?mode=paper')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['mode'],'paper')
        self.assertEqual(result.json()['orders'],[])

    def test_confirmed_setting_roundtrip_does_not_enable_execution(self):
        from backend import scanner
        headers=self.login()
        before=a.get_config()['enabled']
        with patch.object(scanner,'DB_PATH',a.DB_PATH),patch.object(scanner,'index_symbols',return_value={'ZYDUSWELL':'Zydus'}):
            response=self.client.post('/api/scanner/config',headers=headers,json={'index_name':'NIFTY50','use_alternate_resolution':True,'alternate_mode':'confirmed'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(self.client.get('/api/scanner/config').json()['alternate_mode'],'confirmed')
            self.assertEqual(self.client.post('/api/scanner/config',headers=headers,json={'alternate_mode':'unsafe'}).status_code,422)
        self.assertEqual(a.get_config()['enabled'],before)


    def test_telegram_endpoints_require_session_and_csrf_and_hide_credentials(self):
        from backend import telegram_notifications as telegram
        self.assertEqual(self.client.get('/api/telegram').status_code,401)
        headers=self.login()
        self.assertEqual(self.client.post('/api/telegram/test').status_code,403)
        with patch.object(telegram,'DB_PATH',Path(self.temp.name)/'notifications.db'):
            response=self.client.get('/api/telegram')
            self.assertEqual(response.status_code,200)
            self.assertNotIn('bot_token',response.json())
            self.assertEqual(self.client.post('/api/telegram/settings',headers=headers,json={'bot_token':'forbidden'}).status_code,422)
        with patch.object(telegram,'emit',return_value=True) as emit:
            self.assertEqual(self.client.post('/api/telegram/test',headers=headers).json(),{'queued':True})
            emit.assert_called_once()

if __name__ == "__main__": unittest.main()
