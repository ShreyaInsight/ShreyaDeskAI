"""Cumulative risk uses only app-tracked live trades and their recorded entries.
Daily per-trade snapshots prove history coverage; they are not summed into P&L.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta

from . import autotrade as trade

CLEAR_CONFIRMATION = "CLEAR KILL SWITCH"
RECONCILE_CONFIRMATION = "RECONCILE ALGO HISTORY"
# History finalization uses the shared session close plus 45 minutes.
DEFAULT_STATE = {
    "cumulative_loss_baseline": None,
    "baseline_bot_pnl": None,
    "baseline_trades": None,
    "accounting_version": 3,
    "baseline_requires_reset": False,
    "broker_user_id": None,
    "loss_latched": False,
    "manual_latched": False,
    "reason": "Initialize the cumulative baseline using Clear kill switch",
    "history_error": "Cumulative baseline has not been initialized",

    "app_cumulative_pnl": None,
    "checked_at": None,
    "cancellation_errors": [],
}


def connection():
    conn = trade.connection()
    conn.execute("CREATE TABLE IF NOT EXISTS autotrade_risk_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS autotrade_app_pnl_days (baseline TEXT NOT NULL, day TEXT NOT NULL, trades TEXT NOT NULL, finalized INTEGER NOT NULL, observed_at TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(baseline,day))")
    conn.execute("CREATE TABLE IF NOT EXISTS autotrade_risk_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL)")
    conn.commit()
    return conn


def load():
    conn = connection()
    row = conn.execute("SELECT payload FROM autotrade_risk_state WHERE id=1").fetchone()
    conn.close()
    return {**DEFAULT_STATE, **json.loads(row[0])} if row else DEFAULT_STATE.copy()


def _write(conn, state):
    conn.execute("INSERT INTO autotrade_risk_state VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (json.dumps(state, allow_nan=False),))


def save(state):
    conn = connection()
    _write(conn, state)
    conn.commit()
    conn.close()


def audit(conn, action, payload):
    conn.execute("INSERT INTO autotrade_risk_audit(timestamp,action,payload) VALUES (?,?,?)", (trade.now(), action, json.dumps(payload, allow_nan=False)))


def public_state():
    state = load()
    return {
        "kill_switch": state["manual_latched"] or state["loss_latched"],
        "manual_kill_switch": state["manual_latched"],
        "loss_kill_switch": state["loss_latched"],
        "cumulative_loss_baseline": state["cumulative_loss_baseline"],
        "risk_reason": state["reason"],
        "risk_history_error": state["history_error"],
        "baseline_requires_reset": state["baseline_requires_reset"],
        "app_cumulative_pnl": state["app_cumulative_pnl"],
        "risk_checked_at": state["checked_at"],
        "cancellation_errors": state["cancellation_errors"],
    }


@trade.serialized
def migrate():
    conn = connection()
    exists = conn.execute("SELECT id FROM autotrade_risk_state WHERE id=1").fetchone()
    if not exists:
        old = conn.execute("SELECT payload FROM autotrade_config WHERE id=1").fetchone()
        old = json.loads(old[0]) if old else {}
        state = DEFAULT_STATE.copy()
        if old.get("kill_switch") or old.get("loss_lock_date"):
            state.update(loss_latched=True, reason="Previous kill switch preserved; explicit reset required")
        _write(conn, state)
        # Backfill real/uncertain historical submissions only, not scanner skips.
        conn.execute("INSERT OR IGNORE INTO autotrade_buy_reservations SELECT id,substr(timestamp,1,10) FROM autotrade_orders WHERE mode='live' AND signal_source LIKE 'BUY / %' AND (kite_order_id IS NOT NULL OR status IN ('PENDING','OPEN','RECONCILE_REQUIRED','PARTIAL_UNRESOLVED') OR (status='ERROR' AND signal_source='BUY / scanner signal' AND requested_qty>0))")
        audit(conn, "migration", {"limits_version": 2})
    row = conn.execute("SELECT payload FROM autotrade_risk_state WHERE id=1").fetchone()
    stored = json.loads(row[0])
    if stored.get("accounting_version") != 3:
        audit(conn, "legacy_accounting_archived", stored)
        state = {**DEFAULT_STATE, **{key:value for key,value in stored.items() if key in DEFAULT_STATE}}
        state.update(accounting_version=3, baseline_bot_pnl=None, baseline_trades=None,
                     baseline_requires_reset=bool(state["cumulative_loss_baseline"]),
                     app_cumulative_pnl=None, history_error="Initialize an app-only baseline using the typed reset")
        _write(conn, state)
    conn.commit()
    conn.close()


def finite(value, label):
    if isinstance(value, bool):
        raise ValueError(f"Invalid {label}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"Invalid {label}")
    return result


def app_positions():
    conn = trade.connection()
    from .broker_identity import owner
    account=owner()
    rows = [dict(r) for r in conn.execute("SELECT * FROM autotrade_positions WHERE mode='live' AND (account_id=? OR account_id IS NULL) ORDER BY id",(account,))]
    conn.close()
    return rows


def _contribution(row, mark, closed):
    entry = finite(row["entry_price"], "recorded entry price")
    quantity = finite(row["quantity"], "recorded quantity")
    mark = finite(mark, "app position price")
    if entry <= 0 or mark <= 0 or quantity <= 0 or int(quantity) != quantity:
        raise ValueError("Invalid recorded app trade")
    return {"position_id":row["id"], "symbol":row["symbol"], "quantity":int(quantity),
            "entry_price":entry, "mark_price":mark, "closed":closed,
            "pnl":finite((mark-entry)*quantity + (float(row.get("realized_pnl") or 0) if not closed else 0), "app trade P&L")}


def app_snapshot(kite):
    rows = app_positions()
    from .broker_identity import verify
    verify(kite,*rows)
    opened = [r for r in rows if r["status"] == "OPEN"]
    quotes = kite.ltp([f"NSE:{r['symbol']}" for r in opened]) if opened else {}
    contributions = []
    for row in rows:
        if row["status"] in {"CLOSED", "EXIT_PENDING"}:
            contribution = _contribution(row, row["exit_price"], True)
            if row["realized_pnl"] is None or not math.isclose(contribution["pnl"], finite(row["realized_pnl"], "realized P&L"), abs_tol=0.01):
                raise ValueError("Recorded app exit and realized P&L disagree; reconcile the trade")
        elif row["status"] == "OPEN":
            contribution = _contribution(row, quotes[f"NSE:{row['symbol']}"]["last_price"], False)
        else:
            raise ValueError("App trade has an unresolved lifecycle state")
        contributions.append(contribution)
    return contributions


def snapshot_total(contributions):
    return finite(sum(item["pnl"] for item in contributions), "app total")


def app_reading(kite):
    return snapshot_total(app_snapshot(kite))


def _day(value):
    return datetime.fromisoformat(value).astimezone(trade.IST).date()


def history_positions(day_text):
    day = date.fromisoformat(day_text)
    result = []
    from .fill_accounting import positions_at_day
    for row in positions_at_day(app_positions(), day):
        if _day(row["entry_time"]) > day:
            continue
        closed = row["status"] == "CLOSED" and _day(row["exit_time"]) <= day
        result.append({"position_id":row["id"], "symbol":row["symbol"],
                       "quantity":row["quantity"], "entry_price":row["entry_price"],
                       "needs_price":not closed, "recorded_exit_price":row["exit_price"] if closed else None})
    return result


def _cancel(kite):
    errors = []
    conn = trade.connection()
    rows = trade.unresolved_orders(conn)
    conn.close()
    for row in rows:
        if not row["kite_order_id"]:
            errors.append(f"Order {row['id']}: unknown broker acceptance; manual reconciliation required")
            continue
        try:
            from .broker_identity import verify
            verify(kite,row)
            kite.cancel_order(variety="regular", order_id=row["kite_order_id"])
        except Exception:
            errors.append(f"Order {row['id']}: cancellation unconfirmed; check Kite")
    return errors


@trade.serialized
def latch(*, manual=False, kite=None):
    state = load()
    state["manual_latched" if manual else "loss_latched"] = True
    state["reason"] = "Manual kill switch" if manual else "Cumulative loss ceiling reached"
    # Persist the latch first. Even if pausing/cancelling fails, submissions see it.
    save(state)
    try:
        from .telegram_notifications import emit
        emit('Kill switch triggered',state['reason']+'; new trading submissions are blocked.',category='safety',key='kill:'+trade.now())
    except Exception:pass
    trade.pause()
    try:
        state["cancellation_errors"] = _cancel(kite if kite is not None else trade.get_kite())
    except Exception:
        state["cancellation_errors"] = ["Broker unavailable; check outstanding orders directly in Kite"]
    conn = connection()
    _write(conn, state)
    audit(conn, "manual_kill" if manual else "loss_kill", state)
    conn.commit()
    conn.close()
    return trade.get_config()


@trade.serialized
def record_error(message):
    state = load()
    state["history_error"] = message
    save(state)
    try:
        from .telegram_notifications import emit
        emit('P&L history blocks BUYs',message,category='safety')
    except Exception:pass


def _capture_day(conn, state, contributions, current):
    from .market_calendar import is_finalized, is_trading_day
    if not is_trading_day(current.date()): return
    finalized = is_finalized(current.date(),current,delay_minutes=45)
    conn.execute("INSERT INTO autotrade_app_pnl_days VALUES (?,?,?,?,?,?) ON CONFLICT(baseline,day) DO UPDATE SET trades=excluded.trades,finalized=excluded.finalized,observed_at=excluded.observed_at WHERE autotrade_app_pnl_days.source='sample'", (state["cumulative_loss_baseline"], current.date().isoformat(), json.dumps(contributions, allow_nan=False), int(finalized), current.isoformat(), "sample"))


def _recover_cached_days(state, current):
    """Recover past coverage only from finalized broker candles and app fills.

    Never substitutes intraday quotes, calendar guesses or portfolio-wide P&L.
    Missing/ambiguous evidence leaves the day blocked for manual review.
    """
    from .market_calendar import is_trading_day
    from .fill_accounting import positions_at_day
    from . import market_cache
    rows=app_positions()
    conn=connection()
    try:
        if trade.unresolved_orders(conn): return
        ledger_ids={r[0] for r in conn.execute('SELECT DISTINCT position_id FROM autotrade_fill_events')}
        if any(r.get('sold_quantity',0) and r['id'] not in ledger_ids and r['status']=='OPEN' for r in rows):return
        existing={r['day']:r['finalized'] for r in conn.execute(
            'SELECT day,finalized FROM autotrade_app_pnl_days WHERE baseline=?',
            (state['cumulative_loss_baseline'],))}
    finally:conn.close()
    day=datetime.fromisoformat(state['cumulative_loss_baseline']).astimezone(trade.IST).date()
    recovered_min=None
    while day<current.date():
        if existing.get(day.isoformat()) or not is_trading_day(day):
            day+=timedelta(days=1);continue
        historical=positions_at_day(rows,day)
        contributions=[];evidence=[]
        try:
            with market_cache.db() as cache:
                for row in historical:
                    if _day(row['entry_time'])>day:continue
                    closed=row['status']=='CLOSED' and _day(row['exit_time'])<=day
                    if row['status'] not in {'OPEN','CLOSED'} or (row['status']=='CLOSED' and not closed):
                        raise ValueError('Historical lifecycle is ambiguous')
                    if closed:
                        mark=row['exit_price']
                    else:
                        candle=cache.execute("SELECT c.payload,s.fetched_day FROM scanner_daily_candles c JOIN scanner_history_state s ON s.symbol=c.symbol WHERE c.symbol=? AND c.day=?",(row['symbol'],day.isoformat())).fetchone()
                        if not candle or date.fromisoformat(candle['fetched_day'])<=day:
                            raise ValueError('Finalized daily candle is unavailable')
                        bar=json.loads(candle['payload'])
                        if market_cache.day_of(bar['date'])!=day:raise ValueError('Candle date mismatch')
                        mark=finite(bar['close'],'historical close')
                        evidence.append({'position_id':row['id'],'symbol':row['symbol'],'day':day.isoformat(),'fetched_day':candle['fetched_day'],'close':mark})
                    contributions.append(_contribution(row,mark,closed))
        except (ValueError,KeyError,TypeError):
            day+=timedelta(days=1);continue
        recovered=snapshot_total(contributions)-snapshot_total(state['baseline_trades'])
        previous=state.get('recovered_min_pnl')
        state['recovered_min_pnl']=recovered if previous is None else min(previous,recovered)
        conn=connection()
        try:
            conn.execute("INSERT INTO autotrade_app_pnl_days VALUES (?,?,?,?,?,?) ON CONFLICT(baseline,day) DO UPDATE SET trades=excluded.trades,finalized=1,observed_at=excluded.observed_at,source=excluded.source WHERE autotrade_app_pnl_days.finalized=0",(state['cumulative_loss_baseline'],day.isoformat(),json.dumps(contributions,allow_nan=False),1,current.isoformat(),'cache_recovery'))
            _write(conn,state)  # Persist the historical loss evidence with coverage.
            audit(conn,'recover_app_day',{'day':day.isoformat(),'baseline':state['cumulative_loss_baseline'],'trades':contributions,'candle_evidence':evidence})
            conn.commit()
        finally:conn.close()
        recovered=snapshot_total(contributions)-snapshot_total(state['baseline_trades'])
        recovered_min=recovered if recovered_min is None else min(recovered_min,recovered)
        day+=timedelta(days=1)
    return recovered_min


def _history_error(conn, state, current):
    start = datetime.fromisoformat(state["cumulative_loss_baseline"]).astimezone(trade.IST).date()
    rows = {r["day"]: dict(r) for r in conn.execute("SELECT * FROM autotrade_app_pnl_days WHERE baseline=?", (state["cumulative_loss_baseline"],))}
    missing = []
    day = start
    while day <= current.date():
        row = rows.get(day.isoformat())
        from .market_calendar import is_finalized
        # Only completed exchange sessions require finalized history.
        if is_finalized(day,current,delay_minutes=45) and (row is None or not row["finalized"]):
            missing.append(day.isoformat())
        day += timedelta(days=1)
    if missing:
        return "Missing finalized app-trade history for " + ", ".join(missing) + ". Automatic recovery needs finalized cached daily candles and reconciled app orders; otherwise reconcile the app trades manually."
    return None


@trade.serialized
def check(kite, *, block_buy=True):
    state = load()
    if not state["cumulative_loss_baseline"] or state["baseline_requires_reset"]:
        if block_buy:
            raise ValueError("Initialize the cumulative baseline with the typed Clear kill switch action")
        return public_state()
    try:
        if str(kite.profile()["user_id"]) != state["broker_user_id"]:
            raise ValueError("Broker account changed; baseline belongs to a different account")
    except Exception as error:
        record_error("Broker account verification failed; new submissions blocked")
        raise ValueError("Broker account verification failed; new submissions blocked") from error
    current = datetime.now(trade.IST)
    errors = []
    app_total = None
    recovered_pnl = None
    try:
        contributions = app_snapshot(kite)
        baseline_trades = state["baseline_trades"]
        if baseline_trades is None:
            raise ValueError("App baseline is missing its trade snapshots")
        if not {r["position_id"] for r in baseline_trades}.issubset({r["position_id"] for r in contributions}):
            raise ValueError("App trade history was removed after the baseline")
        app_total = snapshot_total(contributions) - snapshot_total(baseline_trades)
        recovered_pnl = _recover_cached_days(state,current)
        conn = connection()
        try:
            _capture_day(conn, state, contributions, current)
            history_error = _history_error(conn, state, current)
            conn.commit()
        finally:
            conn.close()
        if history_error:
            errors.append(history_error)
    except Exception as error:
        errors.append(f"App-trade P&L/history unavailable; new BUYs blocked: {error}")
    state.update(app_cumulative_pnl=app_total, history_error="; ".join(errors) or None,
                 checked_at=current.isoformat())
    save(state)
    try:
        from .telegram_notifications import emit
        if state['history_error']:emit('P&L history blocks BUYs',state['history_error'],category='safety')
    except Exception:pass
    observed=[v for v in (app_total,recovered_pnl,state.get('recovered_min_pnl')) if v is not None]
    if observed and min(observed) <= -trade.get_config()["max_cumulative_loss"] and not state["loss_latched"]:
        latch(kite=kite)
        state = load()
    if block_buy:
        if state["manual_latched"] or state["loss_latched"]:
            raise ValueError("Kill switch is latched; explicit typed reset required")
        if state["history_error"]:
            raise ValueError(state["history_error"])
    return public_state()


@trade.serialized
def clear(confirm_phrase):
    if confirm_phrase != CLEAR_CONFIRMATION:
        raise ValueError(f'Type exactly "{CLEAR_CONFIRMATION}" to reset cumulative P&L')
    # This is a reset, never an enable. Pause even if subsequent broker reads fail.
    trade.pause()
    kite = trade.get_kite()
    from .broker_identity import verify
    verify(kite,initialize=True)
    trade.reconcile_pending_orders()
    conn = trade.connection()
    unresolved = trade.unresolved_orders(conn)
    conn.close()
    if unresolved:
        raise ValueError("Reconcile all outstanding app orders before resetting the baseline")
    try:
        user_id = str(kite.profile()["user_id"])
        contributions = app_snapshot(kite)
        bot_value = snapshot_total(contributions)
    except Exception as error:
        raise ValueError("Cannot reset without verified app-trade P&L; existing latch preserved") from error
    current = datetime.now(trade.IST)
    state = {**DEFAULT_STATE, "cumulative_loss_baseline": current.isoformat(),
             "baseline_bot_pnl": bot_value, "baseline_trades": contributions,
             "broker_user_id": user_id,
             "app_cumulative_pnl": 0.0, "history_error": None,
             "reason": "Baseline reset manually; execution remains paused", "checked_at": current.isoformat()}
    conn = connection()
    conn.execute("BEGIN IMMEDIATE")
    _capture_day(conn, state, contributions, current)
    _write(conn, state)
    audit(conn, "clear", state)
    conn.commit()
    conn.close()
    return trade.get_config()


@trade.serialized
def reconcile_day(day_text, prices, confirm_phrase):
    if confirm_phrase != RECONCILE_CONFIRMATION:
        raise ValueError(f'Type exactly "{RECONCILE_CONFIRMATION}" to verify app-trade history')
    state = load()
    if not state["cumulative_loss_baseline"] or state["baseline_requires_reset"]:
        raise ValueError("Initialize an app-only baseline first")
    day = date.fromisoformat(day_text)
    baseline_day = datetime.fromisoformat(state["cumulative_loss_baseline"]).date()
    if not baseline_day <= day < datetime.now(trade.IST).date():
        raise ValueError("Only completed days since the current baseline can be reconciled")
    from .fill_accounting import positions_at_day
    rows = {r["id"]:r for r in positions_at_day(app_positions(), day)}
    expected = history_positions(day_text)
    required = {r["position_id"] for r in expected if r["needs_price"]}
    if not isinstance(prices, list):
        raise ValueError("Supply day-end prices keyed by app position ID, not a portfolio P&L total")
    supplied = {}
    for item in prices:
        position_id = item["position_id"]
        if type(position_id) is not int or position_id in supplied:
            raise ValueError("Duplicate or invalid app position ID")
        supplied[position_id] = finite(item["price"], "verified app day-end price")
    if set(supplied) != required:
        raise ValueError("Provide exactly one price for every app position open at that day-end")
    contributions = [_contribution(rows[item["position_id"]], supplied[item["position_id"]] if item["needs_price"] else item["recorded_exit_price"], not item["needs_price"]) for item in expected]
    trade.pause()
    conn = connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO autotrade_app_pnl_days VALUES (?,?,?,?,?,?) ON CONFLICT(baseline,day) DO UPDATE SET trades=excluded.trades,finalized=1,observed_at=excluded.observed_at,source=excluded.source", (state["cumulative_loss_baseline"], day_text, json.dumps(contributions, allow_nan=False), 1, trade.now(), "manual"))
        audit(conn, "reconcile_app_day", {"day":day_text, "trades":contributions, "baseline":state["cumulative_loss_baseline"]})
        conn.commit()
    finally:
        conn.close()
    return trade.get_config()
