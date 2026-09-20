from __future__ import annotations

import json
import re
import os
import base64
import hashlib
import hmac
import secrets
import threading
import time
import uuid
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from datetime import datetime, timezone
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from kiteconnect import KiteConnect
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).resolve().parent.parent
from .runtime_paths import ENV_PATH, TOKEN_PATH
load_dotenv(ENV_PATH)

app = FastAPI(title="ShreyaDesk API")
SESSION_TTL = 3600
sessions: dict[str, float] = {}
session_lock = threading.Lock()
rate_limits: dict[str, list[float]] = {}
scanner_jobs: dict[str, dict[str, Any]] = {}
scanner_jobs_lock = threading.Lock()
scanner_executor = ThreadPoolExecutor(max_workers=1)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def start_autotrade_monitor() -> None:
    from .autotrade import startup_safety_reset
    startup_safety_reset()
    try:
        from .telegram_notifications import start
        start()
    except Exception:pass


def _password_matches(password: str) -> bool:
    encoded = os.getenv("SHREYADESK_PASSWORD_HASH", "")
    try:
        salt_text, digest_text = encoded.split("$", 1)
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _rate_limit(key: str, limit: int, window: int) -> None:
    now = time.time()
    values = [item for item in rate_limits.get(key, []) if item > now - window]
    if len(values) >= limit:
        raise HTTPException(status_code=429, detail="Too many requests. Try again later.")
    values.append(now); rate_limits[key] = values


def _session_valid(token: str | None) -> bool:
    if not token:
        return False
    with session_lock:
        expires = sessions.get(token)
        if not expires or expires <= time.time():
            sessions.pop(token, None)
            from .dashboard_sessions import metadata
            metadata.pop(token,None)
            return False
        sessions[token] = time.time() + SESSION_TTL
        return True


def no_cache(response):
    response.headers['Cache-Control']='no-store, no-cache, must-revalidate'
    response.headers['Pragma']='no-cache'
    response.headers['Expires']='0'
    return response


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    from . import dashboard_sessions as ds
    path=request.url.path
    # Legacy scanner aliases must enforce the same permissions as /api/scanner.
    if path.startswith('/scanner/'): path='/api'+path
    token=request.cookies.get('shreyadesk_session')
    try:
        if path.startswith('/api/'):
            if path not in {'/api/health','/api/login'}:
                if not _session_valid(token):
                    raise HTTPException(status_code=401,detail='Authentication required.')
                if request.method in {'POST','PUT','PATCH','DELETE'} and (not request.headers.get('X-CSRF-Token') or request.headers.get('X-CSRF-Token')!=request.cookies.get('shreyadesk_csrf')):
                    raise HTTPException(status_code=403,detail='CSRF validation failed.')
                with session_lock:
                    if token in ds.metadata: ds.metadata[token]['last_seen']=ds.stamp()
                if ds.pending(token) and path not in {'/api/session','/api/kite/login','/api/kite/callback','/api/logout','/api/sessions','/api/sessions/revoke-all','/api/broker-account','/api/broker-account/switch'}:
                    raise HTTPException(status_code=403,detail='Complete Kite login for this dashboard session first.')
            if request.method=='POST' and path in {'/api/login','/api/scanner/run','/api/autotrade/enable','/api/autotrade/pause'}:
                _rate_limit(f"{request.client.host}:{path}",5 if path=='/api/login' else 10,60)
        response=await call_next(request)
    except HTTPException as error:
        response=Response(content=json.dumps({'detail':error.detail}),status_code=error.status_code,media_type='application/json')
    return no_cache(response)


class LoginRequest(BaseModel):
    state: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    api_secret: str = Field(min_length=1)
    request_token: str = Field(min_length=1)


class DashboardLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class SignalsRequest(BaseModel):
    short_sma: int = Field(default=6, ge=2, le=200)
    long_sma: int = Field(default=30, ge=3, le=400)
    lookback_days: int = Field(default=90, ge=2, le=3650)
    max_stocks: int = Field(default=25, ge=1, le=100)


