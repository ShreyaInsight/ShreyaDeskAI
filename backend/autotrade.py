from __future__ import annotations

import json
import math
import sqlite3
import threading
from functools import wraps
from datetime import datetime, time, timedelta, timezone
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

from .main import ROOT_DIR, get_kite

from .runtime_paths import SCANNER_DB_PATH
DB_PATH = SCANNER_DB_PATH
IST = timezone(timedelta(hours=5, minutes=30), name="IST")
LIVE_CONFIRMATION = "ENABLE LIVE TRADING"
TOTAL_CAPITAL = 20000.0
DEFAULT_CONFIG: dict[str, Any] = {
    "mode": "paper", "per_trade_pct": 2.0, "paper_max_positions": 10,
    "paper_max_deployed_pct": 50.0, "paper_cash": 100000.0,
    "max_symbol_value": 1000.0, "max_concurrent_positions": 20,
    "max_daily_order_count": 5, "max_cumulative_loss": 2500.0,
    "sl_pct": 2.0, "tp_pct": 5.0, "tsl_activation_pct": 2.0, "tsl_pct": 1.5,
    "exit_rule": "whichever_first", "live_order_type": "LIMIT", "chase_orders": False,
    "enabled": False, "paused": True, "limits_version": 2,
}
RULE_KEYS = ('sl_pct', 'tp_pct', 'tsl_activation_pct', 'tsl_pct', 'exit_rule')
for _mode in ('paper', 'live'):
    for _key in RULE_KEYS:
        DEFAULT_CONFIG[f'{_mode}_{_key}'] = DEFAULT_CONFIG[_key]


def config_for_mode(config, mode):
    return {**config, **{key: config.get(f'{mode}_{key}', config[key]) for key in RULE_KEYS}}


HARD_LIMITS = ("max_symbol_value", "max_concurrent_positions", "max_daily_order_count", "max_cumulative_loss")
execution_lock = threading.RLock()


def serialized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with execution_lock:
            return function(*args, **kwargs)
    return wrapped


def validate_limits(config, require_positive=True):
    for key in HARD_LIMITS:
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be a finite positive number")
        if key in {"max_daily_order_count", "max_concurrent_positions"} and int(value) != value:
            raise ValueError(f"{key} must be an integer")
    if config["max_symbol_value"] * config["max_concurrent_positions"] > TOTAL_CAPITAL:
        raise ValueError("Symbol ceiling × position limit cannot exceed ₹20,000")


def startup_safety_reset():
    # Reset authority before any scheduler can execute. Read-only P&L collection
    # may continue while paused, so the cumulative history survives restarts.
    from . import risk
    risk.migrate()
    result = pause()
    if not scheduler.running:
        scheduler.start()
    scheduler.add_job(reconcile_gtt_positions, "date", id="gtt-startup", replace_existing=True)
    return result


def now() -> str: return datetime.now(IST).isoformat()


def connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS autotrade_config (id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS autotrade_positions (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL, entry_time TEXT NOT NULL, entry_price REAL NOT NULL, quantity INTEGER NOT NULL, current_sl REAL NOT NULL, current_tp REAL NOT NULL, tsl_activated INTEGER NOT NULL DEFAULT 0, exit_time TEXT, exit_price REAL, exit_reason TEXT, kite_order_id_entry TEXT, kite_order_id_exit TEXT, realized_pnl REAL, scan_run_id INTEGER, UNIQUE(symbol, mode, entry_time))")
    conn.execute("CREATE TABLE IF NOT EXISTS autotrade_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, symbol TEXT NOT NULL, signal_source TEXT NOT NULL, mode TEXT NOT NULL, order_type TEXT NOT NULL, requested_qty INTEGER NOT NULL, requested_price REAL, status TEXT NOT NULL, kite_order_id TEXT, fill_price REAL, fill_time TEXT, error_message TEXT, position_id INTEGER, scan_run_id INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS autotrade_backtest_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, params TEXT NOT NULL, created_at TEXT NOT NULL, summary TEXT NOT NULL, equity_curve TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS autotrade_backtest_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, payload TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS autotrade_buy_reservations (order_id INTEGER PRIMARY KEY, trading_day TEXT NOT NULL, FOREIGN KEY(order_id) REFERENCES autotrade_orders(id))")
    conn.execute("CREATE TABLE IF NOT EXISTS analytics_skips (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, symbol TEXT NOT NULL, action TEXT NOT NULL, mode TEXT NOT NULL, reason TEXT NOT NULL, scan_run_id INTEGER, position_id INTEGER)")
    if 'filled_quantity' not in {r[1] for r in conn.execute('PRAGMA table_info(autotrade_orders)')}:
        try: conn.execute('ALTER TABLE autotrade_orders ADD COLUMN filled_quantity INTEGER')
        except sqlite3.OperationalError as error:
            if 'duplicate column' not in str(error).lower(): raise
    conn.execute("CREATE TABLE IF NOT EXISTS autotrade_confirmed_events (symbol TEXT NOT NULL, mode TEXT NOT NULL, side TEXT NOT NULL, block_end TEXT NOT NULL, order_id INTEGER, processed_at TEXT NOT NULL, PRIMARY KEY(symbol,mode,side,block_end))")
    for table, additions in {
        'autotrade_orders': {'accounted_quantity':'INTEGER NOT NULL DEFAULT 0','accounted_value':'REAL NOT NULL DEFAULT 0'},
        'autotrade_positions': {'sold_quantity':'INTEGER NOT NULL DEFAULT 0','sold_value':'REAL NOT NULL DEFAULT 0'},
    }.items():
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        for name, definition in additions.items():
            if name not in columns:
                try: conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
                except sqlite3.OperationalError as error:
                    if 'duplicate column' not in str(error).lower(): raise
    conn.execute("CREATE TABLE IF NOT EXISTS autotrade_gtts(position_id INTEGER PRIMARY KEY,trigger_id TEXT,state TEXT NOT NULL,quantity INTEGER,sl REAL,tp REAL,attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT,request_json TEXT,expires_at TEXT,updated_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS autotrade_gtt_modifications(position_id INTEGER PRIMARY KEY,payload TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS autotrade_gtt_events(id INTEGER PRIMARY KEY,position_id INTEGER NOT NULL,trigger_id TEXT,event TEXT NOT NULL,message TEXT NOT NULL,timestamp TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS autotrade_fill_events(id INTEGER PRIMARY KEY,order_id INTEGER NOT NULL,position_id INTEGER NOT NULL,side TEXT NOT NULL,quantity INTEGER NOT NULL,value REAL NOT NULL,timestamp TEXT NOT NULL)")
    for table in ('autotrade_positions', 'autotrade_orders', 'autotrade_gtts'):
        if 'account_id' not in {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}:
            try: conn.execute(f'ALTER TABLE {table} ADD COLUMN account_id TEXT')
            except sqlite3.OperationalError as error:
                if 'duplicate column' not in str(error).lower(): raise
    conn.execute('CREATE TABLE IF NOT EXISTS broker_identity_state(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS broker_identity_audit(id INTEGER PRIMARY KEY,event TEXT NOT NULL,account_id TEXT,details TEXT NOT NULL,timestamp TEXT NOT NULL)')
    conn.commit(); return conn


def get_config() -> dict[str, Any]:
    from . import risk
    conn = connection()
    row = conn.execute("SELECT payload FROM autotrade_config WHERE id=1").fetchone()
    conn.close()
    stored = json.loads(row["payload"]) if row else {}
    if stored.get("limits_version") != 2:
        # Migrate old allocations without resetting history or execution latches.
        stored["paper_max_deployed_pct"] = stored.get("max_deployed_pct", 50.0)
        stored["paper_max_positions"] = stored.get("max_concurrent_positions", 10)
        stored.update({key: DEFAULT_CONFIG[key] for key in HARD_LIMITS})
        stored.update(enabled=False, paused=True, limits_version=2,
                      live_order_type="LIMIT", chase_orders=False)
    config = {**DEFAULT_CONFIG, **{key: value for key, value in stored.items() if key in DEFAULT_CONFIG}}
    for mode in ('paper', 'live'):
        for key in RULE_KEYS:
            config[f'{mode}_{key}'] = stored.get(f'{mode}_{key}', stored.get(key, DEFAULT_CONFIG[key]))
    config = config_for_mode(config, config['mode'])
    return {**config, **risk.public_state(), "total_capital": TOTAL_CAPITAL}


@serialized
def save_config(payload: dict[str, Any], *, internal: bool = False) -> dict[str, Any]:
    previous = get_config()
    config = {key: previous[key] for key in DEFAULT_CONFIG}
    config.update({key: value for key, value in payload.items() if key in DEFAULT_CONFIG})
    target = payload.get('settings_mode', payload.get('mode', previous['mode']))
    if target not in {'paper', 'live'}:
        raise ValueError('Invalid settings mode')
    # External writes may modify only the requested mode, including legacy clients.
    if not internal:
        other = 'live' if target == 'paper' else 'paper'
        for key in RULE_KEYS:
            config[f'{other}_{key}'] = previous[f'{other}_{key}']
            config[f'{target}_{key}'] = payload.get(f'{target}_{key}', payload.get(key, previous[f'{target}_{key}']))
        protected = HARD_LIMITS + ('live_order_type', 'chase_orders') if target == 'paper' else ('per_trade_pct', 'paper_max_positions', 'paper_max_deployed_pct', 'paper_cash')
        for key in protected:
            config[key] = previous[key]
    if 'settings_mode' in payload:
        config['mode'] = previous['mode']
    config = config_for_mode(config, config['mode'])
    config["limits_version"] = 2
    if not internal:
        config.update(enabled=False, paused=True)
    validate_limits(config)
    if config["mode"] not in {"paper", "live"}:
        raise ValueError("mode must be paper or live")
    if config["exit_rule"] not in {"scanner_exit", "risk_levels_only", "whichever_first"}:
        raise ValueError("Invalid exit rule")
    if config["live_order_type"] != "LIMIT" or config["chase_orders"] is not False:
        raise ValueError("Live orders require LIMIT with chasing disabled")
    for key in ("enabled", "paused"):
        if type(config[key]) is not bool:
            raise ValueError(f"{key} must be boolean")
    for key in ("per_trade_pct", "paper_max_deployed_pct", "paper_cash", "paper_max_positions", "sl_pct", "tp_pct", "tsl_activation_pct", "tsl_pct"):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid {key}")
    for mode in ('paper', 'live'):
        if config[f'{mode}_exit_rule'] not in {'scanner_exit', 'risk_levels_only', 'whichever_first'}:
            raise ValueError('Invalid exit rule')
        for key in RULE_KEYS[:-1]:
            value = config[f'{mode}_{key}']
            maximum = 500 if key == 'tp_pct' else 100
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
                raise ValueError(f'Invalid {mode}_{key}')
    conn = connection()
    conn.execute("INSERT INTO autotrade_config (id,payload,updated_at) VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at", (json.dumps(config, allow_nan=False), now()))
    conn.commit()
    conn.close()
    return get_config()


def _market_is_open() -> bool:
    from .market_calendar import is_open
    return is_open(datetime.now(IST), exit_buffer_minutes=5)


def _live_preflight(require_margin: bool = True) -> float:
    if not _market_is_open(): raise ValueError("Live orders require an open NSE session and stop five minutes before its close.")
    kite = get_kite(); kite.profile(); margin = kite.margins().get("equity", {})
    if not margin.get("enabled", True): raise ValueError("Kite equity trading is not enabled for this account.")
    available = float(margin.get("available", {}).get("live_balance", 0))
    if not math.isfinite(available) or (require_margin and available <= 0): raise ValueError("No available equity margin for a live order.")
    return available


@serialized
def set_enabled(mode: str, confirm_phrase: str) -> dict[str, Any]:
    from . import risk
    config = get_config()
    if mode not in {"paper", "live"}:
        raise ValueError("mode must be paper or live")
    if config["kill_switch"]:
        raise ValueError("Kill switch is latched; use the typed Clear kill switch action")
    if mode == "live":
        if confirm_phrase != LIVE_CONFIRMATION:
            raise ValueError(f'Type exactly "{LIVE_CONFIRMATION}" to enable live trading.')
        validate_limits(config)
        _live_preflight()
        reconcile_pending_orders()
        risk.check(get_kite(), block_buy=True)
    config.update(mode=mode, enabled=True, paused=False)
    return save_config(config, internal=True)


@serialized
def pause() -> dict[str, Any]:
    config = get_config(); config.update({"paused": True, "enabled": False}); return save_config(config, internal=True)


def order_log(symbol: str, source: str, mode: str, quantity: int, price: float | None, status: str, error: str | None = None, position_id: int | None = None, run_id: int | None = None, order_type: str = "MARKET") -> int:
    conn = connection(); cursor = conn.execute("INSERT INTO autotrade_orders (timestamp, symbol, signal_source, mode, order_type, requested_qty, requested_price, status, error_message, position_id, scan_run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (now(), symbol, source, mode, order_type, quantity, price, status, error, position_id, run_id)); conn.commit(); order_id = cursor.lastrowid; conn.close()
    try:
        from .telegram_notifications import emit
        if status in {'FILLED','FAILED','REJECTED','BLOCKED'}:emit('Order '+status,order_id=order_id,stage=status,key=f'order:{order_id}:{status}')
    except Exception:pass
    return order_id


def _update_order(order_id: int, **values: Any) -> None:
    if not values: return
    assignments = ", ".join(f"{key}=?" for key in values); conn = connection(); conn.execute(f"UPDATE autotrade_orders SET {assignments} WHERE id=?", (*values.values(), order_id)); conn.commit(); conn.close()
    try:
        from .telegram_notifications import emit
        stage='PLACED' if values.get('kite_order_id') else values.get('status') or ('UNCERTAIN' if values.get('error_message') else None)
        if stage in {'PLACED','FAILED','REJECTED','BLOCKED','RECONCILE_REQUIRED','PARTIAL_UNRESOLVED','UNCERTAIN'}:emit('Order '+stage,order_id=order_id,stage=stage,key=f'order:{order_id}:{stage}')
    except Exception:pass


def unresolved_orders(conn):
    return [dict(row) for row in conn.execute("""
        SELECT * FROM autotrade_orders WHERE mode='live'
        AND status NOT IN ('COMPLETE','REJECTED','CANCELLED','BLOCKED')
        AND (kite_order_id IS NOT NULL OR id IN (SELECT order_id FROM autotrade_buy_reservations)
             OR status IN ('PENDING','OPEN','RECONCILE_REQUIRED','PARTIAL_UNRESOLVED')
             OR signal_source IN ('BUY / scanner signal','SELL / scanner exit','SELL / manual close','SELL / risk exit'))
    """)]


@serialized
def _record_live_order(symbol: str, source: str, side: str, quantity: int, price: float | None, run_id: int | None = None, position_id: int | None = None, confirmed_key: str | None = None, confirmed_strategy: str | None = None) -> int:
    from . import risk
    config = get_config()
    local_id = None
    account_id = None
    conn = None
    try:
        if confirmed_key is not None:
            from .scanner import get_config as scanner_config
            from .confirmed_resolution import enabled, strategy_key
            current_strategy = scanner_config()
            if confirmed_key.startswith('st:'):
                from .supertrend_scan import strategy_key
                valid_strategy = current_strategy.get('strategy','occ') == 'supertrend'
            else:
                valid_strategy = enabled(current_strategy)
            if not valid_strategy or strategy_key(current_strategy) != confirmed_strategy:
                raise ValueError('Confirmed strategy changed before submission; scan again')
        if confirmed_key is None and 'scanner' in source:
            from .scanner import get_config as scanner_config
            if scanner_config().get('strategy','occ') != 'occ':
                raise ValueError('OCC is no longer selected; scanner order blocked')
        manual_exit = side == "SELL" and source == "manual close"
        if config["manual_kill_switch"]:
            raise ValueError("Manual kill switch blocks all submissions")
        if config["mode"] != "live" or ((not config["enabled"] or config["paused"]) and not manual_exit):
            raise ValueError("Live execution is disabled or paused")
        if side == "BUY" and config["kill_switch"]:
            raise ValueError("Cumulative loss kill switch blocks new BUYs")
        validate_limits(config, require_positive=True)
        if config["live_order_type"] != "LIMIT" or config["chase_orders"]:
            raise ValueError("Hard caps require LIMIT orders without chasing")
        if side not in {"BUY", "SELL"} or type(quantity) is not int or quantity < 1:
            raise ValueError("Invalid side or quantity")
        available = _live_preflight(require_margin=side == "BUY")
        kite = get_kite()
        from .broker_identity import verify
        conn_check=connection()
        try:
            linked=conn_check.execute('SELECT * FROM autotrade_positions WHERE id=?',(position_id,)).fetchone() if position_id else None
        finally:conn_check.close()
        account_id=verify(kite, *([linked] if linked else []))
        risk.check(kite, block_buy=side == "BUY")
        ltp = float(kite.ltp([f"NSE:{symbol}"])[f"NSE:{symbol}"]["last_price"])
        if not math.isfinite(ltp) or ltp <= 0:
            raise ValueError("A valid current quote is required")
        # BUY limit bounds maximum spend; SELL limit avoids unbounded slippage.
        price = round(ltp, 2)
        if price <= 0:
            raise ValueError("Invalid rounded order price")
        value = quantity * price
        # The capital ceiling restricts entry exposure, never the value of an exit.
        if side == "BUY" and value > config["max_symbol_value"]:
            raise ValueError("Maximum BUY symbol value exceeded")
        conn = connection()
        conn.execute("BEGIN IMMEDIATE")
        today = datetime.now(IST).date().isoformat()
        if confirmed_key is not None and conn.execute('SELECT 1 FROM autotrade_confirmed_events WHERE symbol=? AND mode=? AND side=? AND block_end=?', (symbol,'live',side,confirmed_key)).fetchone():
            raise ValueError('Confirmed 3D crossover already processed')
        count = conn.execute("SELECT COUNT(*) FROM autotrade_buy_reservations WHERE trading_day=?", (today,)).fetchone()[0]
        if side == "BUY" and count >= config["max_daily_order_count"]:
            raise ValueError("Maximum daily BUY submissions reached")
        pending = unresolved_orders(conn)
        if any(r["symbol"] == symbol for r in pending):
            raise ValueError("Symbol has an unresolved order; reconciliation required")
        rows = [dict(r) for r in conn.execute("SELECT * FROM autotrade_positions WHERE mode='live' AND status='OPEN'")]
        if side == "BUY":
            if any(row["symbol"] == symbol for row in rows):
                raise ValueError("A position in this symbol is already open; no overlapping entries")
            buys = [r for r in pending if r["signal_source"].startswith("BUY")]
            if value > available:
                raise ValueError("Order exceeds available broker balance")
            if any(r["entry_price"] * r["quantity"] > config["max_symbol_value"] for r in rows) or any(not r["requested_price"] or r["requested_price"] * r["requested_qty"] > config["max_symbol_value"] for r in buys):
                raise ValueError("Existing exposure exceeds the current symbol ceiling; reconcile it before new BUYs")
            exposure_symbols = [r["symbol"] for r in rows] + [r["symbol"] for r in buys]
            if len(exposure_symbols) != len(set(exposure_symbols)):
                raise ValueError("Existing overlapping exposure requires reconciliation before new BUYs")
            occupied = {r["symbol"] for r in rows} | {r["symbol"] for r in buys}
            if len(occupied) >= config["max_concurrent_positions"]:
                raise ValueError("Maximum concurrent positions reached, including pending BUYs")
        else:
            position = next((r for r in rows if r["id"] == position_id and r["symbol"] == symbol), None)
            if position and source != 'manual close' and ('scanner' in source or 'confirmed 3D' in source or 'Supertrend' in source):
                from .strategies import position_strategy
                expected = 'supertrend' if confirmed_key and confirmed_key.startswith('st:') else 'occ'
                if position_strategy(position) != expected:
                    raise ValueError('Scanner exit strategy does not own this position')
            if not position or quantity != position["quantity"]:
                raise ValueError("SELL must close an existing matching position")
        cursor = conn.execute("INSERT INTO autotrade_orders (timestamp,symbol,signal_source,mode,order_type,requested_qty,requested_price,status,position_id,scan_run_id) VALUES (?,?,?,'live','LIMIT',?,?,'PENDING',?,?)", (now(), symbol, f"{side} / {source}", quantity, price, position_id, run_id))
        local_id = cursor.lastrowid
        conn.execute('UPDATE autotrade_orders SET account_id=? WHERE id=?',(account_id,local_id))
        if confirmed_key is not None:
            conn.execute('INSERT INTO autotrade_confirmed_events VALUES (?,?,?,?,?,?)', (symbol,'live',side,confirmed_key,local_id,now()))
        if side == "BUY":
            conn.execute("INSERT INTO autotrade_buy_reservations (order_id,trading_day) VALUES (?,?)", (local_id, today))
        conn.commit()
        conn.close()
        conn = None
        if side == 'SELL':
            from .gtt import cancel_before_exit
            try:
                cancel_before_exit(position_id, source, kite)
                from .gtt import position as current_position
                remaining=current_position(position_id)
                if not remaining or remaining['status']!='OPEN' or remaining['quantity']!=quantity:
                    raise ValueError('Position changed during GTT reconciliation; refresh before another SELL')
            except Exception as error:
                _update_order(local_id, status='BLOCKED', error_message=f'GTT cancellation not confirmed; SELL blocked: {error}')
                return local_id
        kite_order_id = kite.place_order(variety="regular", exchange="NSE", tradingsymbol=symbol, transaction_type=side, quantity=quantity, product="CNC", order_type="LIMIT", price=price, validity="DAY", tag=f"{'st' if confirmed_key and confirmed_key.startswith('st:') else 'occ'}{local_id}")
        _update_order(local_id, kite_order_id=str(kite_order_id))
        reconcile_order(local_id)
    except Exception as error:
        try:
            from .telegram_notifications import kite_error
            kite_error(error)
        except Exception:pass
        if conn is not None:
            conn.rollback()
            conn.close()
        if local_id is not None:
            # A timeout may follow broker acceptance. Never free its reservation.
            _update_order(local_id, error_message=f"Submission uncertain; reconcile before retry: {error}")
        else:
            local_id = order_log(symbol, f"{side} / {source}", "live", quantity, price, "BLOCKED", str(error), position_id, run_id, "LIMIT")
    if account_id and local_id:
        _update_order(local_id, account_id=account_id)
    return local_id


@serialized
def reconcile_order(local_id: int) -> dict[str, Any] | None:
    conn = connection(); row = conn.execute("SELECT * FROM autotrade_orders WHERE id=?", (local_id,)).fetchone(); conn.close()
    if not row or not row['kite_order_id']: return dict(row) if row else None
    order = dict(row)
    try:
        kite=get_kite()
        from .broker_identity import verify
        c=connection()
        try: linked=c.execute('SELECT * FROM autotrade_positions WHERE id=?',(order['position_id'],)).fetchone() if order['position_id'] else None
        finally:c.close()
        verify(kite,order,*([linked] if linked else []))
        history = kite.order_history(order['kite_order_id'])
        if not history: return order
        detail = history[-1]; status = str(detail.get('status', 'PENDING')).upper()
        from .fill_accounting import apply
        conn = connection()
        try:
            conn.execute('BEGIN IMMEDIATE')
            pid = apply(order, detail, conn)
            if pid and status!='COMPLETE' and conn.execute('SELECT status FROM autotrade_positions WHERE id=?',(pid,)).fetchone()[0]=='EXIT_PENDING':
                status='PARTIAL_UNRESOLVED'
                detail={**detail,'status_message':'All SELL fills reported, but COMPLETE confirmation is still required'}
            conn.execute("UPDATE autotrade_orders SET status=?,filled_quantity=?,fill_price=?,fill_time=?,error_message=? WHERE id=?",
                         (status,detail.get('filled_quantity'),detail.get('average_price'),str(detail.get('exchange_update_timestamp') or now()) if detail.get('filled_quantity') else None,detail.get('status_message'),local_id))
            conn.commit()
        except Exception:
            conn.rollback(); raise
        finally: conn.close()
        try:
            from .telegram_notifications import emit
            if status in {'COMPLETE','REJECTED','CANCELLED'} and status!=order['status']:emit('Order '+status,order_id=local_id,stage=status,key=f'order:{local_id}:{status}')
        except Exception:pass
        if pid:
            from .gtt import ensure, mark_waiting
            if status in {'COMPLETE','CANCELLED','REJECTED'}: ensure(pid, kite)
            elif order['signal_source'].startswith('BUY'): mark_waiting(pid)
    except Exception as error:
        try:
            from .telegram_notifications import kite_error
            kite_error(error)
        except Exception:pass
        partial = int(detail.get('filled_quantity') or 0) if 'detail' in locals() else 0
        _update_order(local_id, status='PARTIAL_UNRESOLVED' if partial else 'RECONCILE_REQUIRED', error_message=f'Reconciliation pending: {error}')
    conn = connection(); result = conn.execute('SELECT * FROM autotrade_orders WHERE id=?',(local_id,)).fetchone(); conn.close()
    return dict(result)


def reconcile_pending_orders() -> None:
    conn = connection(); rows = conn.execute("SELECT id FROM autotrade_orders WHERE mode='live' AND status NOT IN ('COMPLETE', 'REJECTED', 'CANCELLED', 'BLOCKED') AND kite_order_id IS NOT NULL").fetchall(); conn.close()
    for row in rows: reconcile_order(row["id"])


@serialized
def process_signals(results: list[dict[str, Any]], run_id: int) -> None:
    from .analytics import audit_skip
    # Comparison skips are logged by the scanner, before the execution boundary.
    results = [signal for signal in results if not signal.get("comparison_only")]
    if not results:
        return
    from .scanner import get_config as scanner_config
    if scanner_config().get('strategy','occ') != 'occ':
        return
    results = [signal for signal in results if signal.get('strategy','occ') == 'occ']
    confirmed = [signal for signal in results if signal.get('occ_mode')=='confirmed_3d']
    if confirmed:
        from .confirmed_execution import process
        process(confirmed, run_id)
    results = [signal for signal in results if signal.get('occ_mode')!='confirmed_3d']
    if not results: return
    config = get_config()
    if not config["enabled"] or config["paused"] or config["kill_switch"]:
        reason = 'Cumulative loss latch active' if config['kill_switch'] else 'Execution disabled or paused'
        for signal in results:
            audit_skip(signal['symbol'], 'BUY' if signal['signal_type']=='BUY' else 'SELL', config['mode'], reason, run_id)
        return
    if config["mode"] == "paper":
        process_paper_signals(results, run_id)
        return
    for signal in results:
        if signal.get("comparison_only"):
            continue
        symbol = signal["symbol"]
        if signal["signal_type"] == "BUY":
            if signal["trigger_date"] != datetime.now(IST).date().isoformat():
                order_log(symbol, "BUY / stale signal", "live", 0, None, "BLOCKED", "Live BUY requires today's signal", run_id=run_id)
                continue
            try:
                quote = float(get_kite().ltp([f"NSE:{symbol}"])[f"NSE:{symbol}"]["last_price"])
                if not math.isfinite(quote) or quote <= 0:
                    raise ValueError("Invalid current price")
                quantity = math.floor(get_config()["max_symbol_value"] / quote)
                if quantity < 1:
                    raise ValueError("One share exceeds the symbol ceiling")
            except Exception as error:
                order_log(symbol, "BUY / sizing", "live", 0, None, "BLOCKED", str(error), run_id=run_id)
                continue
            _record_live_order(symbol, "scanner signal", "BUY", quantity, quote, run_id=run_id)
        elif signal["signal_type"] == "EXIT" and config["exit_rule"] in {"scanner_exit", "whichever_first"}:
            conn = connection()
            existing = conn.execute("SELECT * FROM autotrade_positions WHERE symbol=? AND mode='live' AND status='OPEN'", (symbol,)).fetchone()
            conn.close()
            if existing:
                _record_live_order(symbol, "scanner exit", "SELL", existing["quantity"], None, run_id=run_id, position_id=existing["id"])
            else:
                audit_skip(symbol, 'SELL', 'live', 'No open app position to close', run_id)
        elif signal['signal_type']=='EXIT':
            audit_skip(symbol, 'SELL', 'live', 'Scanner exits disabled by exit rule', run_id)


@serialized
@serialized
def process_paper_signals(results: list[dict[str, Any]], run_id: int) -> None:
    from .analytics import audit_skip
    config = get_config()
    if config["mode"] != "paper" or not config["enabled"] or config["paused"] or config["kill_switch"]:
        for signal in results:
            audit_skip(signal['symbol'], 'BUY' if signal['signal_type']=='BUY' else 'SELL', 'paper', 'Paper execution disabled, paused or loss-latched', run_id)
        return
    from .scanner import get_config as scanner_config
    selected_strategy = scanner_config().get('strategy','occ')
    conn = connection()
    try:
        for signal in results:
            if signal.get('strategy','occ') != selected_strategy:
                audit_skip(signal['symbol'],'BUY' if signal['signal_type']=='BUY' else 'SELL','paper','Scanner strategy changed; scan again',run_id)
                continue
            symbol, price = signal["symbol"], float(signal["trigger_price"])
            confirmed_key=signal.get('_confirmed_key')
            side='BUY' if signal['signal_type']=='BUY' else 'SELL'
            if confirmed_key:
                conn.execute('BEGIN IMMEDIATE')
                if conn.execute('SELECT 1 FROM autotrade_confirmed_events WHERE symbol=? AND mode=? AND side=? AND block_end=?',(symbol,'paper',side,confirmed_key)).fetchone():
                    conn.rollback()
                    audit_skip(symbol,side,'paper','Confirmed 3D crossover already processed',run_id)
                    continue
            existing = conn.execute("SELECT * FROM autotrade_positions WHERE symbol=? AND mode='paper' AND status='OPEN'", (symbol,)).fetchone()
            if existing and signal['signal_type']=='EXIT':
                from .strategies import position_strategy
                if position_strategy(existing) != signal.get('strategy','occ'):
                    conn.rollback()
                    audit_skip(symbol,side,'paper','Scanner exit strategy does not own this position',run_id)
                    continue
            if signal["signal_type"] == "BUY" and not existing:
                rows = conn.execute("SELECT * FROM autotrade_positions WHERE mode='paper' AND status='OPEN'").fetchall()
                quantity = math.floor(config["paper_cash"] * config["per_trade_pct"] / 100 / price)
                deployed = sum(r["entry_price"] * r["quantity"] for r in rows)
                if quantity < 1 or len(rows) >= config["paper_max_positions"] or deployed + quantity * price > config["paper_cash"] * config["paper_max_deployed_pct"] / 100:
                    conn.rollback()
                    audit_skip(symbol, 'BUY', 'paper', 'Paper quantity, position or deployed-capital limit', run_id)
                    continue
                cursor = conn.execute("INSERT INTO autotrade_positions (symbol,mode,status,entry_time,entry_price,quantity,current_sl,current_tp,scan_run_id) VALUES (?,'paper','OPEN',?,?,?,?,?,?)", (symbol, now(), price, quantity, price * (1-config["sl_pct"]/100), price * (1+config["tp_pct"]/100), run_id))
                if confirmed_key:
                    conn.execute('INSERT INTO autotrade_confirmed_events VALUES (?,?,?,?,?,?)',(symbol,'paper','BUY',confirmed_key,None,now()))
                conn.commit()
                order_log(symbol, "BUY", "paper", quantity, price, "FILLED", position_id=cursor.lastrowid, run_id=run_id)
            elif signal["signal_type"] == "EXIT" and existing and config["exit_rule"] in {"scanner_exit", "whichever_first"}:
                pnl = (price-existing["entry_price"]) * existing["quantity"]
                conn.execute("UPDATE autotrade_positions SET status='CLOSED',exit_time=?,exit_price=?,exit_reason='Scanner EXIT signal',realized_pnl=? WHERE id=?", (now(), price, pnl, existing["id"]))
                if confirmed_key:
                    conn.execute('INSERT INTO autotrade_confirmed_events VALUES (?,?,?,?,?,?)',(symbol,'paper','SELL',confirmed_key,None,now()))
                conn.commit()
                order_log(symbol, "Scanner EXIT", "paper", existing["quantity"], price, "FILLED", position_id=existing["id"], run_id=run_id)
            else:
                conn.rollback()
                reason = 'Position already open' if signal['signal_type']=='BUY' else 'No open app position to close' if not existing else 'Scanner exits disabled by exit rule'
                audit_skip(symbol, 'BUY' if signal['signal_type']=='BUY' else 'SELL', 'paper', reason, run_id, existing['id'] if existing else None)
    finally:
        conn.close()


@serialized
def monitor_live_positions() -> None:
    # Order accounting and cumulative loss sampling remain; SL/TP price decisions
    # belong exclusively to Kite GTT. Reconcile even while paused or latched.
    from . import risk
    try:
        reconcile_pending_orders()
        if get_config()['cumulative_loss_baseline']:
            risk.check(get_kite(), block_buy=False)
    except Exception as error:
        risk.record_error(str(error))


def reconcile_gtt_positions():
    from .gtt import reconcile_all
    reconcile_all()


def chase_open_orders() -> None:
    # Price modifications would invalidate the persisted capital reservation.
    return None


def positions() -> list[dict[str, Any]]:
    config = get_config(); conn = connection(); rows = conn.execute("SELECT * FROM autotrade_positions WHERE mode=? ORDER BY status='OPEN' DESC, entry_time DESC", (config["mode"],)).fetchall(); conn.close()
    from .gtt import public
    return [{**dict(row), **public(row['id'])} for row in rows]


def orders(limit: int = 100) -> list[dict[str, Any]]:
    conn = connection(); rows = conn.execute("SELECT * FROM autotrade_orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall(); conn.close(); return [dict(row) for row in rows]


def open_orders() -> list[dict[str, Any]]:
    conn = connection()
    rows = unresolved_orders(conn)
    conn.close()
    return rows


def stats() -> dict[str, Any]:
    config = get_config(); rows = positions(); open_rows = [row for row in rows if row["status"] == "OPEN"]; deployed = sum(row["entry_price"] * row["quantity"] for row in open_rows); realized = sum(row["realized_pnl"] or 0 for row in rows); return {"open_positions": len(open_rows), "capital_deployed": round(deployed, 2), "capital_deployed_pct": round(deployed / max(config["paper_cash"], 1) * 100, 2), "realized_pnl": round(realized, 2), "unrealized_pnl": 0}


def close_position(position_id: int, confirm_phrase: str = "") -> dict[str, Any]:
    conn = connection(); position = conn.execute("SELECT * FROM autotrade_positions WHERE id=? AND status='OPEN'", (position_id,)).fetchone(); conn.close()
    if not position: raise ValueError("Open Auto-Trade position not found")
    position = dict(position)
    if position["mode"] == "live":
        if confirm_phrase != LIVE_CONFIRMATION: raise ValueError(f'Type exactly "{LIVE_CONFIRMATION}" to close a live position.')
        local_id = _record_live_order(position["symbol"], "manual close", "SELL", position["quantity"], None, position_id=position_id)
        conn = connection()
        order = conn.execute("SELECT * FROM autotrade_orders WHERE id=?", (local_id,)).fetchone()
        conn.close()
        if order["status"] == "BLOCKED":
            raise ValueError(order["error_message"])
        return {**position, "submitted_order_id": local_id}
    exit_price = position["entry_price"]; conn = connection(); conn.execute("UPDATE autotrade_positions SET status='CLOSED', exit_time=?, exit_price=?, exit_reason='Manual close', realized_pnl=0 WHERE id=?", (now(), exit_price, position_id)); conn.commit(); conn.close(); order_log(position["symbol"], "Manual close", "paper", position["quantity"], exit_price, "FILLED", position_id=position_id); return {**position, "status": "CLOSED"}


def start_live_monitor() -> None:
    if not scheduler.running: scheduler.start()
    try:
        reconcile_pending_orders()
        reconcile_gtt_positions()
    except Exception: pass


def backtest(payload: dict[str, Any]) -> dict[str, Any]: return {"status": "PAPER BACKTEST READY", "message": "Backtest orchestration is isolated and does not place orders. Supply historical candles through the scanner data provider to run a walk-forward simulation."}


scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
scheduler.add_job(monitor_live_positions, "interval", seconds=30, id="autotrade-live-monitor", replace_existing=True, max_instances=1)

scheduler.add_job(reconcile_gtt_positions, "interval", minutes=3, id="autotrade-gtt-reconcile", replace_existing=True, max_instances=1)
