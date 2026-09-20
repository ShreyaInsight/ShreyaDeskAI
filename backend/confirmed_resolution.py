"""Additive confirmed 3D evaluation. Historical comparison is left unchanged."""
import hashlib
import json
from datetime import datetime
import pandas as pd
from .alternate_resolution import ANCHOR, session_calendar


class IncompleteHistory(ValueError):
    pass


def enabled(config):
    return config.get('strategy','occ') == 'occ' and bool(config.get('use_alternate_resolution')) and config.get('alternate_mode', 'comparison') == 'confirmed'


def strategy_key(config):
    keys=('ma_type','length','offset_sigma','alma_offset','delay','adx_enabled','adx_threshold','adx_auto','adx_lookback','alternate_multiplier','lookback_days','rsi_min','rsi_max','volume_min','volume_max','volume_above_30d_average','pe_filter_operator','pe_filter_value','max_52w_high_distance_pct','max_signal_age_days')
    values = {k:config.get(k) for k in keys}
    # Preserve existing crossover identities when the new filter is disabled.
    if config.get('min_52w_high_distance_pct') is not None:
        values['min_52w_high_distance_pct'] = config['min_52w_high_distance_pct']
    return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()


def prepare(candles, config, current=None):
    from .scanner import IST, prepare_frame
    current=current or datetime.now(IST)
    daily=pd.DataFrame(candles)
    daily['date']=pd.to_datetime(daily['date'],format='ISO8601',utc=True).dt.tz_convert(IST)
    # Quote overlays, including after-hours snapshots, are never final candles.
    # The next day's history refresh supplies the completed exchange candle.
    from .market_calendar import historical_candle_finalized
    daily=daily[(daily['date'].dt.date < current.date()) & daily['date'].dt.date.map(lambda d:historical_candle_finalized(d,current))].sort_values('date').copy()
    if daily.empty:return daily.assign(buy=False, exit=False)
    if daily['date'].dt.date.duplicated().any():raise IncompleteHistory('Duplicate daily session in confirmed OCC history')
    sessions=config.get('_alternate_sessions')
    if sessions is None:sessions=session_calendar(daily['date'].dt.date.min(), current.date())
    sessions=tuple(sorted(set(sessions)))
    indices={day:i for i,day in enumerate(sessions)}
    if ANCHOR not in indices or any(d not in indices for d in daily['date'].dt.date):
        raise IncompleteHistory('Incomplete verified session calendar for confirmed OCC')
    offsets=daily['date'].dt.date.map(lambda d:indices[d]-indices[ANCHOR])
    daily['_block']=offsets//3
    daily['_offset']=offsets%3
    full=[key for key,g in daily.groupby('_block') if g['_offset'].tolist()==[0,1,2]]
    # A missing session inside the history must not silently alter recursive MAs.
    interior=range(int(daily['_block'].min())+1, int(daily['_block'].max()))
    if any(key not in full for key in interior):raise IncompleteHistory('Incomplete interior 3D block in confirmed history')
    daily=daily[daily['_block'].isin(full)].drop(columns=['_offset'])
    if daily.empty:return daily.assign(buy=False, exit=False)
    comparison={**config,'alternate_mode':'comparison','_alternate_sessions':sessions}
    # Reuse the verified aggregation, MA, delay and daily ADX implementation.
    frame=prepare_frame(daily.drop(columns=['_block']).to_dict('records'),comparison)
    if frame.empty:return frame
    frame['_block']=frame['date'].dt.date.map(lambda d:(indices[d]-indices[ANCHOR])//3)
    ends=frame.groupby('_block',sort=True).tail(1)
    buy=(ends['close_ma'].shift(1)<=ends['open_ma'].shift(1)) & (ends['close_ma']>ends['open_ma'])
    sell=(ends['close_ma'].shift(1)>=ends['open_ma'].shift(1)) & (ends['close_ma']<ends['open_ma'])
    if config['adx_enabled']:
        from .scanner import adx
        raw=daily.drop(columns=['_block']).reset_index(drop=True)
        threshold=adx(raw).rolling(config['adx_lookback']).median() if config['adx_auto'] else config['adx_threshold']
        buy &= ends['adx'] > (threshold.loc[ends.index] if isinstance(threshold,pd.Series) else threshold)
    frame['buy']=False;frame['exit']=False
    frame.loc[ends.index,'buy']=buy;frame.loc[ends.index,'exit']=sell
    return frame.drop(columns=['_block'])


def eligibility(event_day, latest_day, sessions, quote, current=None):
    from .scanner import IST
    from .market_cache import day_of
    current=current or datetime.now(IST)
    from .market_calendar import is_trading_day, no_session_reason, previous_trading_day, get_session_hours
    if not is_trading_day(current.date()):return False,no_session_reason(current.date())
    get_session_hours(current.date())
    if event_day!=latest_day:return False,'Crossover is not on the latest completed 3D block'
    if previous_trading_day(current.date())!=event_day:return False,'Crossover is outside its next-session execution window'
    try:
        if not quote or day_of(quote.get('last_trade_time'))!=current.date() or day_of(quote.get('timestamp'))!=current.date():
            return False,'Current-session quote required for confirmed execution'
    except (ValueError,TypeError):return False,'Current-session quote required for confirmed execution'
    return True,'New completed 3D crossover; risk checks still required'
