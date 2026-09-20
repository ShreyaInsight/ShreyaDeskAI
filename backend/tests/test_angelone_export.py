"""Regression against user-supplied TradingView exports, without broker access."""
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
with patch.object(BackgroundScheduler, 'start'):
    from backend import scanner
from backend.confirmed_resolution import prepare

ROOT = Path(__file__).resolve().parents[2]

class AngelOneExportTests(unittest.TestCase):
    def test_export_series_and_confirmed_date(self):
        daily = pd.read_csv(ROOT / 'NSE_ANGELONE, 1D.csv').rename(columns={'Volume': 'volume'})
        daily['date'] = pd.to_datetime(daily.time, unit='s', utc=True).dt.tz_convert('Asia/Kolkata')
        config = {**scanner.DEFAULT_CONFIG, 'use_alternate_resolution': True,
                  '_alternate_sessions': tuple(daily.date.dt.date)}
        comparison = scanner.prepare_frame(daily.to_dict('records'), config)
        recent = comparison[comparison.date.dt.strftime('%Y-%m-%d').between('2026-08-01', '2026-09-04')]
        for _, row in recent.iterrows():
            self.assertAlmostEqual(row.close_ma, row['Close Series'], places=7)
            self.assertAlmostEqual(row.open_ma, row['Open Series'], places=7)
        self.assertEqual(comparison.loc[comparison.buy].iloc[-1].date.strftime('%Y-%m-%d'), '2026-09-02')
        confirmed = prepare(daily.to_dict('records'), {**config, 'alternate_mode': 'confirmed'},
                            datetime(2026, 9, 7, tzinfo=scanner.IST))
        self.assertEqual(confirmed.loc[confirmed.buy].iloc[-1].date.strftime('%Y-%m-%d'), '2026-09-04')
        before = prepare(daily.to_dict('records'), {**config, 'alternate_mode': 'confirmed'},
                         datetime(2026, 9, 4, 15, tzinfo=scanner.IST))
        self.assertFalse(((before.date.dt.strftime('%Y-%m-%d') >= '2026-09-02') & before.buy).any())
        # Verify the underlying 3D candle independently of its 9D alternate plots.
        three = pd.read_csv(ROOT / 'NSE_ANGELONE, 3D.csv')
        block = three[three.time == 1788320700].iloc[0]
        rows = daily[daily.date.dt.strftime('%Y-%m-%d').between('2026-09-02', '2026-09-04')]
        self.assertEqual((rows.open.iloc[0], rows.high.max(), rows.low.min(), rows.close.iloc[-1]),
                         (block.open, block.high, block.low, block.close))
