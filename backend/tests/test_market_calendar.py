"""Shared NSE calendar integration: no broker calls or production state."""
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import patch
from backend import market_calendar as cal, risk, autotrade, scanner, market_cache, supertrend, supertrend_scan
from backend.confirmed_resolution import prepare

class CalendarTests(unittest.TestCase):
 def at(self, day, hour=12, minute=0):return datetime.fromisoformat(day).replace(hour=hour,minute=minute,tzinfo=cal.IST)
 def test_weekday_holiday_and_next_session(self):
  for day in [date(2026,1,26),date(2026,9,14)]:
   self.assertFalse(cal.is_trading_day(day));self.assertIsNone(cal.get_session_hours(day))
  self.assertEqual(cal.next_trading_day(date(2026,9,11)),date(2026,9,15))
 def test_special_saturday_and_muhurat_hours(self):
  self.assertTrue(cal.is_trading_day(date(2025,2,1)))
  self.assertEqual(cal.is_special_session(date(2025,10,21)),'Muhurat Trading')
  self.assertEqual(cal.get_session_hours(date(2025,10,21)),(time(13,45),time(14,45)))
  for hour,minute,opened in [(13,44,False),(13,45,True),(14,39,True),(14,40,False)]:
   self.assertEqual(cal.is_open(self.at('2025-10-21',hour,minute),exit_buffer_minutes=5),opened)
 def test_unknown_calendar_or_hours_block(self):
  with self.assertRaises(cal.CalendarUnavailable):cal.is_trading_day(date(2027,1,4))
  with self.assertRaises(cal.CalendarUnavailable):cal.is_open(self.at('2026-11-08',18))
 def test_order_gate_uses_shared_hours(self):
  for day,hour,minute,opened in [('2026-01-26',12,0,False),('2025-10-21',13,50,True),('2025-10-21',14,40,False),('2026-02-01',12,0,True)]:
   with patch.object(autotrade,'datetime') as clock:
    clock.now.return_value=self.at(day,hour,minute)
    self.assertEqual(autotrade._market_is_open(),opened)
 def test_scheduled_scans_and_cache_skip_holiday(self):
  with patch.object(scanner,'datetime') as clock,patch('backend.main.enqueue_scanner') as enqueue:
   clock.now.return_value=self.at('2026-09-14')
   scanner.queue_scheduled_scan();scanner.queue_scheduled_scan(execute=True);enqueue.assert_not_called()
   clock.now.return_value=self.at('2026-02-01')
   scanner.queue_scheduled_scan();enqueue.assert_called_once_with('scheduled-1200')
  with patch.object(market_cache,'today',return_value=date(2026,9,14)),patch('backend.main.get_kite') as broker:
   market_cache.warm_full_universe();market_cache.refresh_history({'APP':1},date(2026,1,1));broker.assert_not_called()
 def test_supertrend_finalization_and_quote_overlay_use_special_session(self):
  row=dict(date='2025-10-21',open=100,high=101,low=99,close=100,volume=1)
  self.assertEqual(supertrend.finalized([row],self.at('2025-10-21',14,44)),[])
  self.assertEqual(len(supertrend.finalized([row],self.at('2025-10-21',14,45))),1)
  holiday={**row,'date':'2026-01-26'}
  self.assertEqual(supertrend.finalized([holiday],self.at('2026-01-27')),[])
  with patch('backend.scanner.get_kite') as broker:
   supertrend.refresh_close({'APP':1},self.at('2026-01-26',17));broker.assert_not_called()
  self.assertEqual(market_cache.overlay_quote([],{},self.at('2025-10-21',12)),[])
  self.assertEqual(market_cache.overlay_quote([],{},self.at('2026-01-26',12)),[])
 def test_sunday_and_holiday_messages(self):
  for day,label in [('2026-09-13','Sunday'),('2026-09-14','Ganesh Chaturthi')]:
   ok,reason=supertrend_scan.eligible('2026-09-11','2026-09-11',{},self.at(day))
   self.assertFalse(ok);self.assertIn(label,reason);self.assertIn('No trading session',reason)
 def test_risk_coverage_skips_holiday_but_includes_special_session(self):
  c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;self.addCleanup(c.close)
  c.execute('CREATE TABLE autotrade_app_pnl_days(baseline TEXT,day TEXT,trades TEXT,finalized INTEGER,observed_at TEXT,source TEXT,PRIMARY KEY(baseline,day))')
  state={'cumulative_loss_baseline':self.at('2026-01-26').isoformat()}
  self.assertIsNone(risk._history_error(c,state,self.at('2026-01-27',10)))
  state={'cumulative_loss_baseline':self.at('2025-10-21',13).isoformat()}
  self.assertIsNone(risk._history_error(c,state,self.at('2025-10-21',15,29)))
  self.assertIn('2025-10-21',risk._history_error(c,state,self.at('2025-10-21',15,30)))
  risk._capture_day(c,state,[],self.at('2025-10-21',15,30))
  self.assertIsNone(risk._history_error(c,state,self.at('2025-10-23',10)))
 def test_three_day_blocks_span_holiday_without_counting_it(self):
  sessions=cal.sessions(date(2026,9,2),date(2026,9,16))
  candles=[dict(date=day.isoformat(),open=100,high=111,low=89,close=90 if i<6 else 110,volume=100) for i,day in enumerate(sessions) if day<=date(2026,9,15)]
  config={**scanner.DEFAULT_CONFIG,'ma_type':'SMA','length':1,'delay':0,'adx_enabled':False,'use_alternate_resolution':True,'alternate_mode':'confirmed','_alternate_sessions':sessions}
  before=prepare(candles,config,self.at('2026-09-15',14))
  self.assertFalse(before.buy.any())
  after=prepare(candles,config,self.at('2026-09-16',10))
  self.assertEqual(after.loc[after.buy,'date'].dt.date.tolist(),[date(2026,9,15)])
 def test_unknown_future_never_comes_from_observed_candles(self):
  with self.assertRaises(cal.CalendarUnavailable):cal.historical_sessions(date(2026,12,31),date(2027,1,4),[date(2027,1,4)])
 def test_update_validation_and_cache_invalidation(self):
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'active.json';path.write_text(json.dumps(cal.data()))
   candidate=Path(directory)/'candidate.json'
   with patch.object(cal,'DATA_PATH',path):
    self.assertTrue(cal.is_trading_day(date(2026,9,15)))
    updated=json.loads(path.read_text());updated['holidays']['2026-09-15']='Test closure';updated['version']='test-update';candidate.write_text(json.dumps(updated))
    cal.install(candidate);self.assertFalse(cal.is_trading_day(date(2026,9,15)))
    updated['special_sessions']['2026-09-15']={'label':'Invalid','open':'16:00','close':'09:00'};candidate.write_text(json.dumps(updated))
    with self.assertRaises(ValueError):cal.install(candidate)
    self.assertFalse(cal.is_trading_day(date(2026,9,15)))

 def test_official_3d_calendar_does_not_depend_on_benchmark_cache(self):
  from backend.alternate_resolution import session_calendar
  with patch.object(market_cache,'read_histories',side_effect=AssertionError('Official coverage must not require benchmark candles')):
   days=session_calendar(date(2026,9,2),date(2026,9,16))
   self.assertNotIn(date(2026,9,14),days);self.assertIn(date(2026,9,15),days)
 def test_scanner_fundamentals_use_cache_on_holiday(self):
  with patch.object(scanner,'datetime') as clock,patch.object(scanner,'fundamentals_cache',{}),patch.object(scanner,'previous_fundamentals',return_value={'pe_ratio':20}),patch.object(scanner.requests,'get') as get:
   clock.now.return_value=self.at('2026-09-14')
   self.assertEqual(scanner.screener_fundamentals('APP')['pe_ratio'],20)
   get.assert_not_called()
 def test_status_exposes_version_and_missing_future_calendar(self):
  status=cal.status(self.at('2026-01-26'))
  self.assertFalse(status['market_open']);self.assertIn('Republic Day',status['message']);self.assertIsNotNone(status['version'])
  self.assertIn('2027',cal.status(self.at('2027-01-04'))['error'])
