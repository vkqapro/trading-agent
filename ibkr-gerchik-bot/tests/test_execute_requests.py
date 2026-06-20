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

    def place_market_order(self, symbol, action, quantity):
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