class ScannerConfigRequest(BaseModel):
    strategy: str = Field(default="occ", pattern="^(occ|supertrend)$")
    st_atr_length: int = Field(default=10, ge=1, le=200)
    st_factor: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    rsi_min: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    rsi_max: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    universe_mode: str = Field(default='index', pattern='^(index|full_nse)$')
    volume_min: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    volume_max: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    volume_above_30d_average: bool = False
    use_alternate_resolution: bool = False
    alternate_multiplier: int = Field(default=3, ge=3, le=3)
    alternate_mode: str = Field(default="comparison", pattern="^(comparison|confirmed)$")
    index_name: str = Field(default="NIFTY100")
    ma_type: str = Field(default="SMMA")
    length: int = Field(default=5, ge=2, le=200)
    offset_sigma: int = Field(default=6, ge=0, le=100)
    alma_offset: float = Field(default=0.85, ge=0, le=1)
    delay: int = Field(default=0, ge=0, le=20)
    adx_enabled: bool = False
    adx_threshold: float = Field(default=20, ge=0, le=100)
    adx_auto: bool = False
    adx_lookback: int = Field(default=200, ge=20, le=1000)
    sl_pct: float = Field(default=2, ge=0, le=100)
    tsl_activation_pct: float = Field(default=2, ge=0, le=100)
    tsl_pct: float = Field(default=1.5, ge=0, le=100)
    tp_pct: float = Field(default=5, ge=0, le=500)
    session_filter: bool = True
    pe_filter_operator: str = Field(default="none", pattern="^(none|above|below)$")
    pe_filter_value: float | None = Field(default=None, ge=0)
    min_52w_high_distance_pct: float | None = Field(default=None, ge=0, le=100)
    max_52w_high_distance_pct: float | None = Field(default=None, ge=0, le=100)
    max_signal_age_days: int | None = Field(default=None, ge=1, le=3650)
    watchlist: list[str] = Field(default_factory=list)
    lookback_days: int = Field(default=365, ge=30, le=3650)


class AutoTradeEnableRequest(BaseModel):
    mode: str
    confirm_phrase: str = ""


class AutoTradeCloseRequest(BaseModel):
    confirm_phrase: str = ""


INDEX_CSV_URLS = {
    "NIFTY50": "ind_nifty50list.csv",
    "NIFTYNEXT50": "ind_niftynext50list.csv",
    "NIFTY100": "ind_nifty100list.csv",
    "NIFTYNEXT100": "ind_niftyNext100_list.csv",
    "NIFTY200": "ind_nifty200list.csv",
    "NIFTYTOTALMARKET": "ind_niftytotalmarket_list.csv",
    "NIFTY500": "ind_nifty500list.csv",
    "NIFTY500MULTICAP502525": "ind_nifty500multicap502525_list.csv",
    "NIFTY500LARGEMIDSMALLEQUAL": "ind_nifty500LargeMidSmallEqualCapWeighted_list.csv",
    "NIFTYMIDCAP150": "ind_niftymidcap150list.csv",
    "NIFTYMIDCAP50": "ind_niftymidcap50list.csv",
    "NIFTYMIDCAPSELECT": "ind_niftymidcapselect_list.csv",
    "NIFTYMIDCAP100": "ind_niftymidcap100list.csv",
    "NIFTYSMALLCAP500": "ind_NiftySmallcap500_list.csv",
    "NIFTYSMALLCAP250": "ind_niftysmallcap250list.csv",
    "NIFTYSMALLCAP50": "ind_niftysmallcap50list.csv",
    "NIFTYSMALLCAP100": "ind_niftysmallcap100list.csv",
    "NIFTYMICROCAP250": "ind_niftymicrocap250_list.csv",
    "NIFTYLARGEMIDCAP250": "ind_niftylargemidcap250list.csv",
    "NIFTYMIDSMALLCAP400": "ind_niftymidsmallcap400list.csv",
    "NIFTYMIDSMALLCAP4005050": "ind_niftymidsmallcap4005050_list.csv",
    "NIFTYINDIAFPI150": "ind_niftyIndiaFPI150_list.csv",
}


def read_saved_session() -> dict[str, Any] | None:
    if not TOKEN_PATH.exists():
        return None
    try:
        return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_session(session: dict[str, Any]) -> None:
    TOKEN_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path = TOKEN_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(session), encoding="utf-8")
    temporary_path.chmod(0o600)
    temporary_path.replace(TOKEN_PATH)


def get_kite() -> KiteConnect:
    session = read_saved_session()
    if not session or not session.get("api_key") or not session.get("access_token"):
        raise HTTPException(status_code=401, detail="Connect your Kite account to continue.")

    kite = KiteConnect(api_key=session["api_key"])
    kite.set_access_token(session["access_token"])
    return kite


