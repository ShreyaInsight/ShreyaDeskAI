"""Regression tests for exchange-day attribution and actual crossover semantics."""
import unittest
from backend.scanner import DEFAULT_CONFIG, prepare_frame


class OccParityTests(unittest.TestCase):
    def candles(self, closes):
        return [dict(date=f'2026-09-{i+1:02d}T00:00:00+05:30',open=100,close=c,high=max(100,c)+1,low=min(100,c)-1,volume=1000) for i,c in enumerate(closes)]
    def test_kite_midnight_uses_ist_trading_day(self):
        frame=prepare_frame(self.candles([99,101,102]),{**DEFAULT_CONFIG,'ma_type':'SMA','length':1})
        event=frame[frame.buy].iloc[0]
        self.assertEqual(event.date.strftime('%Y-%m-%d'),'2026-09-02')
        self.assertEqual(event.date.utcoffset().total_seconds(),19800)
    def test_bullish_series_is_not_a_new_buy_each_day(self):
        frame=prepare_frame(self.candles([99,101,102]),{**DEFAULT_CONFIG,'ma_type':'SMA','length':1})
        self.assertEqual(frame.buy.tolist(),[False,True,False])
        self.assertGreater(frame.iloc[-1].close_ma,frame.iloc[-1].open_ma)
    def test_utc_and_ist_input_represent_same_candle(self):
        candles=self.candles([99,101])
        candles[1]['date']='2026-09-01T18:30:00+00:00'
        frame=prepare_frame(candles,{**DEFAULT_CONFIG,'ma_type':'SMA','length':1})
        self.assertEqual(frame.iloc[-1].date.strftime('%Y-%m-%d'),'2026-09-02')
