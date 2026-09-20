"""Daily close RSI with Wilder's SMA-seeded smoothing, independent of OCC."""
import math
import numpy as np


def daily_rsi(candles):
    """Latest RSI(14); today's quote-backed daily close is provisional intraday."""
    try:
        closes = np.asarray([row['close'] for row in candles], dtype=float)
    except (KeyError, TypeError, ValueError):
        return None
    if len(closes) < 15 or not np.isfinite(closes).all() or (closes <= 0).any():
        return None
    changes = np.diff(closes)
    gains = np.maximum(changes, 0)
    losses = np.maximum(-changes, 0)
    gain, loss = float(gains[:14].mean()), float(losses[:14].mean())
    for up, down in zip(gains[14:], losses[14:]):
        gain = (gain * 13 + up) / 14
        loss = (loss * 13 + down) / 14
    # TradingView's built-in RSI checks zero loss first (also for flat series).
    return 100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss)


def passes_rsi_filter(value, config):
    low, high = config.get('rsi_min'), config.get('rsi_max')
    if low is None and high is None:
        return True
    if value is None or not math.isfinite(value):
        return False
    return (low is None or value >= low) and (high is None or value <= high)