@lru_cache(maxsize=1)
def nifty100_symbols() -> dict[str, str]:
    return index_symbols("NIFTY100")


@lru_cache(maxsize=3)
def index_symbols(index_name: str) -> dict[str, str]:
    normalized_index = index_name.upper()
    if normalized_index == "INDIAVIX":
        return {"INDIAVIX": "India VIX"}
    if normalized_index not in INDEX_CSV_URLS:
        raise ValueError("Unsupported NSE index universe.")
    response = requests.get(
        f"https://www.niftyindices.com/IndexConstituent/{INDEX_CSV_URLS[normalized_index]}",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"},
        timeout=20,
    )
    response.raise_for_status()
    constituents = pd.read_csv(StringIO(response.text))
    symbol_column = next((column for column in constituents.columns if column.strip().lower() == "symbol"), None)
    company_column = next((column for column in constituents.columns if "company" in column.lower()), None)
    if not symbol_column or not company_column:
        raise ValueError("The Nifty 100 CSV format has changed.")
    return {
        str(row[symbol_column]).strip().upper(): str(row[company_column]).strip()
        for _, row in constituents.iterrows()
        if pd.notna(row[symbol_column])
    }


def instrument_tokens(kite: KiteConnect) -> dict[str, int]:
    from .market_cache import instruments
    return {
        item["tradingsymbol"].upper(): item["instrument_token"]
        for item in instruments(kite)
        if item.get("tradingsymbol") and item.get("instrument_token")
    }


