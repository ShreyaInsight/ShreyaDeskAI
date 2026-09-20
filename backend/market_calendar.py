"""Versioned NSE cash-session calendar. No network access on decision paths.

Unknown years and announced sessions with unpublished hours fail closed.
Install a reviewed NSE calendar update with `python -m backend.market_calendar FILE`.
"""
import json
import time as clock
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
DATA_PATH = Path(__file__).with_name('data') / 'nse_calendar.json'

class CalendarUnavailable(ValueError):
    pass

@lru_cache(maxsize=4)
def _load(path, mtime):
    result=json.loads(Path(path).read_text())
    validate(result)
    return result

_snapshot = None
_checked = 0.0
_snapshot_path = None

def data():
    global _snapshot, _checked, _snapshot_path
    now=clock.monotonic()
    path=str(DATA_PATH)
    if _snapshot is None or _snapshot_path!=path or now-_checked>=1:
        try:
            _snapshot=_load(path,DATA_PATH.stat().st_mtime_ns)
        except (OSError,ValueError) as error:
            raise CalendarUnavailable('NSE calendar unavailable or invalid; restore a verified calendar file') from error
        _snapshot_path=path
        _checked=now
    return _snapshot

def _year(day):
    if day.year not in data()['years']:
        raise CalendarUnavailable(f'NSE calendar does not cover {day.year}; install an official calendar update before trading')

def is_special_session(day):
    _year(day)
    return data()['special_sessions'].get(day.isoformat(), {}).get('label')

def is_trading_day(day):
    _year(day)
    if day.isoformat() in data()['special_sessions']: return True
    return day.weekday() < 5 and day.isoformat() not in data()['holidays']

def get_session_hours(day):
    if not is_trading_day(day): return None
    special = data()['special_sessions'].get(day.isoformat())
    if special:
        if not special['open'] or not special['close']:
            raise CalendarUnavailable(f"{special['label']} on {day}: official session hours not yet recorded; refresh the NSE calendar")
        return time.fromisoformat(special['open']), time.fromisoformat(special['close'])
    return time(9,15), time(15,30)

def session_bounds(day):
    hours=get_session_hours(day)
    return tuple(datetime.combine(day,t,IST) for t in hours) if hours else None

def is_open(current, *, exit_buffer_minutes=0):
    current=current.astimezone(IST)
    bounds=session_bounds(current.date())
    return bool(bounds and bounds[0] <= current < bounds[1]-timedelta(minutes=exit_buffer_minutes))

def is_finalized(day, current, *, delay_minutes=0):
    bounds=session_bounds(day)
    return bool(bounds and current.astimezone(IST) >= bounds[1]+timedelta(minutes=delay_minutes))

def historical_candle_finalized(day, current):
    # Broker candles before published coverage are historical evidence only;
    # they can never authorize present/future execution or supply session hours.
    if day.year < min(data()['years']): return day < current.astimezone(IST).date()
    return is_finalized(day,current)


def next_trading_day(day):
    for _ in range(370):
        day+=timedelta(days=1)
        if is_trading_day(day): return day
    raise CalendarUnavailable('No verified next trading session')

def previous_trading_day(day):
    for _ in range(370):
        day-=timedelta(days=1)
        if is_trading_day(day): return day
    raise CalendarUnavailable('No verified previous trading session')

def no_session_reason(day):
    _year(day)
    label=data()['holidays'].get(day.isoformat(),day.strftime('%A'))
    return f'No trading session today ({label}) — no current-session quote is available'

def sessions(start,end):
    result=[]
    while start<=end:
        if is_trading_day(start): result.append(start)
        start+=timedelta(days=1)
    return tuple(result)

def historical_sessions(start,end,observed=()):
    """Older chart-only dates retain observed benchmark evidence, never weekdays.
    Published years use the official calendar, including sessions missing in cache.
    Unknown future dates are never inferred from stale candles.
    """
    first_year=min(data()['years'])
    result={d for d in observed if d.year<first_year and start<=d<=end}
    result.update(sessions(max(start,date(first_year,1,1)),end))
    return tuple(sorted(result))

def status(current=None):
    current=(current or datetime.now(IST)).astimezone(IST)
    result={'day':current.date().isoformat(),'timezone':'Asia/Kolkata'}
    try:
        snapshot=data()
        result.update(version=snapshot['version'],updated_at=snapshot['updated_at'],covered_years=snapshot['years'],sources=snapshot['sources'])
        trading=is_trading_day(current.date())
        result.update(is_trading_day=trading,special_session=is_special_session(current.date()))
        hours=get_session_hours(current.date())
        result.update(session_hours=[v.isoformat() for v in hours] if hours else None,
                      market_open=is_open(current),error=None,
                      message='Session hours verified' if trading else no_session_reason(current.date()))
    except CalendarUnavailable as error:
        result.update(market_open=False,error=str(error),message=str(error))
    return result


def validate(candidate):
    if not isinstance(candidate,dict):raise ValueError('Calendar must be a JSON object')
    years=candidate.get('years')
    if not isinstance(years,list) or not years or any(type(y) is not int or not 1900<=y<=2200 for y in years):
        raise ValueError('Covered years must be a nonempty list of valid years')
    if any(not isinstance(candidate.get(key),dict) for key in ('holidays','special_sessions')):
        raise ValueError('Holiday and special-session maps are required')
    if not candidate.get('version') or not candidate.get('sources') or not candidate.get('years'):
        raise ValueError('Calendar version, sources and covered years are required')
    date.fromisoformat(candidate['updated_at'])
    if not all(url.startswith('https://nsearchives.nseindia.com/content/circulars/') and url.endswith('.pdf') for url in candidate['sources']):
        raise ValueError('Record official NSE circular URLs as calendar sources')
    for key in ('holidays','special_sessions'):
        for day in candidate[key]:
            if date.fromisoformat(day).year not in candidate['years']: raise ValueError('Calendar date outside coverage')
    for row in candidate['special_sessions'].values():
        if not row.get('label'): raise ValueError('Special session label is required')
        if (row['open'] is None)!=(row['close'] is None):raise ValueError('Both session times are required')
        if row['open'] is not None and not time.fromisoformat(row['open'])<time.fromisoformat(row['close']):
            raise ValueError('Invalid session hours')


def install(path):
    """Manual, local administrator refresh from reviewed official NSE circulars."""
    candidate=json.loads(Path(path).read_text())
    validate(candidate)
    if DATA_PATH.exists() and not set(data()['years']).issubset(candidate['years']):
        raise ValueError('Retain historical calendar coverage when installing an update')
    temporary=DATA_PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(candidate,indent=2)+'\n');temporary.replace(DATA_PATH)
    _load.cache_clear()
    global _snapshot
    _snapshot=None

if __name__=='__main__':
    import sys
    install(sys.argv[1])
    print('NSE calendar installed:',data()['version'])
