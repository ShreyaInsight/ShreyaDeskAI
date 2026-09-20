from __future__ import annotations

import json
import math
import re
import sqlite3
from time import perf_counter
from datetime import date, datetime, time, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler

from .rsi import daily_rsi, passes_rsi_filter
from .main import ROOT_DIR, get_kite, index_symbols, instrument_tokens, nifty100_symbols

from .runtime_paths import SCANNER_DB_PATH
DB_PATH = SCANNER_DB_PATH
IST = timezone(timedelta(hours=5, minutes=30), name="IST")
fundamentals_executor = ThreadPoolExecutor(max_workers=16)
fundamentals_cache: dict[str, tuple[datetime, dict[str, float | None]]] = {}
FUNDAMENTALS_CACHE_TTL = timedelta(hours=6)
DEFAULT_CONFIG: dict[str, Any] = {
    "strategy": "occ", "st_atr_length": 10, "st_factor": 3.0,
    "index_name": "NIFTY100",
    "universe_mode": "index",
    "rsi_min": None, "rsi_max": None,
    "volume_min": None, "volume_max": None, "volume_above_30d_average": False,
    "use_alternate_resolution": False, "alternate_multiplier": 3, "alternate_mode": "comparison",
    "ma_type": "SMMA", "length": 5, "offset_sigma": 6, "alma_offset": 0.85,
    "delay": 0, "adx_enabled": False, "adx_threshold": 20.0, "adx_auto": False,
    "adx_lookback": 200, "sl_pct": 2.0, "tsl_activation_pct": 2.0,
    "tsl_pct": 1.5, "tp_pct": 5.0, "session_filter": True,
    "pe_filter_operator": "none", "pe_filter_value": None,
    "max_52w_high_distance_pct": None,
    "min_52w_high_distance_pct": None,
    "max_signal_age_days": None,
    "watchlist": [], "lookback_days": 365,
}


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE IF NOT EXISTS scanner_config (id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS scanner_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, ran_at TEXT NOT NULL, payload TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS scanner_signals (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, signal_type TEXT NOT NULL, trigger_date TEXT NOT NULL, payload TEXT NOT NULL, run_id INTEGER NOT NULL)")
    connection.commit()
    return connection


def get_config() -> dict[str, Any]:
    connection = db()
    row = connection.execute("SELECT payload FROM scanner_config WHERE id = 1").fetchone()
    connection.close()
    if not row or row["payload"] is None:
        config = {**DEFAULT_CONFIG, "watchlist": list(index_symbols(DEFAULT_CONFIG["index_name"]).keys())}
        save_config(config)
        return config
    config = {**DEFAULT_CONFIG, **json.loads(row["payload"])}
    config.pop("pb_filter_operator", None)
    config.pop("pb_filter_value", None)
    return config


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    from .autotrade import execution_lock
    with execution_lock:
        return _save_config(config)


def _save_config(config: dict[str, Any]) -> dict[str, Any]:
    merged = {**DEFAULT_CONFIG, **config}
    if merged['strategy'] not in {'occ','supertrend'}:
        raise ValueError('Strategy must be OCC or Supertrend')
    from .supertrend import parameters
    parameters(merged)
    if merged["pe_filter_operator"] not in {"none", "above", "below"}:
        raise ValueError("P/E filter must be none, above, or below.")
    if merged["alternate_mode"] not in {"comparison", "confirmed"}:
        raise ValueError("Alternate mode must be comparison or confirmed")
    if merged["alternate_multiplier"] != 3:
        raise ValueError("Only the verified alternate multiplier 3 is supported")
    if merged['universe_mode'] not in {'index', 'full_nse'}:
        raise ValueError('Universe must be index or full_nse')
    for key in ('volume_min', 'volume_max'):
        value = merged[key]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
            raise ValueError('Volume bounds must be finite non-negative numbers')
    if merged['volume_min'] is not None and merged['volume_max'] is not None and merged['volume_min'] > merged['volume_max']:
        raise ValueError('Minimum volume cannot exceed maximum volume')
    for key in ('rsi_min', 'rsi_max'):
        value = merged[key]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100):
            raise ValueError('RSI bounds must be finite numbers between 0 and 100')
    if merged['rsi_min'] is not None and merged['rsi_max'] is not None and merged['rsi_min'] > merged['rsi_max']:
        raise ValueError('Minimum RSI cannot exceed maximum RSI')
    for key in ('min_52w_high_distance_pct', 'max_52w_high_distance_pct'):
        value = merged[key]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100):
            raise ValueError('52W distance bounds must be finite numbers between 0 and 100')
    if merged['min_52w_high_distance_pct'] is not None and merged['max_52w_high_distance_pct'] is not None and merged['min_52w_high_distance_pct'] > merged['max_52w_high_distance_pct']:
        raise ValueError('Minimum 52W distance cannot exceed maximum distance')
    connection = db()
    previous = connection.execute("SELECT payload FROM scanner_config WHERE id = 1").fetchone()
    previous_config = json.loads(previous["payload"]) if previous else {}
    if merged["universe_mode"] == "index" and (merged["index_name"] != previous_config.get("index_name") or previous_config.get("universe_mode", "index") != "index" or not merged["watchlist"]):
        merged["watchlist"] = list(index_symbols(merged["index_name"]).keys())
    merged.pop("pb_filter_operator", None)
    merged.pop("pb_filter_value", None)
    connection.execute("INSERT INTO scanner_config (id, payload) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (json.dumps(merged),))
    connection.commit()
    connection.close()
    return merged


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length).mean()


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def wma(series: pd.Series, length: int) -> pd.Series:
    weights = np.arange(1, length + 1, dtype=float)
    return series.rolling(length).apply(lambda values: float(np.dot(values, weights) / weights.sum()), raw=True)


