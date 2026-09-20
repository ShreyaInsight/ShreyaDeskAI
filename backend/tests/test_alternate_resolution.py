import unittest
from datetime import date
from unittest.mock import patch
import pandas as pd
from backend import scanner, autotrade
from backend.alternate_resolution import alternate_series


class AlternateTests(unittest.TestCase):
    def config(self):
        return {**scanner.DEFAULT_CONFIG,'ma_type':'SMA','length':1,'use_alternate_resolution':True,'_alternate_sessions':tuple(date(2026,9,d) for d in [2,3,4,7,8,9])}
    def frame(self):
        return pd.DataFrame([dict(date=pd.Timestamp(f'2026-09-{d:02d}',tz='Asia/Kolkata'),open=o,close=c,high=max(o,c),low=min(o,c),volume=1) for d,o,c in [(2,100,99),(3,99,98),(4,98,97),(7,96,97),(8,97,99),(9,99,101)]])
    def test_three_trading_sessions_skip_weekend(self):
        op,cl=alternate_series(self.frame(),self.config(),scanner.moving_average)
        self.assertEqual(op.tolist(),[100]*3+[96]*3)
        self.assertEqual(cl.tolist(),[97]*3+[101]*3)
    def test_crossover_mapped_to_first_group_day_for_comparison(self):
        f=scanner.prepare_frame(self.frame().to_dict('records'),self.config())
        self.assertEqual(f.loc[f.buy,'date'].dt.strftime('%Y-%m-%d').tolist(),['2026-09-07'])
    def test_delay_measured_in_alternate_bars(self):
        op,cl=alternate_series(self.frame(),{**self.config(),'delay':1},scanner.moving_average)
        self.assertTrue(op.iloc[:3].isna().all());self.assertEqual(cl.iloc[3:].tolist(),[97]*3)
    def test_missing_calendar_fails_instead_of_shifting_groups(self):
        with self.assertRaises(ValueError):alternate_series(self.frame(),{**self.config(),'_alternate_sessions':[]},scanner.moving_average)
    def test_comparison_signals_cannot_reach_execution(self):
        with patch.object(autotrade,'get_config') as cfg, patch.object(autotrade,'_record_live_order') as order:
            autotrade.process_signals([dict(symbol='APLAPOLLO',signal_type='BUY',comparison_only=True)],1)
            cfg.assert_not_called();order.assert_not_called()
    def test_unsupported_multiplier_rejected(self):
        with self.assertRaises(ValueError):alternate_series(self.frame(),{**self.config(),'alternate_multiplier':2},scanner.moving_average)

    def test_cached_history_plus_live_overlay_preserves_occ(self):
        from datetime import datetime
        from backend.market_cache import overlay_quote
        original = self.frame().iloc[:4].to_dict('records')
        cached = [{**bar, 'date': str(bar['date'])} for bar in original[:3]]
        bar = original[-1]
        quote = dict(timestamp='2026-09-07 11:00:00', last_trade_time='2026-09-07 10:59:59',
                     last_price=bar['close'], volume=bar['volume'],
                     ohlc={key:bar[key] for key in ('open','high','low')})
        mixed = overlay_quote(cached, quote, datetime(2026,9,7,11,tzinfo=scanner.IST))
        self.assertIn(' ', mixed[0]['date'])
        self.assertIn('T', mixed[-1]['date'])
        for alternate in (False, True):
            with self.subTest(alternate=alternate):
                config = {**self.config(), 'use_alternate_resolution': alternate}
                expected = scanner.prepare_frame(original, config)
                actual = scanner.prepare_frame(mixed, config)
                pd.testing.assert_frame_equal(actual, expected)
                self.assertEqual(actual.iloc[-1]['date'].strftime('%Y-%m-%d'), '2026-09-07')

    def test_invalid_timestamp_still_fails(self):
        candles = self.frame().to_dict('records')
        candles[-1]['date'] = 'invalid timestamp'
        with self.assertRaises(ValueError):
            scanner.prepare_frame(candles, self.config())
