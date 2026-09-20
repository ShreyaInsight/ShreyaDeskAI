"""Official equity universe and persistent daily market data for the scanner."""
import csv
import fcntl
from contextlib import contextmanager
import io
import json
import math
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import requests

OFFICIAL_URL = 'https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv'
refresh_lock = threading.Lock()


class RateLimit:
    def __init__(self, interval):
        self.interval, self.last, self.lock = interval, 0, threading.Lock()
    def acquire(self):
        with self.lock:
            time.sleep(max(0, self.last + self.interval - time.monotonic()))
            self.last = time.monotonic()

history_limit = RateLimit(.35)
quote_limit = RateLimit(1.05)


def today():
    from .scanner import IST
    return datetime.now(IST).date()


@contextmanager
def db():
    from .scanner import DB_PATH
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript('''
      CREATE TABLE IF NOT EXISTS scanner_market_master (key TEXT PRIMARY KEY, day TEXT NOT NULL, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS scanner_daily_candles (symbol TEXT NOT NULL, day TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(symbol,day));
      CREATE TABLE IF NOT EXISTS scanner_history_state (symbol TEXT PRIMARY KEY, fetched_day TEXT NOT NULL, start_day TEXT NOT NULL, token INTEGER NOT NULL);
    ''')
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def day_of(value):
    import pandas as pd
    stamp = pd.Timestamp(value)
    return (stamp.tz_localize('Asia/Kolkata') if stamp.tzinfo is None else stamp.tz_convert('Asia/Kolkata')).date()


def daily_master(key, fetch):
    day = today().isoformat()
    with db() as c:
        row = c.execute('SELECT payload FROM scanner_market_master WHERE key=? AND day=?',(key,day)).fetchone()
    if row: return json.loads(row['payload'])
    value = fetch()
    with db() as c:
        c.execute('INSERT OR REPLACE INTO scanner_market_master VALUES (?,?,?)',(key,day,json.dumps(value,default=str)))
    return value


def official_equities():
    def fetch():
        response = requests.get(OFFICIAL_URL,headers={'User-Agent':'Mozilla/5.0'},timeout=20)
        response.raise_for_status()
        rows = [{k.strip():v.strip() for k,v in r.items()} for r in csv.DictReader(io.StringIO(response.text))]
        if not rows or not {'SYMBOL','SERIES','NAME OF COMPANY'} <= rows[0].keys():
            raise ValueError('Official NSE equity master schema is invalid')
        return rows
    return daily_master('official_equities',fetch)


def instruments(kite):
    return daily_master('kite_nse',lambda:kite.instruments('NSE'))


def match_equities(rows, official):
    allowed = {(r['SYMBOL'] if r['SERIES']=='EQ' else r['SYMBOL']+'-'+r['SERIES']):r for r in official if r['SERIES'] in {'EQ','BE','BZ'}}
    return {r['tradingsymbol']:r for r in rows if r.get('tradingsymbol') in allowed
            and r.get('exchange')=='NSE' and r.get('segment')=='NSE' and r.get('instrument_type')=='EQ'
            and r.get('instrument_token') and '-SG' not in r['tradingsymbol']
            and not re.search(r'-RE\d*(?:-|$)', r['tradingsymbol'])
            and not re.search(r'\b(GOVT|TBILL|BOND|TREASURY)\b',r.get('name','').upper())}


def equity_universe(kite):
    result = match_equities(instruments(kite),official_equities())
    if not result: raise ValueError('No verified NSE equities matched; refusing unfiltered fallback')
    return result


@contextmanager
def cache_refresh_lock():
    from .scanner import DB_PATH
    with refresh_lock, open(str(DB_PATH)+'.history.lock', 'a') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(lock_file, fcntl.LOCK_UN)


