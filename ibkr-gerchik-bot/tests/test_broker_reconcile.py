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
