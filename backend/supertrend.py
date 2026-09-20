"""Pine-compatible Supertrend on finalized daily OHLC, independent of OCC."""
import hashlib
import json
import math
from datetime import datetime, timedelta, time
import pandas as pd


def parameters(config):
    length = config.get('st_atr_length',10)
    factor = config.get('st_factor',3.0)
    if isinstance(length,bool) or not isinstance(length,int) or not 1 <= length <= 200:
        raise ValueError('Supertrend ATR length must be an integer between 1 and 200')
    if isinstance(factor,bool) or not isinstance(factor,(int,float)) or not math.isfinite(factor) or factor <= 0:
        raise ValueError('Supertrend factor must be a finite positive number')
    return length,float(factor)


def calculate(candles, length=10, factor=3.0):
    """TR uses high-low on the first bar; ATR is SMA-seeded Wilder RMA.

    Caller supplies only finalized candles. Strict band crossings match Pine;
    equality does not flip. Initial direction is +1 (downtrend).
    """
    parameters({'st_atr_length':length,'st_factor':factor})
    output=[]; trs=[]; atr=None; upper=None; lower=None; previous_close=None; trend=None; direction=1
    for candle in candles:
        row=dict(candle)
        h,l,c,o = (float(row[k]) for k in ('high','low','close','open'))
        if not all(math.isfinite(v) and v>0 for v in (h,l,c,o)) or not l<=min(o,c)<=max(o,c)<=h:
            raise ValueError('Invalid finalized OHLC candle')
        tr = h-l if previous_close is None else max(h-l,abs(h-previous_close),abs(l-previous_close))
        trs.append(tr)
        prev_atr,prev_upper,prev_lower,prev_trend,prev_direction = atr,upper,lower,trend,direction
        if len(trs)==length: atr=sum(trs)/length
        elif len(trs)>length: atr=(atr*(length-1)+tr)/length
        if atr is not None:
            basic_upper=(h+l)/2+factor*atr; basic_lower=(h+l)/2-factor*atr
            # Pine nz(previous band) uses zero during warm-up.
            pu=prev_upper if prev_upper is not None else 0.0
            pl=prev_lower if prev_lower is not None else 0.0
            upper=basic_upper if basic_upper<pu or (previous_close is not None and previous_close>pu) else pu
            lower=basic_lower if basic_lower>pl or (previous_close is not None and previous_close<pl) else pl
            if prev_atr is None: direction=1
            elif prev_trend==prev_upper: direction=-1 if c>upper else 1
            else: direction=1 if c<lower else -1
            trend=lower if direction<0 else upper
        row.update(open=o,high=h,low=l,close=c)
        row.update(supertrend_value=trend,direction=direction,bullFlip=prev_direction==1 and direction==-1,bearFlip=prev_direction==-1 and direction==1)
        output.append(row); previous_close=c
    return output


def finalized(candles, current=None):
    from .scanner import IST
    now=current or datetime.now(IST)
    now=now.astimezone(IST)
    rows=[]; seen=set()
    from .market_cache import day_of
    for candle in sorted(candles,key=lambda b:day_of(b['date'])):
        stamp=pd.Timestamp(candle['date'])
        day=(stamp.tz_localize(IST) if stamp.tzinfo is None else stamp.tz_convert(IST)).date()
        from .market_calendar import historical_candle_finalized
        if day>now.date() or not historical_candle_finalized(day,now): continue
        if day in seen: raise ValueError('Duplicate daily session in Supertrend history')
        seen.add(day)
        rows.append({**candle,'date':day.isoformat()})
    return rows


def cached_bars(symbol, candles, config):
    from .scanner import db
    length,factor=parameters(config)
    rows=finalized(candles)
    fingerprint=hashlib.sha256(json.dumps([length,factor,rows],sort_keys=True,default=str).encode()).hexdigest()
    conn=db()
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS supertrend_cache(symbol TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,payload TEXT NOT NULL)')
        cached=conn.execute('SELECT payload FROM supertrend_cache WHERE symbol=? AND fingerprint=?',(symbol,fingerprint)).fetchone()
        if cached:return json.loads(cached['payload'])
        bars=calculate(rows,length,factor)
        conn.execute('INSERT OR REPLACE INTO supertrend_cache VALUES (?,?,?)',(symbol,fingerprint,json.dumps(bars,default=str,allow_nan=False)))
        conn.commit()
        return bars
    finally: conn.close()


def refresh_close(tokens, current=None, progress=None):
    """Fetch today's broker daily candle once after close; never quote overlays."""
    from . import market_cache as cache
    from .scanner import IST, get_kite
    now=current or datetime.now(IST)
    from .market_calendar import is_finalized
    if not is_finalized(now.date(),now):return
    day=now.date().isoformat()
    with cache.cache_refresh_lock():
        with cache.db() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS supertrend_close_fetch(symbol TEXT PRIMARY KEY,day TEXT NOT NULL)')
            done={r['symbol']:r['day'] for r in conn.execute('SELECT * FROM supertrend_close_fetch')}
        needed=[(s,t) for s,t in tokens.items() if done.get(s)!=day]
        kite=get_kite()
        for index,(symbol,token) in enumerate(needed,1):
            cache.history_limit.acquire()
            # Exceptions leave this symbol eligible for retry, never marked complete.
            bars=kite.historical_data(token,now.date(),now.date(),'day')
            rows=finalized(bars,now)
            calculate(rows,1,3)  # Validate OHLC before committing to the shared cache.
            with cache.db() as conn:
                for row in rows:
                    if row['date']==day:
                        conn.execute('INSERT OR REPLACE INTO scanner_daily_candles VALUES (?,?,?)',(symbol,day,json.dumps(row,default=str)))
                if any(row['date']==day for row in rows):
                    conn.execute('INSERT OR REPLACE INTO supertrend_close_fetch VALUES (?,?)',(symbol,day))
            if progress:progress({'stage':'Finalizing daily Supertrend candles','processed':index,'total':len(needed)})


def chart(symbol, config=None):
    from .scanner import get_config, IST
    from .market_cache import read_histories
    cfg=config or get_config()
    if cfg.get('strategy','occ')!='supertrend':raise ValueError('Select Supertrend before opening its chart')
    start=datetime.now(IST).date()-timedelta(days=max(cfg['lookback_days'],365)+parameters(cfg)[0]*10)
    bars=cached_bars(symbol,read_histories([symbol],start).get(symbol,[]),cfg)
    return {'symbol':symbol,'strategy':'supertrend','timeframe':'1D','atr_length':parameters(cfg)[0],'factor':parameters(cfg)[1],'bars':bars,'as_of':bars[-1]['date'] if bars else None}