def crossover_signal(candles: list[dict[str, Any]], short_window: int, long_window: int, lookback_days: int) -> dict[str, Any] | None:
    if len(candles) < long_window + 1:
        return None
    frame = pd.DataFrame(candles)
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    frame["short_sma"] = frame["close"].rolling(short_window).mean()
    frame["long_sma"] = frame["close"].rolling(long_window).mean()
    frame = frame.dropna(subset=["short_sma", "long_sma"]).copy()
    frame["previous_short"] = frame["short_sma"].shift(1)
    frame["previous_long"] = frame["long_sma"].shift(1)
    frame["type"] = None
    bullish = (frame["previous_short"] <= frame["previous_long"]) & (frame["short_sma"] > frame["long_sma"])
    bearish = (frame["previous_short"] >= frame["previous_long"]) & (frame["short_sma"] < frame["long_sma"])
    frame.loc[bullish, "type"] = "Bullish"
    frame.loc[bearish, "type"] = "Bearish"
    cutoff = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=lookback_days)
    matches = frame[(frame["type"].notna()) & (frame["date"] >= cutoff)]
    if matches.empty:
        return None
    latest = matches.iloc[-1]
    return {
        "crossover_type": latest["type"],
        "crossover_date": latest["date"].strftime("%Y-%m-%d"),
        "close": round(float(latest["close"]), 2),
        "short_sma": round(float(latest["short_sma"]), 2),
        "long_sma": round(float(latest["long_sma"]), 2),
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/session")
def session_status(request: Request) -> dict[str, Any]:
    from .dashboard_sessions import pending
    session = read_saved_session()
    waiting = pending(request.cookies.get('shreyadesk_session'))
    from .dashboard_sessions import account_change
    change=account_change(request.cookies.get('shreyadesk_session'))
    connected = False
    error = None
    if session and session.get('access_token'):
        try:
            # Verify with Kite, not just the presence of a token file.
            get_kite().profile()
            connected = True
        except Exception:
            error = 'Kite connection could not be verified. Reconnect or retry.'
    return {'account_change':change,'authenticated':True,'connected':connected and not waiting,'shared_connected':connected,
            'kite_pending':waiting,'connection_error':error,
            'connected_at':session.get('connected_at') if session else None}



@app.post("/api/login")
def dashboard_login(payload: DashboardLoginRequest, response: Response, request: Request) -> dict[str, bool]:
    if payload.username != os.getenv("SHREYADESK_USERNAME", "") or not _password_matches(payload.password):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    session_token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    previous=request.cookies.get('shreyadesk_session')
    from . import dashboard_sessions as ds
    with session_lock:
        sessions.pop(previous,None)
        ds.metadata.pop(previous,None)
    with session_lock:
        sessions[session_token] = time.time() + SESSION_TTL
    ds.create(session_token,request)
    response.set_cookie("shreyadesk_session", session_token, max_age=SESSION_TTL, httponly=True, secure=True, samesite="lax")
    response.set_cookie("shreyadesk_csrf", csrf_token, max_age=SESSION_TTL, httponly=False, secure=True, samesite="strict")
    return {"authenticated": True}


@app.post("/api/kite/login")
def kite_login(payload: LoginRequest, request: Request) -> dict[str, Any]:
    from . import dashboard_sessions as ds
    token=ds.consume(request,payload.state)
    try:
        kite = KiteConnect(api_key=payload.api_key)
        session = kite.generate_session(payload.request_token, api_secret=payload.api_secret)
        access_token = session.get("access_token")
        if not access_token:
            raise HTTPException(status_code=502, detail="Kite did not return an access token.")
        kite.set_access_token(access_token)
        from .broker_identity import profile_id
        verified_account=profile_id(kite)
        accepted=ds.complete(token,request,
            {
                "account_id": verified_account,
                "api_key": payload.api_key,
                "access_token": access_token,
                "connected_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return {"connected": accepted, "account_change_pending": not accepted}
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Kite login failed: {error}") from error

@app.get("/api/kite/login")
def kite_login_redirect(request: Request) -> RedirectResponse:
    api_key = os.getenv("KITE_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="KITE_API_KEY is not configured on the server.")
    from .dashboard_sessions import begin
    state=begin(request)
    return RedirectResponse(url=f"https://kite.trade/connect/login?api_key={quote(api_key)}&v=3&redirect_params={quote('state='+state,safe='')}", status_code=307)


@app.get("/api/kite/callback")
def kite_callback(request: Request, state: str | None = Query(default=None), request_token: str | None = Query(default=None), status: str | None = Query(default=None)) -> RedirectResponse:
    from . import dashboard_sessions as ds
    token=ds.consume(request,state)
    frontend_origin = os.getenv("FRONTEND_ORIGIN", "").rstrip("/")
    if status != "success" or not request_token:
        raise HTTPException(status_code=400, detail="Kite login was cancelled or did not return a request token.")
    api_key = os.getenv("KITE_API_KEY", "").strip()
    api_secret = os.getenv("KITE_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise HTTPException(status_code=503, detail="KITE_API_KEY and KITE_API_SECRET must be configured on the server.")
    try:
        kite = KiteConnect(api_key=api_key)
        session = kite.generate_session(request_token, api_secret=api_secret)
        access_token = session.get("access_token")
        if not access_token:
            raise HTTPException(status_code=502, detail="Kite did not return an access token.")
        kite.set_access_token(access_token)
        from .broker_identity import profile_id
        verified_account=profile_id(kite)
        ds.complete(token,request,{"account_id": verified_account, "api_key": api_key, "access_token": access_token, "connected_at": datetime.now(timezone.utc).isoformat()})
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Kite callback exchange failed: {error}") from error
    from .autotrade import scheduler, reconcile_gtt_positions
    scheduler.add_job(reconcile_gtt_positions, "date", id="gtt-login", replace_existing=True)
    return RedirectResponse(url=f"{frontend_origin}/", status_code=302)


@app.get("/api/profile")
def profile() -> dict[str, Any]:
    try:
        return get_kite().profile()
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Unable to load profile: {error}") from error


@app.post("/api/signals")
def signals(payload: SignalsRequest) -> dict[str, Any]:
    if payload.short_sma >= payload.long_sma:
        raise HTTPException(status_code=422, detail="Short SMA must be smaller than long SMA.")
    try:
        kite = get_kite()
        companies = nifty100_symbols()
        tokens = instrument_tokens(kite)
        from_date = date.today() - timedelta(days=payload.lookback_days + payload.long_sma + 15)
        to_date = date.today()
        results: list[dict[str, Any]] = []
        matched_instruments = 0
        candle_series = 0
        failed_symbols = 0
        latest_candle_date: str | None = None
        for symbol, company in companies.items():
            token = tokens.get(symbol)
            if not token:
                continue
            matched_instruments += 1
            try:
                candles = kite.historical_data(token, from_date, to_date, "day")
                if candles:
                    candle_series += 1
                    latest_candle_date = max(
                        latest_candle_date or "",
                        str(candles[-1]["date"])[:10],
                    )
                signal = crossover_signal(candles, payload.short_sma, payload.long_sma, payload.lookback_days)
                if signal:
                    results.append({"ticker": symbol, "company": company, **signal})
            except Exception:
                failed_symbols += 1
                continue
        results.sort(key=lambda item: item["crossover_date"], reverse=True)
        results = results[: payload.max_stocks]
        for rank, result in enumerate(results, start=1):
            result["rank"] = rank
        return {
            "results": results,
            "scanned": len(companies),
            "returned": len(results),
            "matched_instruments": matched_instruments,
            "candle_series": candle_series,
            "failed_symbols": failed_symbols,
            "latest_candle_date": latest_candle_date,
            "parameters": payload.model_dump(),
        }
    except HTTPException:
        raise
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=f"Unable to load the official Nifty 100 list: {error}") from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Unable to generate signals: {error}") from error


@app.get("/api/scanner/config")
@app.get("/scanner/config")
def scanner_config() -> dict[str, Any]:
    from .scanner import get_config
    return get_config()


@app.post("/api/scanner/config")
@app.post("/scanner/config")
def update_scanner_config(payload: ScannerConfigRequest) -> dict[str, Any]:
    from .scanner import save_config
    if payload.index_name not in {*INDEX_CSV_URLS, "INDIAVIX"}:
        raise HTTPException(status_code=422, detail="Unsupported NSE index universe.")
    allowed_ma_types = {"SMA", "EMA", "DEMA", "TEMA", "WMA", "VWMA", "SMMA", "HullMA", "LSMA", "ALMA", "SSMA", "TMA"}
    if payload.ma_type not in allowed_ma_types:
        raise HTTPException(status_code=422, detail=f"MA type must be one of: {', '.join(sorted(allowed_ma_types))}")
    if payload.watchlist and any(not symbol.strip() for symbol in payload.watchlist):
        raise HTTPException(status_code=422, detail="Watchlist symbols cannot be empty.")
    try:
        return save_config(payload.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))


def _run_scanner_job(job_id: str) -> None:
    from .scanner import run_scan
    try:
        with scanner_jobs_lock:
            scanner_jobs[job_id]["status"] = "running"
        def progress(values):
            with scanner_jobs_lock:
                scanner_jobs[job_id].update(values)
        source = scanner_jobs[job_id].get('source', 'manual')
        from datetime import datetime
        from .scanner import IST
        now = datetime.now(IST)
        from .market_calendar import is_open
        execute = source == 'manual' or (source == 'scheduled-1500' and scanner_jobs[job_id]['day'] == now.date().isoformat() and now.hour == 15 and is_open(now,exit_buffer_minutes=5))
        result = run_scan(progress=progress, execute=execute)
        try:
            from .telegram_notifications import emit
            emit('Scanner completed',f"{source}: {result.get('returned',len(result.get('results',[])))} signals; {len(result.get('skipped_quotes',[]))} excluded quotes",category='scanner',key='scan:'+job_id)
        except Exception:pass
        with scanner_jobs_lock:
            scanner_jobs[job_id].update({"status": "completed", "result": result})
    except Exception as error:
        with scanner_jobs_lock:
            scanner_jobs[job_id].update({"status": "failed", "error": f"Unable to run scanner: {error}"})


@app.post("/api/scanner/run", status_code=202)
@app.post("/scanner/run", status_code=202)
def run_scanner() -> dict[str, Any]:
    return enqueue_scanner('manual')


def enqueue_scanner(source='manual') -> dict[str, Any]:
    if source not in {'manual','scheduled-1200','scheduled-1500'}:
        raise ValueError('Invalid scan source')
    from .scanner import IST
    with scanner_jobs_lock:
        active = next((job for job in scanner_jobs.values() if job["status"] in {"queued", "running"} and job.get("source", "manual") == source), None)
        if active:
            return {"job_id": active["job_id"], "status": active["status"]}
        job_id = uuid.uuid4().hex
        scanner_jobs[job_id] = {"job_id": job_id, "status": "queued", "source": source, "day": datetime.now(IST).date().isoformat()}
    scanner_executor.submit(_run_scanner_job, job_id)
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/scanner/run/{job_id}")
@app.get("/scanner/run/{job_id}")
def scanner_run_status(job_id: str) -> dict[str, Any]:
    with scanner_jobs_lock:
        job = scanner_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Scanner job not found.")
    return dict(job)


@app.get("/api/scanner/results")
@app.get("/scanner/results")
def scanner_results() -> dict[str, Any]:
    from .strategies import results_snapshot
    return results_snapshot()


@app.get("/api/scanner/results/{symbol}")
@app.get("/scanner/results/{symbol}")
def scanner_symbol_results(symbol: str) -> dict[str, Any]:
    from .scanner import symbol_history
    return {"symbol": symbol.upper(), "results": symbol_history(symbol)}


@app.get("/api/autotrade/config")
def autotrade_config() -> dict[str, Any]:
    from .autotrade import get_config
    return get_config()


@app.post("/api/autotrade/config")
def update_autotrade_config(payload: dict[str, Any]) -> dict[str, Any]:
    from .autotrade import save_config
    try:
        return save_config(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/autotrade/enable")
def enable_autotrade(payload: AutoTradeEnableRequest) -> dict[str, Any]:
    from .autotrade import set_enabled, start_live_monitor
    try:
        result = set_enabled(payload.mode, payload.confirm_phrase)
        if payload.mode == "live":
            start_live_monitor()
        return result
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/autotrade/pause")
def pause_autotrade() -> dict[str, Any]:
    from .autotrade import pause
    return pause()


@app.post("/api/autotrade/kill-switch")
def trigger_autotrade_kill_switch() -> dict[str, Any]:
    from .risk import latch
    return latch(manual=True)


@app.post("/api/autotrade/kill-switch/clear")
def clear_autotrade_kill_switch(payload: AutoTradeCloseRequest) -> dict[str, Any]:
    from .risk import clear
    try:
        return clear(payload.confirm_phrase)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


class AppTradeDayPrice(BaseModel):
    position_id: int = Field(gt=0, strict=True)
    price: float = Field(gt=0, allow_inf_nan=False)
    model_config = {"extra": "forbid"}


class AppPnlReconciliationRequest(BaseModel):
    day: date
    prices: list[AppTradeDayPrice]
    confirm_phrase: str
    model_config = {"extra": "forbid"}


@app.get("/api/autotrade/risk/history-positions")
def app_history_positions(day: date) -> dict[str, Any]:
    from .risk import history_positions
    return {"positions": history_positions(day.isoformat())}


@app.post("/api/autotrade/risk/reconcile-day")
def reconcile_app_pnl(payload: AppPnlReconciliationRequest) -> dict[str, Any]:
    from .risk import reconcile_day
    try:
        return reconcile_day(payload.day.isoformat(), [item.model_dump() for item in payload.prices], payload.confirm_phrase)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/autotrade/positions")
def autotrade_positions() -> dict[str, Any]:
    from .autotrade import positions
    return {"positions": positions()}


@app.get("/api/autotrade/orders")
def autotrade_orders(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    from .autotrade import orders
    return {"orders": orders(limit)}


@app.get("/api/autotrade/open-orders")
def autotrade_open_orders() -> dict[str, Any]:
    from .autotrade import open_orders
    return {"orders": open_orders()}


@app.get("/api/autotrade/stats")
def autotrade_stats() -> dict[str, Any]:
    from .autotrade import stats
    return stats()


@app.post("/api/autotrade/positions/{position_id}/close")
def close_autotrade_position(position_id: int, payload: AutoTradeCloseRequest) -> dict[str, Any]:
    from .autotrade import close_position
    try:
        return close_position(position_id, payload.confirm_phrase)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/autotrade/backtest/run")
def run_autotrade_backtest(payload: dict[str, Any]) -> dict[str, Any]:
    from .autotrade import backtest
    return backtest(payload)


@app.post("/api/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    token = request.cookies.get("shreyadesk_session")
    from . import dashboard_sessions as ds
    ds.audit(token,'dashboard_logout',request)
    with session_lock:
        sessions.pop(token, None)
        ds.metadata.pop(token,None)
    response.delete_cookie("shreyadesk_session")
    response.delete_cookie("shreyadesk_csrf")
    try:
        TOKEN_PATH.unlink(missing_ok=True)
    except OSError as error:
        raise HTTPException(status_code=500, detail="Could not clear the saved session.") from error
    try:
        from .telegram_notifications import emit
        emit('Kite disconnected','Saved Kite session cleared by dashboard logout.',category='connection',key='kite:disconnected')
    except Exception:pass
    return {"connected": False}


@app.post("/api/sessions/revoke-all")
def revoke_all_sessions() -> dict[str, bool]:
    from .dashboard_sessions import metadata
    with session_lock:
        sessions.clear()
        metadata.clear()
    return {"revoked": True}


@app.get('/api/holdings')
def holdings_view():
    from .holdings import current_holdings
    try:
        return current_holdings(get_kite())
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail='Unable to load holdings. Check your Kite connection and retry.')


@app.get('/api/holdings/signal')
def holding_signal(symbol: str = Query(..., min_length=1, max_length=40, pattern=r'^[A-Za-z0-9&_.-]+$'), exchange: str = Query('NSE', pattern=r'^(NSE|BSE)$')):
    from .holdings import strategy_signal
    try:
        return strategy_signal(get_kite(), symbol.upper(), exchange)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail='Strategy signal unavailable. Retry after checking your historical-data access.')


@app.get("/api/analytics")
def analytics_snapshot(mode: str = Query(default="live", pattern="^(live|paper)$")) -> dict[str, Any]:
    from .analytics import snapshot
    return snapshot(mode)


class GTTReconcileRequest(BaseModel):
    confirm_phrase: str
    trigger_id: int | None = Field(default=None, gt=0)


@app.post('/api/autotrade/positions/{position_id}/gtt/reconcile')
def reconcile_position_gtt(position_id: int, payload: GTTReconcileRequest):
    from .gtt import resolve
    try:
        return resolve(position_id, payload.trigger_id, payload.confirm_phrase)
    except Exception as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get('/api/sessions')
def session_activity(request: Request):
    from .dashboard_sessions import activity
    return activity(request)


@app.get('/api/overview')
def overview():
    from .overview import snapshot
    return snapshot()


@app.get('/api/scanner/chart/{symbol}')
def scanner_chart(symbol: str, strategy: str = Query('supertrend',pattern='^supertrend$')):
    if not re.fullmatch(r'[A-Za-z0-9&_.-]{1,40}',symbol):
        raise HTTPException(status_code=422,detail='Invalid symbol')
    from .supertrend import chart
    try:return chart(symbol.upper())
    except ValueError as error:raise HTTPException(status_code=409,detail=str(error)) from error


@app.post('/api/autotrade/apply-existing-protection')
def apply_existing_protection(payload: dict[str, Any]):
    from .gtt import apply_existing
    try:
        return apply_existing(payload.get('confirm_phrase'))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

@app.get('/api/search/symbols')
def search_symbols(q: str = Query('', max_length=80)):
    from .search import symbols
    return {'results':symbols(q)}

@app.get('/api/search/weights')
def search_weights():
    from .search import weights
    return weights()

@app.post('/api/search/weights')
def save_search_weights(payload: dict[str, Any]):
    from .search import weights
    try:return weights(payload)
    except ValueError as e:raise HTTPException(status_code=422,detail=str(e)) from e

@app.get('/api/search/{symbol}')
def search_stock(symbol: str):
    import re
    from .search import analyse
    if not re.fullmatch(r'[A-Z0-9&_.-]{1,40}',symbol):raise HTTPException(status_code=422,detail='Invalid NSE symbol')
    try:return analyse(symbol)
    except ValueError as e:raise HTTPException(status_code=422,detail=str(e)) from e

@app.get('/api/telegram')
def telegram_status():
    from .telegram_notifications import public
    return public()

@app.post('/api/telegram/settings')
def telegram_settings(payload: dict[str, Any]):
    from .telegram_notifications import configure
    try:return configure(payload)
    except ValueError as e:raise HTTPException(status_code=422,detail=str(e)) from e

@app.post('/api/telegram/test')
def telegram_test():
    from .telegram_notifications import emit
    return {'queued':emit('Test notification','Test only: Telegram notifications are connected. No orders or safety actions were performed.',key='test:'+datetime.now(timezone.utc).isoformat(),category='service')}


@app.get('/api/broker-account')
def broker_account_status():
    from .broker_identity import public
    return public()

@app.post('/api/broker-account/bind-legacy')
def broker_bind_legacy(payload: dict[str, Any]):
    from .broker_identity import bind_legacy
    try:return bind_legacy(payload.get('account_id',''),payload.get('confirm_phrase',''))
    except ValueError as error:raise HTTPException(status_code=409,detail=str(error)) from error

@app.post('/api/broker-account/switch')
def broker_account_switch(payload: dict[str, Any], request: Request):
    from .dashboard_sessions import resolve_account_change
    try:return resolve_account_change(request,payload.get('action'),payload.get('confirm_phrase',''))
    except ValueError as error:raise HTTPException(status_code=409,detail=str(error)) from error


@app.get('/api/market-calendar')
def market_calendar_status():
    from .market_calendar import status
    return status()