def smma(series: pd.Series, length: int) -> pd.Series:
    values = series.to_numpy(dtype=float)
    output = np.full(len(values), np.nan)
    if len(values) < length:
        return pd.Series(output, index=series.index)
    output[length - 1] = np.mean(values[:length])
    for index in range(length, len(values)):
        output[index] = (output[index - 1] * (length - 1) + values[index]) / length
    return pd.Series(output, index=series.index)


def alma(series: pd.Series, length: int, offset: float, sigma: float) -> pd.Series:
    center = offset * (length - 1)
    scale = length / sigma
    weights = np.array([math.exp(-((index - center) ** 2) / (2 * scale * scale)) for index in range(length)])
    weights /= weights.sum()
    return series.rolling(length).apply(lambda values: float(np.dot(values, weights)), raw=True)


def lsma(series: pd.Series, length: int, offset: int) -> pd.Series:
    x = np.arange(length, dtype=float)
    x_mean = x.mean()
    denominator = ((x - x_mean) ** 2).sum()
    def calculate(values: np.ndarray) -> float:
        slope = ((x - x_mean) * (values - values.mean())).sum() / denominator
        return float(values.mean() + slope * ((length - 1) + offset - x_mean))
    return series.rolling(length).apply(calculate, raw=True)


def ssma(series: pd.Series, length: int) -> pd.Series:
    values = series.to_numpy(dtype=float)
    output = np.full(len(values), np.nan)
    a1 = math.exp(-1.414 * math.pi / length)
    b1 = 2 * a1 * math.cos(1.414 * math.pi / length)
    c2, c3 = b1, -a1 * a1
    c1 = 1 - c2 - c3
    for index in range(len(values)):
        if index == 0:
            output[index] = values[index]
        elif index == 1:
            output[index] = c1 * (values[index] + values[index - 1]) / 2 + c2 * output[index - 1]
        else:
            output[index] = c1 * (values[index] + values[index - 1]) / 2 + c2 * output[index - 1] + c3 * output[index - 2]
    return pd.Series(output, index=series.index)


