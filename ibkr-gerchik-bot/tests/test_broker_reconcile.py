from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from src.journal import broker_reconcile


class BrokerReconcileTests(TestCase):
    def test_missing_position_without_sell_execution_is_not_closed(self) -> None:
        writes: list[dict] = []

        def fake_read(path, default):
            return {"positions": []}

        def capture_write(path, payload):
            writes.append(payload)

        with (
            patch("src.journal.broker_reconcile._read_json", side_effect=fake_read),
            patch("src.journal.broker_reconcile._write_json", side_effect=capture_write),
        ):
            added = broker_reconcile._record_closed_positions(
                before={
                    "TREX": {
                        "symbol": "TREX",
                        "quantity": 12,
                        "market_order_id": 725,
                        "stop_order_id": 726,
                        "limit_order_id": 727,
                    }
                },
                synced=[],
                executions=[],
            )

        self.assertEqual(added, 0)
        self.assertEqual(writes, [])

    def test_bracket_sell_execution_is_persisted_without_local_open_position(self) -> None:
        order_requests = {
            "requests": [
                {
                    "id": "trex-request",
                    "action": "place",
                    "status": "done",
                    "created_at": "2026-07-27T10:01:37",
                    "updated_at": "2026-07-27T10:01:41",
                    "source": "MARKET_SCREENER_MANUAL",
                    "symbol": "TREX",
                    "signal": "BUY",
                    "entry": 44.08,
                    "quantity": 8,
                    "result": {
                        "status": "executed",
                        "quantity": 8,
                        "market_order_id": 589,
                        "stop_order_id": 590,
                        "limit_order_id": 591,
                    },
                }
            ]
        }
        writes: list[dict] = []

        def fake_read(path, default):
            if str(path).endswith("order_requests.json"):
                return order_requests
            return {"positions": []}

        def capture_write(path, payload):
            writes.append(payload)

        with (
            patch("src.journal.broker_reconcile._read_json", side_effect=fake_read),
            patch("src.journal.broker_reconcile._write_json", side_effect=capture_write),
        ):
            added = broker_reconcile._record_closed_positions(
                before={},
                synced=[],
                executions=[
                    {
                        "symbol": "TREX",
                        "order_id": 591,
                        "perm_id": 1049140103,
                        "exec_id": "trex-exec",
                        "side": "SLD",
                        "shares": 8,
                        "price": 49.79,
                        "time": "2026-08-05T14:14:34+00:00",
                    }
                ],
            )

        self.assertEqual(added, 1)
        record = writes[0]["positions"][0]
        self.assertEqual(record["symbol"], "TREX")
        self.assertEqual(record["limit_order_id"], 591)
        self.assertEqual(record["exit_price"], 49.79)
        self.assertEqual(record["exit_execution_id"], "trex-exec")
        self.assertEqual(record["exit_reason"], "BRACKET_TARGET_FILLED")
        self.assertEqual(record["entry_price_source"], "planned_entry")

    def test_unlinked_market_sell_uses_visible_bracket_to_find_request(self) -> None:
        order_requests = {
            "requests": [
                {
                    "id": "sanm-request",
                    "action": "place",
                    "status": "done",
                    "created_at": "2026-07-31T09:55:16",
                    "updated_at": "2026-07-31T09:55:23",
                    "source": "MARKET_SCREENER_MANUAL",
                    "symbol": "SANM",
                    "signal": "BUY",
                    "entry": 189.83,
                    "stop": 159.97,
                    "target": 249.55,
                    "quantity": 1,
                    "result": {
                        "status": "executed",
                        "quantity": 1,
                        "market_order_id": 667,
                        "stop_order_id": 668,
                        "limit_order_id": 669,
                    },
                }
            ]
        }
        writes: list[dict] = []

        def fake_read(path, default):
            if str(path).endswith("order_requests.json"):
                return order_requests
            return {"positions": []}

        with (
            patch("src.journal.broker_reconcile._read_json", side_effect=fake_read),
            patch("src.journal.broker_reconcile._write_json", side_effect=lambda _path, payload: writes.append(payload)),
        ):
            added = broker_reconcile._record_closed_positions(
                before={},
                synced=[],
                executions=[
                    {
                        "symbol": "SANM",
                        "order_id": 2243,
                        "perm_id": 658207656,
                        "exec_id": "sanm-exec",
                        "side": "SLD",
                        "shares": 1,
                        "price": 200.25,
                        "time": "2026-08-06T13:49:51+00:00",
                    }
                ],
                open_orders=[
                    {"symbol": "SANM", "order_id": 668, "action": "SELL", "type": "STP"},
                    {"symbol": "SANM", "order_id": 669, "action": "SELL", "type": "LMT"},
                ],
            )

        self.assertEqual(added, 1)
        record = writes[0]["positions"][0]
        self.assertEqual(record["symbol"], "SANM")
        self.assertEqual(record["exit_order_id"], 2243)
        self.assertEqual(record["exit_price"], 200.25)
        self.assertEqual(record["exit_reason"], "BROKER_SELL_EXECUTION")
        self.assertEqual(record["request_id"], "sanm-request")
