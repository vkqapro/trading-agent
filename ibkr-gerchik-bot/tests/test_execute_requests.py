"""Tests for the dashboard order-request queue and the execution worker."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.execution import order_requests as oq
from src.jobs import execute_requests as worker


class _Recorder:
    """Minimal OrderResult-like object the fake broker returns."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


class FakeBroker:
    def __init__(self, positions=None):
        self._positions = positions or []
        self.market_orders = []

    def get_positions(self):
        return self._positions

    def place_market_order(self, symbol, action, quantity, tif=None):
        self.market_orders.append((symbol, action, quantity))
        return SimpleNamespace(order_id=99, symbol=symbol, action=action, quantity=quantity, status="Submitted")


class FakeOrderManager:
    def __init__(self, ok=True, payload=None):
        self.ok = ok
        self.payload = payload or {"status": "submitted", "quantity": 100}
        self.dry_run = True
        self.calls = []

    def execute_manual_order(self, signal, **kwargs):
        self.calls.append((signal, self.dry_run, kwargs))
        return self.ok, self.payload


def _paper(dry_run=False):
    return SimpleNamespace(paper_trading=True, dry_run_mode=dry_run)


class ApprovalWorkerTests(unittest.TestCase):
    def test_reviewed_paper_gate_does_not_run_autonomous_market_quality_checks(self) -> None:
        candidate = SimpleNamespace(symbol="AAPL", entry=100.0, stop=99.0)
        settings = SimpleNamespace(
            risk=SimpleNamespace(risk_per_trade=0.01, max_position_value=25000.0),
            decision_agent=SimpleNamespace(ibkr_paper_risk_per_trade_pct=0.10),
        )
        with patch.object(worker, "SETTINGS", settings), patch.object(
            worker, "AutonomousRiskGate", side_effect=AssertionError("autonomous gate must not run"), create=True
        ):
            result = worker._reviewed_paper_gate(
                candidate,
                account_id="DU123",
                paper_verified=True,
                account_allowed=True,
                account_equity=100000.0,
                cash_available=100000.0,
            )
        self.assertTrue(result["approved"])
        self.assertGreater(result["quantity"], 0)
        self.assertEqual(result["reasons"], [])

    def test_approved_telegram_rows_enter_existing_intent_pipeline(self) -> None:
        calls = []

        class Store:
            def __init__(self, *_args, **_kwargs):
                pass

            def list_approvals(self, *, status=None):
                self.status = status
                return [{"approval_id": "approval_test"}]

            def process_approved(self, approval_id, **kwargs):
                calls.append((approval_id, kwargs))
                return SimpleNamespace(status="EXECUTION_READY", message="queued")

        broker = SimpleNamespace(
            get_account_identity=lambda: {"paper_verified": True, "account_id": "DU123"},
            is_connected=True,
        )
        manager = SimpleNamespace()
        with patch.object(worker, "ApprovalStore", Store):
            result = worker.process_approved_once(broker, manager, 100000.0, 100000.0, [])
        self.assertEqual(result[0]["status"], "EXECUTION_READY")
        self.assertEqual(calls[0][0], "approval_test")
        self.assertIn("risk_gate", calls[0][1])
        self.assertIn("enqueue", calls[0][1])

    def test_immutable_approval_fails_closed_without_verified_paper_identity(self) -> None:
        request = {"action": "place", "immutable_intent": True, "live": False, "approval_id": "approval_test"}
        settings = SimpleNamespace(paper_trading=True)
        with patch.object(worker, "SETTINGS", settings):
            result = worker._process_one(request, broker=SimpleNamespace(), order_manager=SimpleNamespace(),
                                         account_equity=100000.0, cash_available=100000.0, tracked_positions=[])
        self.assertEqual(result["status"], oq.REJECTED)
        self.assertIn("ACCOUNT_NOT_PAPER", result["message"])

    def test_verified_paper_approval_submits_once_and_persists_actual_statuses(self) -> None:
        calls = []

        class Store:
            def __init__(self, *_args, **_kwargs):
                pass

            def get(self, approval_id):
                return {"approval_id": approval_id, "status": "EXECUTION_READY", "setup_id": "setup-1",
                        "plan_hash": "hash-1", "symbol": "AAPL", "direction": "long"}

            def mark_execution(self, approval_id, status, **kwargs):
                calls.append((approval_id, status, kwargs))
                return True

        class Manager:
            dry_run = False

            def execute_manual_order(self, signal, **kwargs):
                self.signal = signal
                self.kwargs = kwargs
                return True, {
                    "status": "executed", "market_order_id": 101, "stop_order_id": 102,
                    "limit_order_id": 103, "broker_perm_id": 9001,
                    "broker_statuses": {"market_order": "PreSubmitted", "stop_order": "Submitted", "limit_order": "Submitted"},
                }

        request = {"action": "place", "immutable_intent": True, "live": False,
                   "approval_id": "approval-1", "execution_intent_id": "intent-1",
                   "setup_id": "setup-1", "plan_hash": "hash-1", "symbol": "AAPL",
                   "direction": "long", "entry": 100, "stop": 99, "target": 103, "quantity": 5,
                   "strategy": "PRB1"}
        settings = SimpleNamespace(
            paper_trading=True, dry_run_mode=False,
            decision_agent=SimpleNamespace(database_path="unused"),
            risk=SimpleNamespace(max_spread_pct=0.01),
        )
        broker = SimpleNamespace(get_account_identity=lambda: {"paper_verified": True, "account_id": "DU123"})
        with patch.object(worker, "SETTINGS", settings), patch.object(worker, "ApprovalStore", Store):
            result = worker._process_one(request, broker=broker, order_manager=Manager(),
                                         account_equity=100000.0, cash_available=100000.0, tracked_positions=[])
        self.assertEqual(result["status"], oq.DONE)
        self.assertEqual(calls[0][1], "SUBMITTING")
        self.assertEqual(calls[1][1], "EXECUTED")
        details = calls[1][2]["broker_details"]
        self.assertEqual(details["account_id"], "DU123")
        self.assertEqual(details["perm_id"], 9001)
        self.assertEqual(details["broker_status"], "PreSubmitted")
        self.assertEqual(details["stop_order_id"], 102)
        self.assertEqual(details["limit_order_id"], 103)
        self.assertTrue(details["outside_rth"])
        self.assertEqual(details["tif"], "GTC")

    def test_telegram_intent_uses_reviewed_manual_service_not_autonomous_trade(self) -> None:
        class Store:
            def __init__(self, *_args, **_kwargs):
                pass

            def get(self, approval_id):
                return {"approval_id": approval_id, "status": "EXECUTION_READY", "setup_id": "setup-1",
                        "plan_hash": "hash-1", "symbol": "AAPL", "direction": "long"}

            def mark_execution(self, *_args, **_kwargs):
                return True

        class Manager:
            dry_run = False

            def __init__(self):
                self.manual_calls = []
                self.autonomous_calls = []

            def execute_manual_order(self, signal, **kwargs):
                self.manual_calls.append((signal, kwargs))
                return True, {"status": "executed", "market_order_id": 1, "stop_order_id": 2,
                              "limit_order_id": 3, "broker_statuses": {"market_order": "Submitted"}}

            def execute_trade(self, signal, **kwargs):
                self.autonomous_calls.append((signal, kwargs))
                raise AssertionError("Telegram approval must not use autonomous execute_trade")

        request = {"action": "place", "immutable_intent": True, "live": False,
                   "approval_id": "approval-1", "execution_intent_id": "intent-1",
                   "setup_id": "setup-1", "plan_hash": "hash-1", "symbol": "AAPL",
                   "direction": "long", "entry": 100, "stop": 99, "target": 103, "quantity": 5,
                   "strategy": "PRB1", "entry_order_type": "MARKET"}
        manager = Manager()
        settings = SimpleNamespace(
            paper_trading=True, dry_run_mode=False,
            decision_agent=SimpleNamespace(database_path="unused"),
        )
        broker = SimpleNamespace(get_account_identity=lambda: {"paper_verified": True, "account_id": "DU123"})
        with patch.object(worker, "SETTINGS", settings), patch.object(worker, "ApprovalStore", Store):
            result = worker._process_one(request, broker=broker, order_manager=manager,
                                         account_equity=100000.0, cash_available=100000.0, tracked_positions=[])
        self.assertEqual(result["status"], oq.DONE)
        self.assertEqual(len(manager.manual_calls), 1)
        self.assertEqual(manager.manual_calls[0][1]["quantity"], 5)
        self.assertTrue(manager.manual_calls[0][1]["allow_extended_hours_order"])
        self.assertEqual(manager.manual_calls[0][1]["time_in_force"], "GTC")
        self.assertEqual(manager.autonomous_calls, [])

    def test_broker_exception_after_submission_is_unknown_and_not_retried(self) -> None:
        calls = []

        class Store:
            def __init__(self, *_args, **_kwargs):
                pass

            def get(self, approval_id):
                return {"approval_id": approval_id, "status": "EXECUTION_READY", "setup_id": "setup-1",
                        "plan_hash": "hash-1", "symbol": "AAPL", "direction": "long"}

            def mark_execution(self, approval_id, status, **kwargs):
                calls.append((approval_id, status, kwargs))
                return True

        class Manager:
            dry_run = False

            def execute_manual_order(self, signal, **kwargs):
                raise TimeoutError("response lost after send")

        request = {"action": "place", "immutable_intent": True, "live": False,
                   "approval_id": "approval-1", "execution_intent_id": "intent-1",
                   "setup_id": "setup-1", "plan_hash": "hash-1", "symbol": "AAPL",
                   "direction": "long", "entry": 100, "stop": 99, "target": 103, "quantity": 5,
                   "strategy": "PRB1"}
        settings = SimpleNamespace(
            paper_trading=True, dry_run_mode=False,
            decision_agent=SimpleNamespace(database_path="unused"),
            risk=SimpleNamespace(max_spread_pct=0.01),
        )
        broker = SimpleNamespace(get_account_identity=lambda: {"paper_verified": True, "account_id": "DU123"})
        with patch.object(worker, "SETTINGS", settings), patch.object(worker, "ApprovalStore", Store):
            result = worker._process_one(request, broker=broker, order_manager=Manager(),
                                         account_equity=100000.0, cash_available=100000.0, tracked_positions=[])
        self.assertEqual(result["status"], oq.ERROR)
        self.assertIn("SUBMISSION_UNKNOWN", result["message"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[-1][1], "EXECUTION_FAILED")
        self.assertEqual(calls[-1][2]["broker_details"]["submission_outcome"], "UNKNOWN")


class OrderRequestQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        path = Path(self._dir.name) / "order_requests.json"
        self._patchers = [
            patch.object(oq, "ORDER_REQUESTS_PATH", path),
            patch.object(oq, "LOCK_PATH", path.with_suffix(".json.lock")),
            patch.object(oq, "HEARTBEAT_PATH", path.with_name("execute_worker.json")),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(self._dir.cleanup)
        for p in self._patchers:
            self.addCleanup(p.stop)

    def test_submit_and_claim_marks_processing_once(self) -> None:
        oq.submit_place({"symbol": "AAPL", "signal": "BUY", "direction": "long",
                         "entry": 100, "stop": 99, "target": 104}, live=True)
        self.assertEqual(len(oq.pending_requests()), 1)
        claimed = oq.claim_pending()
        self.assertEqual(len(claimed), 1)
        # Claimed request is now PROCESSING (in-flight) and cannot be re-claimed.
        self.assertEqual(oq.claim_pending(), [])
        record = oq.list_requests()[0]
        self.assertEqual(record["status"], oq.PROCESSING)

    def test_telegram_intent_uses_the_shared_place_request_shape(self) -> None:
        request_id = oq.submit_approval_intent({
            "approval_id": "approval-1", "execution_intent_id": "intent-1",
            "setup_id": "setup-1", "plan_hash": "hash-1", "symbol": "AAPL",
            "direction": "LONG", "entry": 100, "stop": 99, "target": 103,
            "quantity": 5, "strategy": "PRB1",
        })
        record = next(item for item in oq.list_requests() if item["id"] == request_id)
        self.assertEqual(record["source"], "telegram_approval")
        self.assertTrue(record["immutable_intent"])
        self.assertTrue(record["manual_setup"])
        self.assertEqual(record["entry"], 100)
        self.assertEqual(record["target"], 103)

    def test_update_writes_result_back(self) -> None:
        rid = oq.submit_close("MSFT")
        oq.update_request(rid, status=oq.DONE, message="closed", result={"x": 1})
        record = next(r for r in oq.list_requests() if r["id"] == rid)
        self.assertEqual(record["status"], oq.DONE)
        self.assertEqual(record["result"], {"x": 1})

    def test_heartbeat_freshness(self) -> None:
        self.assertFalse(oq.worker_is_alive())  # none written yet
        oq.write_heartbeat()
        self.assertTrue(oq.worker_is_alive())
        self.assertLess(oq.worker_age_seconds(), 5)
        # An old heartbeat is not alive.
        import json as _json
        oq.HEARTBEAT_PATH.write_text(_json.dumps({"pid": 1, "ts": 0.0}), encoding="utf-8")
        self.assertFalse(oq.worker_is_alive())


class ExecuteWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        path = Path(self._dir.name) / "order_requests.json"
        for p in (
            patch.object(oq, "ORDER_REQUESTS_PATH", path),
            patch.object(oq, "LOCK_PATH", path.with_suffix(".json.lock")),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._dir.cleanup)

    def test_place_live_appends_tracked_position(self) -> None:
        oq.submit_place({"symbol": "AAPL", "signal": "BUY", "direction": "long",
                         "entry": 100, "stop": 99, "target": 104, "strategy": "x"}, live=True)
        tracked: list = []
        om = FakeOrderManager(ok=True, payload={"status": "submitted", "signal": {"quantity": 50},
                                                "market_order_id": 1})
        with patch.object(worker, "SETTINGS", _paper(dry_run=True)):
            processed = worker.process_pending_once(FakeBroker(), om, 100_000.0, 100_000.0, tracked)
        self.assertEqual(processed[0]["status"], oq.DONE)
        self.assertEqual(om.dry_run, False)  # live request forces real submission
        self.assertEqual(len(tracked), 1)
        self.assertEqual(tracked[0]["symbol"], "AAPL")

    def test_place_respects_dry_run_when_not_live(self) -> None:
        oq.submit_place({"symbol": "AAPL", "signal": "BUY", "direction": "long",
                         "entry": 100, "stop": 99, "target": 104}, live=False)
        tracked: list = []
        om = FakeOrderManager(ok=True, payload={"status": "simulated"})
        with patch.object(worker, "SETTINGS", _paper(dry_run=True)):
            processed = worker.process_pending_once(FakeBroker(), om, 100_000.0, 100_000.0, tracked)
        self.assertEqual(processed[0]["status"], oq.SIMULATED)
        self.assertEqual(om.dry_run, True)
        self.assertEqual(tracked, [])  # simulated -> no tracked position

    def test_manual_setup_requires_stop_and_target_before_worker_submission(self) -> None:
        oq.submit_place({
            "symbol": "AAPL",
            "signal": "BUY",
            "direction": "long",
            "entry": 100,
            "stop": 99,
            "target": None,
            "manual_setup": True,
        }, live=False)
        order_manager = FakeOrderManager()
        with patch.object(worker, "SETTINGS", _paper(dry_run=False)):
            processed = worker.process_pending_once(FakeBroker(), order_manager, 100_000.0, 100_000.0, [])
        self.assertEqual(processed[0]["status"], oq.REJECTED)
        self.assertIn("requires both a positive Stop and Target", processed[0]["message"])
        self.assertEqual(order_manager.calls, [])

    def test_paper_only_gate_rejects_when_not_paper(self) -> None:
        oq.submit_place({"symbol": "AAPL", "signal": "BUY", "direction": "long",
                         "entry": 100, "stop": 99, "target": 104}, live=True)
        with patch.object(worker, "SETTINGS", SimpleNamespace(paper_trading=False, dry_run_mode=False)):
            processed = worker.process_pending_once(FakeBroker(), FakeOrderManager(), 100_000.0, 100_000.0, [])
        self.assertEqual(processed[0]["status"], oq.REJECTED)
        self.assertIn("PAPER_TRADING", processed[0]["message"])

    def test_close_live_places_opposite_market_order_and_untracks(self) -> None:
        oq.submit_close("AAPL", live=True)
        broker = FakeBroker(positions=[{"symbol": "AAPL", "position": 50, "avg_cost": 100}])
        tracked = [{"symbol": "AAPL", "quantity": 50}]
        with patch.object(worker, "SETTINGS", _paper(dry_run=False)):
            processed = worker.process_pending_once(broker, FakeOrderManager(), 100_000.0, 100_000.0, tracked)
        self.assertEqual(processed[0]["status"], oq.DONE)
        self.assertEqual(broker.market_orders, [("AAPL", "SELL", 50)])
        self.assertEqual(tracked, [])

    def test_close_without_position_errors(self) -> None:
        oq.submit_close("AAPL", live=True)
        with patch.object(worker, "SETTINGS", _paper(dry_run=False)):
            processed = worker.process_pending_once(FakeBroker(positions=[]), FakeOrderManager(),
                                                    100_000.0, 100_000.0, [])
        self.assertEqual(processed[0]["status"], oq.ERROR)
        self.assertIn("No open broker position", processed[0]["message"])


class ManualOrderTests(unittest.TestCase):
    def _signal(self):
        from src.strategy.signal_models import TradeSignal
        return TradeSignal(
            symbol="BIRD", strategy="dashboard", signal="SELL", direction="short",
            entry=5.0, stop=5.2, target=4.0, level_price=5.0, level_type="gap",
        )

    def test_manual_order_caps_size_and_bypasses_gates(self) -> None:
        from src.config import SETTINGS
        from src.execution.order_manager import OrderManager
        om = OrderManager(None, None, None, None, dry_run=True)
        with patch("src.execution.order_manager.append_markdown_log"):
            ok, payload = om.execute_manual_order(
                self._signal(), account_equity=100_000.0, cash_available=100_000.0
            )
        self.assertTrue(ok)
        self.assertEqual(payload["status"], "simulated")
        self.assertGreater(payload["quantity"], 0)
        # Notional is capped at the configured max position value, not rejected.
        self.assertLessEqual(payload["quantity"] * 5.0, SETTINGS.risk.max_position_value + 5.0)

    def test_manual_order_honors_supplied_quantity(self) -> None:
        from src.execution.order_manager import OrderManager
        om = OrderManager(None, None, None, None, dry_run=True)
        with patch("src.execution.order_manager.append_markdown_log"):
            ok, payload = om.execute_manual_order(
                self._signal(), account_equity=100_000.0, cash_available=100_000.0, quantity=62
            )
        self.assertTrue(ok)
        self.assertEqual(payload["quantity"], 62)  # forecast size, not re-sized from live config

    def test_manual_order_rejects_only_when_unaffordable(self) -> None:
        from src.execution.order_manager import OrderManager
        om = OrderManager(None, None, None, None, dry_run=True)
        with patch("src.execution.order_manager.append_markdown_log"):
            ok, payload = om.execute_manual_order(
                self._signal(), account_equity=100_000.0, cash_available=1.0
            )
        self.assertFalse(ok)
        self.assertIn("insufficient_cash_or_position_value", payload["reasons"])

    def test_manual_order_requires_target(self) -> None:
        from src.execution.order_manager import OrderManager
        signal = self._signal()
        signal.target = 0
        om = OrderManager(None, None, None, None, dry_run=True)
        with patch("src.execution.order_manager.append_markdown_log"):
            ok, payload = om.execute_manual_order(
                signal, account_equity=100_000.0, cash_available=100_000.0
            )
        self.assertFalse(ok)
        self.assertIn("manual_setup_requires_target", payload["reasons"])


class TickRoundingTests(unittest.TestCase):
    def test_round_to_tick(self) -> None:
        from src.brokers.ibkr import IBKRClient
        self.assertEqual(IBKRClient._round_to_tick(241.3297), 241.33)
        self.assertEqual(IBKRClient._round_to_tick(239.2503), 239.25)
        self.assertEqual(IBKRClient._round_to_tick(0.38123), 0.3812)  # sub-$1 finer tick
        self.assertIsNone(IBKRClient._round_to_tick(None))

    def test_forecast_candidate_prices_are_tick_valid(self) -> None:
        from dashboard.forecast import projected_level_candidates
        watchlist = {
            "ASND": {
                "daily_atr": 7.0, "news_blocked": False,
                "level_spacing": {"current_price": 250.0},
                "levels": [{
                    "price": 240.29, "zone_low": 239.2503, "zone_high": 241.3297,
                    "nearest_upper_level": 260.0, "nearest_lower_level": 222.75,
                    "type": "gap", "strength_score": 29.5,
                }],
            }
        }
        for candidate in projected_level_candidates(watchlist):
            for key in ("entry", "stop", "target"):
                value = candidate[key]
                self.assertEqual(round(value, 2), value, f"{key}={value} not 1c-conformant")


if __name__ == "__main__":
    unittest.main()
