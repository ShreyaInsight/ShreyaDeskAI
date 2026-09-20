"""TradingView-aligned NSE 3-session aggregation for historical comparison only."""
from datetime import date, timedelta
import pandas as pd

# Verified boundary in the user's TradingView NSE APLAPOLLO 3D export.
ANCHOR = date(2026, 9, 2)


def session_calendar(start: date, end: date):
    from .market_calendar import data, historical_sessions
    first=min(start,ANCHOR)
    observed=()
    if first.year < min(data()['years']):
        from .market_cache import read_histories
        rows=read_histories(['NIFTY 50'],first)['NIFTY 50']
        observed={pd.Timestamp(c['date']).date() if pd.Timestamp(c['date']).tzinfo is None else pd.Timestamp(c['date']).tz_convert('Asia/Kolkata').date() for c in rows}
    return historical_sessions(first,max(end,ANCHOR),observed)


def alternate_series(frame, config, moving_average):
    multiplier = config.get('alternate_multiplier', 3)
    if multiplier != 3:
        raise ValueError('Only the verified 3D multiplier (3) is supported')
    dates = frame['date'].dt.date
    sessions = config.get('_alternate_sessions')
    if sessions is None:
        sessions = session_calendar(dates.min(), dates.max())
    # The frame may contain today's real daily candle or validated quote overlay.
    from .market_cache import today
    from .market_calendar import is_trading_day
    if dates.max() == today() and today() not in sessions and is_trading_day(today()):
        sessions = sorted([*sessions, today()])
    indices = {day: i for i, day in enumerate(sessions)}
    if ANCHOR not in indices or any(day not in indices for day in dates):
        raise ValueError('Incomplete NSE session calendar; cannot align alternate candles')
    groups = pd.Series([(indices[day]-indices[ANCHOR])//3 for day in dates], index=frame.index)
    bars = frame.groupby(groups).agg(open=('open','first'),close=('close','last'),high=('high','max'),low=('low','min'),volume=('volume','sum'))
    # Discard a partial warm-up group, not a developing final group: lookahead_on
    # historical comparison intentionally maps each group's final value backwards.
    if (indices[dates.iloc[0]]-indices[ANCHOR]) % 3:
        bars = bars.iloc[1:]
    values = {}
    for name in ('open','close'):
        values[name] = moving_average(bars[name], config['ma_type'], config['length'], config['offset_sigma'], config['alma_offset'], bars['volume']).shift(config['delay'])
    return groups.map(values['open']), groups.map(values['close'])
