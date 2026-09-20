import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, Mock
from backend import autotrade as a, risk
from backend.main import start_autotrade_monitor


class Clock(datetime):
    value = datetime(2026, 9, 7, 10, tzinfo=a.IST)
    @classmethod
    def now(cls, tz=None): return cls.value if tz else cls.value.replace(tzinfo=None)


class Broker:
    def __init__(self):
        self.calls, self.cancelled = [], []
        self.pnl, self.price = 0, 100
        self.timeout, self.cancel_failure = False, False
        self.holding_rows = []
        self.history = {"status": "OPEN"}
    def profile(self): return {"user_id": "TEST"}
    def positions(self): return {"day": [{"pnl": self.pnl, "exchange": "NSE"}]}
    def holdings(self): return self.holding_rows
    def ltp(self, symbols): return {s: {"last_price": self.price} for s in symbols}
    def place_order(self, **kw):
        self.calls.append(kw)
        if self.timeout: raise TimeoutError("unknown acceptance")
        return str(len(self.calls))
    def order_history(self, order_id): return [self.history]
    def cancel_order(self, **kw):
        self.cancelled.append(kw)
        if self.cancel_failure: raise TimeoutError("cancellation unknown")
        return "cancelled"


class SafetyTests(unittest.TestCase):
    def setUp(self):
        Clock.value = datetime(2026, 9, 7, 10, tzinfo=a.IST)
        self.temp = tempfile.TemporaryDirectory()
        self.broker = Broker()
        from backend import scanner
        self.patches = [patch.object(scanner, "get_config", return_value=dict(scanner.DEFAULT_CONFIG)), patch.object(a, "DB_PATH", Path(self.temp.name) / "test.db"),
                        patch.object(a, "get_kite", return_value=self.broker),
                        patch.object(a, "_live_preflight", return_value=20000),
                        patch.object(a, "datetime", Clock), patch.object(risk, "datetime", Clock),
                        patch.object(a, "scheduler", Mock(running=True))]
        for p in self.patches: p.start()
        risk.migrate()
        risk.clear(risk.CLEAR_CONFIRMATION)
        self.configure()
    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()
    def configure(self, **changes):
        rules = {f"live_{key}": changes[key] for key in a.RULE_KEYS if key in changes}
        return a.save_config({**a.DEFAULT_CONFIG, "mode": "live", "enabled": True, "paused": False, **changes, **rules}, internal=True)
    def order(self, symbol="TEST", quantity=10):
        return a._record_live_order(symbol, "scanner signal", "BUY", quantity, 1)
    def position(self, symbol="HELD", quantity=10, entry=100, closed_pnl=None):
        if closed_pnl is not None:
            entry = max(entry, -closed_pnl / quantity + 100)
        exit_price = None if closed_pnl is None else entry + closed_pnl / quantity
        conn = a.connection()
        cursor = conn.execute("INSERT INTO autotrade_positions(symbol,mode,status,entry_time,entry_price,quantity,current_sl,current_tp,exit_time,realized_pnl,exit_price) VALUES (?,'live',?,?,?,?,?,?,?,?,?)", (symbol, "OPEN" if closed_pnl is None else "CLOSED", a.now(), entry, quantity, 90, 110, None if closed_pnl is None else a.now(), closed_pnl, exit_price))
        conn.execute('UPDATE autotrade_positions SET account_id=? WHERE id=?',('TEST',cursor.lastrowid))
        conn.commit(); conn.close(); return cursor.lastrowid
    def test_startup_persistently_disables(self):
        start_autotrade_monitor()
        self.assertFalse(a.get_config()["enabled"])
        self.assertTrue(a.get_config()["paused"])
        self.order(); self.assertEqual(self.broker.calls, [])
    def test_settings_cannot_enable_or_clear_baseline(self):
        baseline = risk.load()["cumulative_loss_baseline"]
        risk.latch(manual=True, kite=self.broker)
        c = a.save_config({"enabled": True, "paused": False, "kill_switch": False, "cumulative_loss_baseline": None})
        self.assertFalse(c["enabled"])
        self.assertTrue(c["kill_switch"])
        self.assertEqual(c["cumulative_loss_baseline"], baseline)
    def test_symbol_ceiling_and_fresh_quote(self):
        self.order(quantity=11); self.assertEqual(self.broker.calls, [])
        self.order(quantity=10); self.assertEqual(self.broker.calls[0]["price"], 100)
    def test_five_buys_sixth_blocked(self):
        for i in range(6): self.order(str(i))
        self.assertEqual(len(self.broker.calls), 5)
    def test_daily_count_concurrent(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(self.order, [str(i) for i in range(10)]))
        self.assertEqual(len(self.broker.calls), 5)
    def test_skips_do_not_count(self):
        for status in ["CANCELLED", "ERROR", "BLOCKED"]:
            for _ in range(5): a.order_log("SKIP", "BUY / sizing", "live", 0, None, status)
        self.order(); self.assertEqual(len(self.broker.calls), 1)
    def test_rejected_buy_still_counts(self):
        self.broker.history = {"status": "REJECTED", "filled_quantity": 0}
        for _ in range(6): self.order()
        self.assertEqual(len(self.broker.calls), 5)
    def test_sell_not_limited_by_daily_buys(self):
        for i in range(5): self.order(str(i))
        for i in range(7):
            symbol = f"HELD{i}"
            pos = self.position(symbol)
            a._record_live_order(symbol, "manual close", "SELL", 10, None, position_id=pos)
        self.assertEqual(len(self.broker.calls), 12)
    def test_twenty_slots_including_pending(self):
        for i in range(19): self.position(str(i))
        self.order("LAST"); self.order("EXTRA")
        self.assertEqual(len(self.broker.calls), 1)
    def test_closed_position_frees_slot(self):
        for i in range(20): self.position(str(i), closed_pnl=0 if i == 0 else None)
        self.order("NEW"); self.assertEqual(len(self.broker.calls), 1)
    def test_no_overlap_even_if_below_symbol_ceiling(self):
        self.position("TEST", quantity=1)
        self.order(quantity=1); self.assertEqual(self.broker.calls, [])
    def test_timeout_reserves_symbol_and_allowance(self):
        self.broker.timeout = True
        self.order(); self.order()
        self.assertEqual(len(self.broker.calls), 1)
        self.assertEqual(a.orders()[-1]["status"], "PENDING")
    def test_partial_cancel_keeps_reservation(self):
        local_id = self.order()
        self.broker.history = {"status": "CANCELLED", "filled_quantity": 1}
        a.reconcile_order(local_id); self.order()
        self.assertEqual(len(self.broker.calls), 1)
        self.assertEqual(a.orders()[-1]["status"], "PARTIAL_UNRESOLVED")
    def test_loss_latched_across_midnight_restart_and_paper_enable(self):
        self.position("LOSS", closed_pnl=-2500)
        self.order(); self.assertTrue(a.get_config()["kill_switch"])
        Clock.value += timedelta(days=1)
        start_autotrade_monitor()
        for mode in ("paper", "live"):
            with self.assertRaisesRegex(ValueError, "latched"):
                a.set_enabled(mode, a.LIVE_CONFIRMATION)
    def test_cumulative_app_carries_days(self):
        Clock.value = Clock.value.replace(hour=16, minute=20)
        self.position("DAY1", closed_pnl=-1400); risk.check(self.broker)
        Clock.value += timedelta(days=1)
        self.position("DAY2", closed_pnl=-1100)
        with self.assertRaisesRegex(ValueError, "latched"): risk.check(self.broker)
        self.assertEqual(risk.load()["app_cumulative_pnl"], -2500)
    def test_missing_day_blocks_buy_but_not_confirmed_sell(self):
        pos = self.position()
        Clock.value += timedelta(days=1)
        self.order(); self.assertEqual(self.broker.calls, [])
        a._record_live_order("HELD", "manual close", "SELL", 10, None, position_id=pos)
        self.assertEqual(len(self.broker.calls), 1)
        self.assertIn("Missing finalized", a.get_config()["risk_history_error"])
    def test_cumulative_app_has_no_day_filter(self):
        self.position("OLD", closed_pnl=-1400)
        Clock.value += timedelta(days=1)
        self.position("NEW", closed_pnl=-1100)
        risk.check(self.broker, block_buy=False)
        self.assertTrue(a.get_config()["loss_kill_switch"])
        self.assertEqual(a.get_config()["app_cumulative_pnl"], -2500)
    def test_reset_excludes_old_realized_and_unrealized_losses(self):
        self.position("OLD", closed_pnl=-3000)
        self.position("HELD", entry=400)
        risk.clear(risk.CLEAR_CONFIRMATION)
        risk.check(self.broker)
        self.assertEqual(risk.load()["app_cumulative_pnl"], 0)
        self.broker.price = 90
        risk.check(self.broker)
        self.assertEqual(risk.load()["app_cumulative_pnl"], -100)
        self.assertFalse(a.get_config()["enabled"])
    def test_reset_baseline_survives_position_closure(self):
        pos = self.position(entry=120)
        risk.clear(risk.CLEAR_CONFIRMATION)  # -200 unrealized excluded
        conn = a.connection()
        conn.execute("UPDATE autotrade_positions SET status='CLOSED',exit_time=?,realized_pnl=-250,exit_price=95 WHERE id=?", (a.now(), pos))
        conn.commit(); conn.close()
        risk.check(self.broker)
        self.assertEqual(risk.load()["app_cumulative_pnl"], -50)
    def test_reset_requires_phrase_and_complete_data(self):
        risk.latch(manual=True, kite=self.broker)
        with self.assertRaises(ValueError): risk.clear("clear kill switch")
        self.position()
        self.broker.ltp = lambda _: {}
        with self.assertRaises(ValueError): risk.clear(risk.CLEAR_CONFIRMATION)
        self.assertTrue(a.get_config()["kill_switch"])
    def test_reset_refuses_unresolved_order(self):
        self.order()
        with self.assertRaisesRegex(ValueError, "outstanding"): risk.clear(risk.CLEAR_CONFIRMATION)
    def test_manual_kill_cancels_and_blocks_sells(self):
        self.order()
        risk.latch(manual=True, kite=self.broker)
        pos = self.position()
        a._record_live_order("HELD", "manual close", "SELL", 10, None, position_id=pos)
        self.assertEqual(len(self.broker.calls), 1)
        self.assertEqual(len(self.broker.cancelled), 1)
    def test_cancellation_failure_does_not_clear_latch(self):
        self.order(); self.broker.cancel_failure = True
        risk.latch(manual=True, kite=self.broker)
        self.assertTrue(a.get_config()["kill_switch"])
        self.assertTrue(a.get_config()["cancellation_errors"])
    def test_missing_pnl_blocks_buy(self):
        self.position()
        self.broker.ltp = lambda _: {}
        self.order(); self.assertEqual(self.broker.calls, [])
    def test_market_and_chase_cannot_be_saved(self):
        for changes in [{"live_order_type": "MARKET"}, {"chase_orders": True}]:
            with self.assertRaises(ValueError): self.configure(**changes)
    def test_invalid_limits(self):
        for value in [-1, 0, float("nan"), float("inf"), True, "1000"]:
            with self.assertRaises(ValueError): a.save_config({"max_symbol_value": value})
        with self.assertRaises(ValueError): a.save_config({"max_concurrent_positions": 1.5})
        with self.assertRaises(ValueError): a.save_config({"max_symbol_value": 1100})
    def test_legacy_allocation_fields_ignored_for_live_sizing(self):
        a.save_config({"live_trade_value": 1, "max_deployed_pct": 1, "max_order_value": 1})
        self.configure()
        a.process_signals([{"symbol": "TEST", "signal_type": "BUY", "trigger_date": Clock.now(a.IST).date().isoformat(), "trigger_price": 10000}], 1)
        self.assertEqual(self.broker.calls[0]["quantity"], 10)
        self.assertNotIn("live_trade_value", a.get_config())
    def test_appreciated_position_exits_in_one_order_for_every_sell_source(self):
        # ₹1,000 entries appreciate to ₹2,500; every exit path sells all shares.
        for index, source in enumerate(("manual close", "scanner exit", "risk exit")):
            symbol = f"GAIN{index}"
            pos = self.position(symbol, quantity=10, entry=100)
            self.broker.price = 250
            local_id = a._record_live_order(symbol, source, "SELL", 10, None, position_id=pos)
            self.assertEqual(a.orders()[0]["id"], local_id)
            self.assertEqual(a.orders()[0]["status"], "OPEN")
        self.assertEqual(len(self.broker.calls), 3)
        for order in self.broker.calls:
            self.assertEqual(order["transaction_type"], "SELL")
            self.assertEqual(order["quantity"], 10)
            self.assertEqual(order["price"], 250)
            self.assertEqual(order["order_type"], "LIMIT")

    def test_large_sell_still_requires_matching_position(self):
        self.broker.price = 250
        pos = self.position(quantity=10)
        a._record_live_order("HELD", "manual close", "SELL", 11, None, position_id=pos)
        a._record_live_order("OTHER", "manual close", "SELL", 10, None, position_id=pos)
        self.assertEqual(self.broker.calls, [])

    def test_large_sell_allowed_after_buy_allowance_exhausted(self):
        for i in range(5): self.order(str(i))
        pos = self.position()
        self.broker.price = 250
        a._record_live_order("HELD", "manual close", "SELL", 10, None, position_id=pos)
        self.assertEqual(len(self.broker.calls), 6)
        self.assertEqual(self.broker.calls[-1]["transaction_type"], "SELL")
    def test_reconcile_history_preserves_baseline(self):
        baseline = risk.load()["cumulative_loss_baseline"]
        Clock.value += timedelta(days=1)
        risk.reconcile_day("2026-09-07", [], risk.RECONCILE_CONFIRMATION)
        risk.check(self.broker)
        self.assertEqual(risk.load()["app_cumulative_pnl"], 0)
        self.assertEqual(risk.load()["cumulative_loss_baseline"], baseline)
        self.assertFalse(a.get_config()["enabled"])
    def test_monitor_checks_while_paused(self):
        a.pause(); self.position("LOSS", closed_pnl=-2500)
        a.monitor_live_positions()
        self.assertTrue(a.get_config()["loss_kill_switch"])
        self.assertEqual(self.broker.calls, [])
    def test_legacy_oversized_exposure_blocks_new_buys(self):
        self.position("LEGACY", entry=150, quantity=10)
        self.order("NEW")
        self.assertEqual(self.broker.calls, [])

    def test_next_day_buy_allowance_resets_without_loss_reset(self):
        for i in range(5): self.order(str(i))
        Clock.value = Clock.value.replace(hour=16, minute=20)
        self.position("DAY1", closed_pnl=-500)
        risk.check(self.broker)
        Clock.value += timedelta(days=1)
        self.position("DAY2", closed_pnl=-100)
        self.order("NEXTDAY")
        self.assertEqual(len(self.broker.calls), 6)
        self.assertEqual(risk.load()["app_cumulative_pnl"], -600)

    def test_manual_portfolio_is_never_read_even_during_reset(self):
        self.broker.positions = Mock(side_effect=AssertionError("Portfolio positions must not be read"))
        self.broker.holdings = Mock(side_effect=AssertionError("Manual holdings must not be read"))
        self.broker.pnl = -100000
        risk.clear(risk.CLEAR_CONFIRMATION)
        risk.check(self.broker)
        self.assertEqual(risk.load()["app_cumulative_pnl"], 0)
        self.assertFalse(a.get_config()["kill_switch"])
        self.broker.positions.assert_not_called()
        self.broker.holdings.assert_not_called()

    def test_profitable_manual_holdings_cannot_offset_algo_loss(self):
        self.broker.pnl = 100000
        self.broker.holding_rows = [{"quantity":1000,"day_change":100,"exchange":"NSE"}]
        self.position("ALGO", entry=400)
        risk.check(self.broker, block_buy=False)
        self.assertEqual(risk.load()["app_cumulative_pnl"], -3000)
        self.assertTrue(a.get_config()["loss_kill_switch"])

    def test_history_reconciliation_cannot_clear_loss_latch(self):
        self.position("LOSS", closed_pnl=-2500)
        risk.check(self.broker, block_buy=False)
        Clock.value += timedelta(days=1)
        risk.reconcile_day("2026-09-07", [], risk.RECONCILE_CONFIRMATION)
        self.assertTrue(a.get_config()["loss_kill_switch"])

    def test_legacy_migration_preserves_latch_history_and_uncertain_attempts(self):
        with patch.object(a, "DB_PATH", Path(self.temp.name)/"legacy.db"):
            conn = a.connection()
            old = {"mode":"live", "enabled":True, "paused":False, "kill_switch":True,
                   "max_symbol_value":1500, "max_daily_order_count":10, "max_concurrent_positions":10,
                   "live_trade_value":2500, "max_deployed_pct":50}
            conn.execute("INSERT INTO autotrade_config VALUES (1,?,?)", (json.dumps(old), a.now()))
            conn.commit(); conn.close()
            a.order_log("UNKNOWN", "BUY / scanner signal", "live", 10, 100, "ERROR")
            a.order_log("SKIP", "BUY / sizing", "live", 0, None, "ERROR")
            risk.migrate(); risk.migrate()
            start_autotrade_monitor()
            config = a.get_config()
            self.assertTrue(config["kill_switch"])
            self.assertFalse(config["enabled"])
            self.assertEqual(config["max_symbol_value"], 1000)
            self.assertEqual(config["max_concurrent_positions"], 20)
            self.assertEqual(config["max_daily_order_count"], 5)
            self.assertIsNone(config["cumulative_loss_baseline"])
            self.assertNotIn("max_deployed_pct", config)
            conn = a.connection()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM autotrade_buy_reservations").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM autotrade_orders").fetchone()[0],2)
            conn.close()

    def test_baseline_snapshot_is_traceable_to_app_positions(self):
        opened = self.position("OPEN", entry=120)
        closed = self.position("CLOSED", closed_pnl=-50)
        risk.clear(risk.CLEAR_CONFIRMATION)
        values = risk.load()["baseline_trades"]
        self.assertEqual({r["position_id"] for r in values}, {opened, closed})
        self.assertEqual(sum(r["pnl"] for r in values), -250)
        for row in values:
            self.assertEqual(row["pnl"], (row["mark_price"]-row["entry_price"])*row["quantity"])

    def test_day_reconciliation_is_per_trade_and_not_added_to_current_pnl(self):
        pos = self.position(entry=120)
        Clock.value += timedelta(days=1)
        risk.reconcile_day("2026-09-07", [{"position_id":pos,"price":90}], risk.RECONCILE_CONFIRMATION)
        risk.check(self.broker)
        self.assertEqual(risk.load()["app_cumulative_pnl"], -200)  # current quote 100, not historical 90
        conn = risk.connection()
        payload = json.loads(conn.execute("SELECT trades FROM autotrade_app_pnl_days WHERE source='manual'").fetchone()[0])
        conn.close()
        self.assertEqual(payload[0]["position_id"], pos)
        self.assertEqual(payload[0]["pnl"], -300)

    def test_reconcile_rejects_unknown_missing_duplicate_and_portfolio_totals(self):
        pos = self.position()
        Clock.value += timedelta(days=1)
        for prices in (10000, [], [{"position_id":9999,"price":100}],
                       [{"position_id":pos,"price":100},{"position_id":pos,"price":100}]):
            with self.assertRaises(ValueError):
                risk.reconcile_day("2026-09-07", prices, risk.RECONCILE_CONFIRMATION)

    def test_history_lookup_excludes_later_and_paper_trades(self):
        old = self.position("OLD")
        Clock.value += timedelta(days=1)
        self.position("LATER")
        conn = a.connection()
        conn.execute("UPDATE autotrade_positions SET mode='paper' WHERE symbol='LATER'")
        conn.commit(); conn.close()
        self.assertEqual([r["position_id"] for r in risk.history_positions("2026-09-07")], [old])

    def test_old_broker_accounting_cannot_be_reused_as_app_baseline(self):
        conn = risk.connection()
        legacy = {"cumulative_loss_baseline":a.now(), "baseline_bot_pnl":123,
                  "baseline_broker_pnl":-5000, "broker_cumulative_pnl":-5000,
                  "loss_latched":True, "broker_user_id":"TEST"}
        conn.execute("UPDATE autotrade_risk_state SET payload=?", (json.dumps(legacy),))
        conn.commit(); conn.close()
        risk.migrate()
        current = risk.load()
        self.assertTrue(current["loss_latched"])
        self.assertTrue(current["baseline_requires_reset"])
        self.assertIsNone(current["baseline_trades"])
        self.assertNotIn("baseline_broker_pnl", current)
        self.assertNotIn("broker_cumulative_pnl", a.get_config())
        with self.assertRaises(ValueError): risk.check(self.broker)

    def test_paper_remains_simulated(self):
        self.configure(mode="paper")
        a.process_signals([{"symbol": "TEST", "signal_type": "BUY", "trigger_price": 100}], 1)
        self.assertEqual(len(a.positions()), 1)
        self.assertEqual(self.broker.calls, [])

if __name__ == "__main__": unittest.main()
