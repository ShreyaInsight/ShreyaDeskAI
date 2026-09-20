import json,queue,tempfile,time,threading,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from backend import telegram_notifications as t

class TelegramTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.patches=[patch.object(t,'DB_PATH',Path(self.tmp.name)/'telegram.db'),patch.object(t,'settings',dict(t.DEFAULTS)),patch.object(t,'messages',queue.Queue(maxsize=2)),patch.object(t,'urgent',queue.Queue(maxsize=2)),patch.object(t,'seen',{}),patch.dict('os.environ',{'TELEGRAM_BOT_TOKEN':'fake-token-secret','TELEGRAM_CHAT_ID':'fake-chat'})]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.tmp.cleanup()
 def test_emit_never_uses_network_or_disk_and_full_queue_does_not_wait(self):
  with patch.object(t.requests,'post',side_effect=AssertionError('network on critical path')),patch.object(t,'db',side_effect=AssertionError('DB on critical path')):
   start=time.monotonic()
   self.assertTrue(t.emit('Test','one'));self.assertTrue(t.emit('Test','two'));self.assertFalse(t.emit('Test','overflow'))
   self.assertTrue(t.emit('Kill','urgent',category='safety'))
   self.assertLess(time.monotonic()-start,.1)
 def test_slow_worker_does_not_block_enqueue(self):
  entered=threading.Event();release=threading.Event()
  def slow(*args,**kwargs):entered.set();release.wait(2);return Mock(status_code=401)
  t.emit('Test','slow')
  with patch.object(t.requests,'post',side_effect=slow):
   worker=threading.Thread(target=t.deliver,args=(t.messages.get_nowait(),));worker.start();self.assertTrue(entered.wait(1))
   start=time.monotonic();self.assertTrue(t.emit('Kill','now',category='safety'));self.assertLess(time.monotonic()-start,.1)
   release.set();worker.join()
 def test_broken_token_reports_generic_failure_without_exposing_secret(self):
  t.emit('Test','safe')
  with patch.object(t.requests,'post',side_effect=RuntimeError('https://api.telegram.org/botfake-token-secret/sendMessage')):t.deliver(t.messages.get_nowait())
  result=t.public();self.assertNotIn('fake-token-secret',json.dumps(result));self.assertEqual(result['recent'][0]['status'],'FAILED')
 def test_durable_delivery_deduplication_and_no_parse_mode(self):
  response=Mock(status_code=200);response.json.return_value={'ok':True}
  with patch.object(t.requests,'post',return_value=response) as post:
   for _ in range(2):
    t.emit('GTT','SL triggered',key='gtt:1:SL',category='safety');t.deliver(t.urgent.get_nowait());t.seen.clear()
   post.assert_called_once();self.assertNotIn('parse_mode',post.call_args.kwargs['json'])
 def test_expired_session_detection_and_toggles(self):
  from kiteconnect.exceptions import TokenException
  with patch('backend.main.get_kite',side_effect=TokenException('expired')):self.assertEqual(t.connection_check(),'disconnected')
  t.kite_error(TokenException('secret'));self.assertEqual(t.urgent.qsize(),1)
  t.settings['scanner']=False;self.assertFalse(t.emit('Scan','done',category='scanner'))
  with self.assertRaises(ValueError):t.configure({'token':'not-accepted'})
 def test_error_summary_redacts_authorization_and_unknown_access_token(self):
  self.assertNotIn('sensitive-value',t.clean('Failed Authorization: token sensitive-value'))
  self.assertNotIn('a'*32,t.clean('error '+('a'*32)))
 def test_notification_exception_cannot_fail_order_or_kill_switch(self):
  from backend.tests.test_autotrade_safety import SafetyTests
  from backend import autotrade as a,risk
  fixture=SafetyTests();fixture.setUp()
  try:
   with patch.object(t,'emit',side_effect=RuntimeError('notification bug')):
    fixture.order()
    self.assertEqual(len(fixture.broker.calls),1)
    risk.latch(manual=True,kite=fixture.broker)
    self.assertTrue(a.get_config()['kill_switch'])
  finally:fixture.tearDown()