def moving_average(series: pd.Series, ma_type: str, length: int, offset_sigma: int, alma_offset: float, volume: pd.Series | None = None) -> pd.Series:
    ma_type = ma_type.upper()
    if ma_type == "SMA": return sma(series, length)
    if ma_type == "EMA": return ema(series, length)
    if ma_type == "DEMA":
        first = ema(series, length); return 2 * first - ema(first, length)
    if ma_type == "TEMA":
        first = ema(series, length); second = ema(first, length); return 3 * (first - second) + ema(second, length)
    if ma_type == "WMA": return wma(series, length)
    if ma_type == "VWMA":
        if volume is None: raise ValueError("VWMA requires candle volume")
        return (series * volume).rolling(length).sum() / volume.rolling(length).sum()
    if ma_type == "SMMA": return smma(series, length)
    if ma_type == "HullMA": return wma(2 * wma(series, max(1, length // 2)) - wma(series, length), max(1, round(math.sqrt(length))))
    if ma_type == "LSMA": return lsma(series, length, offset_sigma)
    if ma_type == "ALMA": return alma(series, length, alma_offset, max(1, offset_sigma))
    if ma_type == "SSMA": return ssma(series, length)
    if ma_type == "TMA": return sma(sma(series, math.ceil(length / 2)), math.floor(length / 2) + 1)
    raise ValueError(f"Unsupported MA type: {ma_type}")


def adx(frame: pd.DataFrame, length: int = 14) -> pd.Series:
    high, low, close = frame["high"], frame["low"], frame["close"]
    true_range = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    up = high.diff(); down = -low.diff()
    plus = pd.Series(np.where((up > down) & (up > 0), up, 0), index=frame.index)
    minus = pd.Series(np.where((down > up) & (down > 0), down, 0), index=frame.index)
    atr = true_range.ewm(alpha=1 / length, adjust=False).mean()
    plus_di = 100 * plus.ewm(alpha=1 / length, adjust=False).mean() / atr
    minus_di = 100 * minus.ewm(alpha=1 / length, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / length, adjust=False).mean()


def prepare_frame(candles: list[dict[str, Any]], config: dict[str, Any]) -> pd.DataFrame:
    from .confirmed_resolution import enabled, prepare
    if enabled(config):
        return prepare(candles, config)
    frame = pd.DataFrame(candles)
    frame["date"] = pd.to_datetime(frame["date"], format="ISO8601", utc=True).dt.tz_convert(IST)
    frame["close_ma"] = moving_average(frame["close"], config["ma_type"], config["length"], config["offset_sigma"], config["alma_offset"], frame.get("volume"))
    frame["open_ma"] = moving_average(frame["open"], config["ma_type"], config["length"], config["offset_sigma"], config["alma_offset"], frame.get("volume"))
    if config["delay"]:
        frame["close_ma"] = frame["close_ma"].shift(config["delay"])
        frame["open_ma"] = frame["open_ma"].shift(config["delay"])
    if config.get("use_alternate_resolution", False):
        from .alternate_resolution import alternate_series
        frame["open_ma"], frame["close_ma"] = alternate_series(frame, config, moving_average)
    frame["adx"] = adx(frame)
    frame["buy"] = (frame["close_ma"].shift(1) <= frame["open_ma"].shift(1)) & (frame["close_ma"] > frame["open_ma"])
    frame["exit"] = (frame["close_ma"].shift(1) >= frame["open_ma"].shift(1)) & (frame["close_ma"] < frame["open_ma"])
    if config["adx_enabled"]:
        threshold = frame["adx"].rolling(config["adx_lookback"]).median() if config["adx_auto"] else config["adx_threshold"]
        frame["buy"] &= frame["adx"] > threshold
    return frame.dropna(subset=["close_ma", "open_ma"])


def risk_levels(entry: float, highs: pd.Series, config: dict[str, Any]) -> dict[str, Any]:
    fixed_stop = entry * (1 - config["sl_pct"] / 100)
    activation = entry * (1 + config["tsl_activation_pct"] / 100)
    trail = fixed_stop
    active = False
    for high in highs:
        if high >= activation:
            active = True
        if active:
            trail = max(trail, float(high) * (1 - config["tsl_pct"] / 100))
    return {"stop_loss": round(fixed_stop, 2), "tsl_activation": round(activation, 2), "trailing_stop": round(trail, 2) if active else None, "tsl_active": active, "take_profit": round(entry * (1 + config["tp_pct"] / 100), 2)}


def scan_universe(config, kite):
    if config.get('universe_mode', 'index') == 'full_nse':
        from .market_cache import equity_universe
        equities = equity_universe(kite)
        companies = {symbol: item.get('name') or symbol for symbol, item in equities.items()}
        return companies, {symbol: item['instrument_token'] for symbol, item in equities.items()}, sorted(equities)
    companies = index_symbols(config['index_name'])
    return companies, instrument_tokens(kite), config['watchlist'] or list(companies)


def volume_metrics(candles):
    def valid(value):
        try:
            value = float(value)
            return value if math.isfinite(value) and value >= 0 else None
        except (TypeError, ValueError):
            return None
    current = valid(candles[-1].get('volume')) if candles else None
    previous = [valid(c.get('volume')) for c in candles[-31:-1]]
    average = sum(previous)/30 if len(previous) == 30 and all(v is not None for v in previous) else None
    return {'current_volume': current, 'previous_30d_avg_volume': average}


def passes_volume_filters(metrics, config):
    low, high = config.get('volume_min'), config.get('volume_max')
    relative = config.get('volume_above_30d_average', False)
    if low is None and high is None and not relative:
        return True
    current = metrics['current_volume']
    if current is None or (low is not None and current < low) or (high is not None and current > high):
        return False
    average = metrics['previous_30d_avg_volume']
    return not relative or (average is not None and current > average)


def run_scan(progress=None, *, execute=True) -> dict[str, Any]:
    if get_config().get('strategy','occ') == 'supertrend':
        from .supertrend_scan import run
        return run(progress, execute=execute)
    scan_started_at = perf_counter()
    if progress: progress({"stage": "Loading universe", "processed": 0, "total": 0})
    config = get_config(); kite = get_kite()
    from .confirmed_resolution import enabled as confirmed_enabled, eligibility, strategy_key, IncompleteHistory
    confirmed = confirmed_enabled(config)
    companies, tokens, watchlist = scan_universe(config, kite)
    from .market_cache import warm_full_universe, refresh_history, read_histories, live_quotes, overlay_quote, InvalidQuote
    from_date = datetime.now(IST).date() - timedelta(days=max(config["lookback_days"], config["adx_lookback"], 365) + config["length"] * 4 * (3 if config.get("use_alternate_resolution") else 1) + 30)
    warm_full_universe(progress)
    refresh_history({symbol:tokens[symbol] for symbol in watchlist if symbol in tokens}, from_date, progress)
    histories = read_histories(watchlist, from_date)
    quotes = live_quotes(kite, watchlist, progress)
    if config.get("use_alternate_resolution"):
        from .alternate_resolution import session_calendar
        config["_alternate_sessions"] = session_calendar(from_date, datetime.now(IST).date())
    run_started = datetime.now(IST).isoformat(); results = []; history = []; fetched = []; skipped_quotes = []; confirmed_market = {}
    for processed, symbol in enumerate(watchlist, 1):
        if symbol not in tokens: continue
        try:
            if 'NSE:'+symbol not in quotes:
                raise InvalidQuote('Missing broker quote')
            candles = overlay_quote(histories[symbol], quotes.get('NSE:'+symbol))
        except InvalidQuote as error:
            skipped_quotes.append({'symbol': symbol, 'reason': str(error)})
            if progress:
                progress({'stage': 'Calculating cached candles and filters', 'processed': processed, 'total': len(watchlist), 'skipped_quotes': len(skipped_quotes)})
            continue
        if not candles: continue
        metrics = volume_metrics(candles)
        rsi_value = daily_rsi(candles)
        if passes_volume_filters(metrics, config) and passes_rsi_filter(rsi_value, config):
            try:
                frame = prepare_frame(candles, config)
            except IncompleteHistory as error:
                skipped_quotes.append({'symbol':symbol,'reason':str(error)})
                continue
            if not frame.empty:
                frame.attrs.update(metrics)
                frame.attrs.update(rsi_14_1d=rsi_value, rsi_date=pd.Timestamp(candles[-1]["date"]).date().isoformat())
                if confirmed: confirmed_market[symbol] = pd.DataFrame(candles)
                fetched.append((symbol, frame))
        if progress and (processed % 50 == 0 or processed == len(watchlist)):
            progress({"stage":"Calculating cached candles and filters", "processed":processed, "total":len(watchlist)})

    if skipped_quotes and len(skipped_quotes) == sum(symbol in tokens for symbol in watchlist):
        raise RuntimeError('No usable broker quotes; previous results preserved. Retry when quote data is available.')

    for symbol, frame in fetched:
        last_signal = frame[(frame["buy"]) | (frame["exit"])].tail(1)
        if last_signal.empty:
            continue
        event = last_signal.iloc[0]; signal_type = "BUY" if bool(event["buy"]) else "EXIT"
        entry = float(event["close"]); levels = risk_levels(entry, frame.loc[frame.index >= event.name, "high"], config)
        current_price = float(frame.iloc[-1]["close"])
        week_52_high = float(frame["high"].tail(252).max())
        result = {**frame.attrs, "volume_date": frame.iloc[-1]["date"].strftime("%Y-%m-%d"), "comparison_only": bool(config.get("use_alternate_resolution")) and not confirmed, "calculation_timeframe": "3D" if config.get("use_alternate_resolution") else "1D", "symbol": symbol, "company": companies.get(symbol, symbol), "signal_type": signal_type, "trigger_date": event["date"].strftime("%Y-%m-%d"), "trigger_price": round(entry, 2), "current_price": round(current_price, 2), "avg_volume_30d": round(float(frame["volume"].tail(30).mean()), 0) if "volume" in frame else None, "pe_ratio": None, "week_52_high": round(week_52_high, 2), "week_52_high_distance_pct": round((week_52_high - current_price) / week_52_high * 100, 2) if week_52_high else None, "change_1d_pct": round((current_price / float(frame.iloc[-2]["close"]) - 1) * 100, 2) if len(frame) > 1 else None, "change_1m_pct": round((current_price / float(frame.iloc[-22]["close"]) - 1) * 100, 2) if len(frame) > 21 else None, "change_1y_pct": round((current_price / float(frame.iloc[-253]["close"]) - 1) * 100, 2) if len(frame) > 252 else None, "ma_close": round(float(event["close_ma"]), 2), "ma_open": round(float(event["open_ma"]), 2), **levels}
        if confirmed:
            event_day = event['date'].date()
            eligible, reason = eligibility(event_day, frame.iloc[-1]['date'].date(), config['_alternate_sessions'], quotes.get('NSE:'+symbol))
            result.update(occ_mode='confirmed_3d', confirmed_block_end=event_day.isoformat(), confirmed_eligible=eligible,
                          confirmed_reason=reason, strategy_key=strategy_key(config), execution_session=datetime.now(IST).date().isoformat())
            # Display the fresh market price; the completed candle remains trigger_price.
            market = confirmed_market[symbol]
            fresh_price = float(market.iloc[-1]['close'])
            high_52 = float(market['high'].tail(252).max())
            result.update(current_price=round(fresh_price, 2), volume_date=pd.Timestamp(market.iloc[-1]['date']).date().isoformat(),
                          week_52_high=round(high_52, 2), week_52_high_distance_pct=round((high_52-fresh_price)/high_52*100, 2) if high_52 else None,
                          avg_volume_30d=round(float(market['volume'].tail(30).mean()), 0))
            for field, offset in [('change_1d_pct', 2), ('change_1m_pct', 22), ('change_1y_pct', 253)]:
                result[field] = round((fresh_price/float(market.iloc[-offset]['close'])-1)*100, 2) if len(market)>=offset else None
        max_signal_age = config.get("max_signal_age_days")
        if max_signal_age is not None and (date.today() - event["date"].date()).days > max_signal_age:
            continue
        history.append(result); results.append(result)

    history = results.copy()
    fundamentals_required = config.get("pe_filter_operator") not in (None, "none") or config.get("max_52w_high_distance_pct") is not None or config.get("min_52w_high_distance_pct") is not None
    if fundamentals_required:
        enrich_results_fundamentals(results)
    else:
        for result in results:
            previous = previous_fundamentals(result["symbol"])
            result["pe_ratio"] = previous["pe_ratio"]
    results = [result for result in results if passes_fundamental_filters(result, config)]
    history = results.copy()
    results.sort(key=lambda item: item["trigger_date"], reverse=True)
    config.pop("_alternate_sessions", None)
    connection = db(); cursor = connection.execute("INSERT INTO scanner_runs (ran_at, payload) VALUES (?, ?)", (run_started, json.dumps({"config": config, "count": len(results)}))); run_id = cursor.lastrowid
    connection.executemany("INSERT INTO scanner_signals (symbol, signal_type, trigger_date, payload, run_id) VALUES (?, ?, ?, ?, ?)", [(item["symbol"], item["signal_type"], item["trigger_date"], json.dumps(item), run_id) for item in history]); connection.commit(); connection.close()
    from .autotrade import process_signals
    if execute and (not config.get("use_alternate_resolution") or confirmed):
        process_signals(results, run_id)
    elif execute:
        from .analytics import audit_skip
        from .autotrade import get_config as execution_config
        mode = execution_config()['mode']
        for result in results:
            audit_skip(result['symbol'], 'BUY' if result['signal_type']=='BUY' else 'SELL', mode, 'Historical comparison only; execution blocked', run_id)
    if not fundamentals_required:
        fundamentals_executor.submit(enrich_run_fundamentals, run_id, results)
    run_time = datetime.now(IST).isoformat()
    duration_seconds = round(perf_counter() - scan_started_at, 2)
    connection = db(); connection.execute("UPDATE scanner_runs SET ran_at=?, payload=? WHERE id=?", (run_time, json.dumps({"config": config, "count": len(results), "duration_seconds": duration_seconds, "skipped_quotes": skipped_quotes}), run_id)); connection.commit(); connection.close()
    return {"run_id": run_id, "ran_at": run_time, "results": results, "scanned": len(watchlist), "returned": len(results), "fundamentals_pending": False, "duration_seconds": duration_seconds, "skipped_quotes": skipped_quotes}


def latest_results() -> list[dict[str, Any]]:
    from .strategies import results_snapshot
    return results_snapshot()['results']


def latest_run() -> dict[str, Any] | None:
    from .strategies import results_snapshot
    return results_snapshot()['last_run']


def symbol_history(symbol: str) -> list[dict[str, Any]]:
    from .strategies import name, matches
    config=get_config(); connection=db()
    rows=connection.execute('SELECT s.payload,r.payload AS run_payload FROM scanner_signals s JOIN scanner_runs r ON r.id=s.run_id WHERE upper(s.symbol)=? ORDER BY s.trigger_date DESC',(symbol.upper(),)).fetchall()
    connection.close()
    return [json.loads(r['payload']) for r in rows if matches(json.loads(r['run_payload']).get('config',{}),config) and json.loads(r['payload']).get('strategy','occ')==name(config)]


def nse_pe(symbol: str) -> float | None:
    try:
        response = requests.get(
            "https://www.nseindia.com/api/quote-equity",
            params={"symbol": symbol},
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": "https://www.nseindia.com/"},
            timeout=8,
        )
        data = response.json()
        value = data.get("metadata", {}).get("pdSymbolPe") or data.get("info", {}).get("pdSymbolPe")
        return round(float(value), 2) if value not in (None, "-", "") else None
    except (ValueError, TypeError, requests.RequestException):
        return None


def screener_fundamentals(symbol: str, detailed: bool = False) -> dict[str, float | None]:
    cached = fundamentals_cache.get(symbol.upper())
    if cached and datetime.now(timezone.utc) - cached[0] < FUNDAMENTALS_CACHE_TTL:
        if not detailed or '_ratios' in cached[1]:
            return cached[1]
    from .market_calendar import is_trading_day
    if not detailed and not is_trading_day(datetime.now(IST).date()):
        return cached[1] if cached else {'eps':None,**previous_fundamentals(symbol)}
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "text/html"}
    for suffix in ("/consolidated/", "/"):
        try:
            response = requests.get(f"https://www.screener.in/company/{symbol}{suffix}", headers=headers, timeout=4)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            ratios: dict[str, float | None] = {}
            for item in soup.select("#top-ratios li"):
                name = item.select_one(".name")
                value = item.select_one(".value .number")
                if not name or not value:
                    continue
                raw = value.get_text(" ", strip=True).replace(",", "")
                try:
                    ratios[name.get_text(" ", strip=True)] = float(raw) if raw not in {"", "-", "—"} else None
                except ValueError:
                    ratios[name.get_text(" ", strip=True)] = None

            if detailed and ratios:
                result = {"eps": ratios.get('EPS'), "pe_ratio": ratios.get('Stock P/E'), '_ratios': ratios, '_status': 'SUCCESS'}
                # Search owns its durable detailed cache; avoid changing scanner cache semantics.
                return result
            stock_pe = ratios.get("Stock P/E")
            if stock_pe is not None:
                result = {"eps": None, "pe_ratio": stock_pe or nse_pe(symbol)}
                fundamentals_cache[symbol.upper()] = (datetime.now(timezone.utc), result)
                return result
        except (ValueError, TypeError, requests.RequestException):
            continue
    if detailed:
        return {'eps': None, 'pe_ratio': None, '_ratios': {}, '_status': 'FAILED'}
    return {"eps": None, "pe_ratio": nse_pe(symbol)}


def previous_fundamentals(symbol: str) -> dict[str, float | None]:
    connection = db()
    rows = connection.execute("SELECT payload FROM scanner_signals WHERE upper(symbol)=? ORDER BY id DESC LIMIT 25", (symbol.upper(),)).fetchall()
    connection.close()
    for row in rows:
        payload = json.loads(row["payload"])
        if payload.get("pe_ratio") is not None:
            return {"pe_ratio": payload.get("pe_ratio")}
    return {"pe_ratio": None}


def fundamental_pe(symbol: str, current_price: float) -> float | None:
    return screener_fundamentals(symbol)["pe_ratio"]


def enrich_results_fundamentals(results: list[dict[str, Any]]) -> None:
    def enrich(result: dict[str, Any]) -> dict[str, Any]:
        fundamentals = screener_fundamentals(result["symbol"])
        result["pe_ratio"] = fundamentals["pe_ratio"]
        if result["pe_ratio"] is None:
            previous = previous_fundamentals(result["symbol"])
            result["pe_ratio"] = previous["pe_ratio"]
        return result

    list(fundamentals_executor.map(enrich, results))


def passes_fundamental_filters(result: dict[str, Any], config: dict[str, Any]) -> bool:
    for field, operator_key, value_key in (("pe_ratio", "pe_filter_operator", "pe_filter_value"),):
        operator, value = config.get(operator_key, "none"), config.get(value_key)
        if operator == "none" or value is None:
            continue
        metric = result.get(field)
        if metric is None or (operator == "above" and metric <= value) or (operator == "below" and metric >= value):
            return False
    maximum_distance = config.get("max_52w_high_distance_pct")
    minimum_distance = config.get("min_52w_high_distance_pct")
    distance = result.get("week_52_high_distance_pct")
    if minimum_distance is None and maximum_distance is None:
        return True
    return distance is not None and math.isfinite(distance) and (minimum_distance is None or distance >= minimum_distance) and (maximum_distance is None or distance <= maximum_distance)


def enrich_run_fundamentals(run_id: int, results: list[dict[str, Any]]) -> None:
    for result in results:
        try:
            fundamentals = screener_fundamentals(result["symbol"])
            pe_ratio = fundamentals["pe_ratio"]
            connection = db()
            row = connection.execute("SELECT payload FROM scanner_signals WHERE run_id=? AND symbol=? ORDER BY id DESC LIMIT 1", (run_id, result["symbol"])).fetchone()
            if row:
                payload = json.loads(row["payload"])
                if pe_ratio is not None:
                    payload["pe_ratio"] = pe_ratio
                connection.execute("UPDATE scanner_signals SET payload=? WHERE run_id=? AND symbol=?", (json.dumps(payload), run_id, result["symbol"]))
                connection.commit()
            connection.close()
        except Exception:
            continue


def queue_scheduled_scan(execute=False):
    from .main import enqueue_scanner
    from .market_calendar import is_open
    if not is_open(datetime.now(IST),exit_buffer_minutes=5 if execute else 0):return
    return enqueue_scanner('scheduled-1500' if execute else 'scheduled-1200')


scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
from .market_cache import warm_full_universe
scheduler.add_job(warm_full_universe, "cron", hour=9, minute=0, id="occ-history-cache", replace_existing=True, max_instances=1, misfire_grace_time=300, coalesce=True)
scheduler.add_job(queue_scheduled_scan, "cron", hour=12, minute=0, id="scanner-noon", kwargs={'execute':False}, replace_existing=True, max_instances=1, misfire_grace_time=60, coalesce=True)
scheduler.add_job(queue_scheduled_scan, "cron", hour=15, minute=0, id="scanner-afternoon", kwargs={'execute':True}, replace_existing=True, max_instances=1, misfire_grace_time=60, coalesce=True)
if not scheduler.running:
    scheduler.start()
