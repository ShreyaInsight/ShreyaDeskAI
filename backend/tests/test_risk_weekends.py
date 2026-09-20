"""History coverage must not demand snapshots that cannot finalize on weekends."""
import sqlite3
import unittest
from datetime import datetime
from backend import risk


class WeekendHistoryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.execute('CREATE TABLE autotrade_app_pnl_days (baseline TEXT, day TEXT, trades TEXT, finalized INTEGER, observed_at TEXT, source TEXT, UNIQUE(baseline,day))')
        self.state = {'cumulative_loss_baseline': self.at(11, 10).isoformat()}

    def at(self, day, hour=17):
        return datetime(2026, 9, day, hour, tzinfo=risk.trade.IST)

    def capture(self, day, hour=17):
        risk._capture_day(self.conn, self.state, [], self.at(day, hour))

    def test_weekend_without_any_snapshot_is_not_missing(self):
        self.capture(11)
        for day in (12, 13):
            self.assertIsNone(risk._history_error(self.conn, self.state, self.at(day)))

    def test_unfinalized_weekend_samples_do_not_block_monday(self):
        for day in (11, 12, 13):
            self.capture(day)
        self.capture(14, 10)
        self.assertIsNone(risk._history_error(self.conn, self.state, self.at(14, 10)))

    def test_missing_or_unfinalized_friday_still_blocks(self):
        for captured in (False, True):
            if captured:
                self.capture(11, 10)
            error = risk._history_error(self.conn, self.state, self.at(13))
            self.assertIn('2026-09-11', error)
            self.assertNotIn('2026-09-12', error)
            self.assertNotIn('2026-09-13', error)

    def test_weekend_baseline_does_not_require_weekend_history(self):
        self.state['cumulative_loss_baseline'] = self.at(12).isoformat()
        self.capture(14, 10)
        self.assertIsNone(risk._history_error(self.conn, self.state, self.at(14, 10)))

    def test_holiday_monday_does_not_block_tuesday(self):
        self.capture(11)
        self.capture(15, 10)
        self.assertIsNone(risk._history_error(self.conn, self.state, self.at(15, 10)))

    def test_missing_regular_monday_still_blocks_tuesday(self):
        self.state['cumulative_loss_baseline'] = self.at(18,10).isoformat()
        self.capture(18)
        self.capture(22,10)
        self.assertIn('2026-09-21',risk._history_error(self.conn,self.state,self.at(22,10)))
