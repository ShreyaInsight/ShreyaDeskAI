import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from backend import autotrade as a, analytics


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.p=patch.object(a,'DB_PATH',Path(self.tmp.name)/'analytics.db');self.p.start()
    def tearDown(self):self.p.stop();self.tmp.cleanup()
    def position(self,mode='live',status='OPEN'):
        c=a.connection()
        rid=c.execute("INSERT INTO autotrade_positions(symbol,mode,status,entry_time,entry_price,quantity,current_sl,current_tp,exit_time,exit_price,realized_pnl) VALUES ('APP',?,?, '2026-09-04T10:00:00+05:30',100,10,98,105,?,?,?)",(mode,status,'2026-09-05T10:00:00+05:30' if status=='CLOSED' else None,110 if status=='CLOSED' else None,100 if status=='CLOSED' else None)).lastrowid
        c.commit();c.close();return rid
    def test_broker_history_excludes_blocked_and_uncertain_attempts(self):
        a.order_log('BLOCK','BUY','live',1,100,'BLOCKED','Symbol ceiling')
        a.order_log('MAYBE','BUY','live',1,100,'PENDING','Submission uncertain')
        oid=a.order_log('ACCEPT','BUY','live',2,100,'OPEN',order_type='LIMIT')
        a._update_order(oid,kite_order_id='broker-1',filled_quantity=1,fill_price=99)
        result=analytics.snapshot('live')
        self.assertEqual(len(result['orders']),1)
        self.assertEqual(result['orders'][0]['display_status'],'PARTIAL / OPEN')
        self.assertEqual(result['orders'][0]['fill_price'],99)
        self.assertEqual({r['outcome_type'] for r in result['logs']},{'BLOCKED','UNCERTAIN','OK'})
    def test_pnl_is_app_only_and_modes_are_isolated(self):
        self.position();self.position('paper','CLOSED')
        broker=Mock();broker.ltp.return_value={'NSE:APP':{'last_price':90},'NSE:MANUAL':{'last_price':999}}
        with patch.object(a,'get_kite',return_value=broker):r=analytics.snapshot('live')
        self.assertEqual(len(r['trades']),1);self.assertEqual(r['trades'][0]['pnl'],-100);self.assertEqual(r['trades'][0]['pnl_pct'],-10)
        broker.ltp.assert_called_once_with(['NSE:APP']);broker.positions.assert_not_called();broker.holdings.assert_not_called()
        paper=analytics.snapshot('paper')['trades'][0]
        self.assertEqual(paper['pnl'],100);self.assertEqual(paper['duration_hours'],24)
    def test_missing_quote_is_not_zero_pnl(self):
        self.position();broker=Mock();broker.ltp.side_effect=RuntimeError('offline')
        with patch.object(a,'get_kite',return_value=broker):r=analytics.snapshot('live')
        self.assertIsNone(r['trades'][0]['pnl']);self.assertTrue(r['warnings'])
    def test_paused_and_comparison_skips_never_reserve_buys(self):
        with patch('backend.scanner.get_config',return_value={'strategy':'occ'}), patch.object(a,'get_config',return_value={'mode':'live','enabled':False,'paused':True,'kill_switch':False}):
            a.process_signals([{'symbol':'PAUSED','signal_type':'BUY'},{'symbol':'COMPARE','signal_type':'EXIT','comparison_only':True}],42)
        c=a.connection()
        self.assertEqual(c.execute('SELECT COUNT(*) FROM analytics_skips').fetchone()[0],1)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM autotrade_orders').fetchone()[0],0)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM autotrade_buy_reservations').fetchone()[0],0);c.close()
    def test_timestamps_are_ist_and_unknown_action_is_not_guessed(self):
        self.assertEqual(analytics.stamp('2026-09-04T22:00:00+00:00'),'2026-09-05T03:30:00+05:30')
        self.assertEqual(analytics.action('unknown legacy source'),'UNKNOWN')