def refresh_history(tokens, start, progress=None):
    """First-run/daily guard; completed candles only. Successful symbols persist."""
    from .main import get_kite
    from .market_calendar import is_trading_day
    current = today()
    if not is_trading_day(current):
        if progress:progress({'stage':'No NSE session today; using cached history','processed':0,'total':0})
        return
    end = current-timedelta(days=1)
    if progress: progress({"stage":"Checking / waiting for daily history cache", "processed":0, "total":len(tokens)})
    with cache_refresh_lock():
        with db() as c:
            states={r['symbol']:dict(r) for r in c.execute('SELECT * FROM scanner_history_state')}
        needed={s:t for s,t in tokens.items() if s not in states or states[s]['fetched_day']!=str(current) or states[s]['start_day']>str(start) or states[s]['token']!=t}
        if not needed: return
        if progress: progress({'stage':'Preparing daily history cache (once per day)','processed':0,'total':len(needed)})
        def fetch(item):
            symbol,token=item; kite=get_kite(); first=start; candles=[]
            while first<=end:
                stop=min(end,first+timedelta(days=1499)); history_limit.acquire()
                candles.extend(kite.historical_data(token,first,stop,'day')); first=stop+timedelta(days=1)
            encoded=[]
            for bar in candles:
                day=day_of(bar['date'])
                if start<=day<current:
                    if any(not math.isfinite(float(bar[k])) or float(bar[k])<=0 for k in ('open','high','low','close')) or not math.isfinite(float(bar['volume'])) or float(bar['volume'])<0:
                        raise ValueError('Invalid historical candle')
                    encoded.append((symbol,str(day),json.dumps(bar,default=str)))
            with db() as c:
                c.execute('DELETE FROM scanner_daily_candles WHERE symbol=?',(symbol,))
                c.executemany('INSERT OR REPLACE INTO scanner_daily_candles VALUES (?,?,?)',encoded)
                c.execute('INSERT OR REPLACE INTO scanner_history_state VALUES (?,?,?,?)',(symbol,str(current),str(start),token))
        failures=[];completed=0
        with ThreadPoolExecutor(max_workers=3) as pool:
            for future in as_completed([pool.submit(fetch,item) for item in needed.items()]):
                try: future.result()
                except Exception: failures.append(True)
                completed+=1
                if progress: progress({'stage':'Preparing daily history cache (once per day)','processed':completed,'total':len(needed),'failed':len(failures)})
        if failures: raise RuntimeError(f'Daily history cache: {len(failures)} symbols failed; successful downloads are saved. Retry to fill gaps.')


def read_histories(symbols, start):
    result={s:[] for s in symbols}
    with db() as c:
        for row in c.execute('SELECT symbol,payload FROM scanner_daily_candles WHERE day>=? ORDER BY symbol,day',(str(start),)):
            if row['symbol'] in result: result[row['symbol']].append(json.loads(row['payload']))
    return result


def live_quotes(kite, symbols, progress=None):
    quotes={}
    for offset in range(0,len(symbols),500):
        quote_limit.acquire()
        quotes.update(kite.quote(['NSE:'+s for s in symbols[offset:offset+500]]))
        if progress: progress({'stage':'Loading batched quotes','processed':min(offset+500,len(symbols)),'total':len(symbols)})
    return quotes


class InvalidQuote(ValueError):
    """A single symbol lacks usable current-session OHLCV."""


def overlay_quote(candles, quote, current=None):
    """Do not use quote ohlc.close: it is yesterday's close, not today's LTP."""
    from .scanner import IST
    now=current or datetime.now(IST)
    result=list(candles)
    from .market_calendar import session_bounds
    bounds=session_bounds(now.date())
    if not bounds or now < bounds[0]: return result
    if not isinstance(quote, dict): raise InvalidQuote('Missing broker quote')
    if not quote.get('last_trade_time'): return result
    try:
        if day_of(quote['last_trade_time'])!=now.date(): return result
        if not quote.get('timestamp') or day_of(quote['timestamp'])!=now.date():
            raise InvalidQuote('Quote timestamp is not current')
    except (ValueError, TypeError, OverflowError) as error:
        raise InvalidQuote('Invalid or stale quote timestamp') from error
    if not isinstance(quote.get('ohlc'), dict):
        raise InvalidQuote('Missing OHLC fields')
    values={k:quote.get('ohlc',{}).get(k) for k in ('open','high','low')}
    values.update(close=quote.get('last_price'),volume=quote.get('volume'))
    for key, value in values.items():
        try:
            if value is None or isinstance(value, bool): raise ValueError()
            value = float(value)
            if not math.isfinite(value) or value < 0 or (key != 'volume' and value == 0): raise ValueError()
        except (ValueError, TypeError, OverflowError) as error:
            raise InvalidQuote(f'Invalid live quote field: {key}') from error
        if isinstance(values[key], str): values[key] = value
    result=[c for c in result if day_of(c['date'])!=now.date()]
    result.append(dict(date=now.replace(hour=0,minute=0,second=0,microsecond=0).isoformat(),**values))
    return result


def warm_full_universe(progress=None):
    from .main import get_kite
    from .scanner import get_config
    from .market_calendar import is_trading_day, get_session_hours
    if not is_trading_day(today()):return
    get_session_hours(today())
    cfg=get_config(); kite=get_kite(); rows=equity_universe(kite)
    # Prepare enough shared raw history for either strategy, regardless of selection.
    # Keep each indicator's own read window unchanged so OCC output is unaffected.
    occ_days=max(cfg['lookback_days'],cfg['adx_lookback'],365)+cfg['length']*4*(3 if cfg.get('use_alternate_resolution') else 1)+30
    from .supertrend import parameters
    supertrend_days=max(cfg['lookback_days'],365)+parameters(cfg)[0]*10
    start=today()-timedelta(days=max(occ_days,supertrend_days))
    tokens={s:r['instrument_token'] for s,r in rows.items()}
    all_tokens={r['tradingsymbol']:r['instrument_token'] for r in instruments(kite)}
    if 'NIFTY 50' in all_tokens:tokens['NIFTY 50']=all_tokens['NIFTY 50']
    refresh_history(tokens,start,progress)
