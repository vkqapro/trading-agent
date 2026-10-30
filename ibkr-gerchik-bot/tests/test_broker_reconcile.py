from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from src.journal import broker_reconcile


class BrokerReconcileTests(TestCase):
    def test_broker_close_uses_first_seen_date_when_fill_timestamp_is_unavailable(self) -> None:
        record = broker_reconcile._execution_closed_record(
            {
                "symbol": "LIN",
                "order_id": 820,
                "exec_id": "lin-close",
                "shares": 2,
                "price": 481.0,
                "time": "2026-08-27T13:31:07+00:00",
            },
            None,
            {
                "symbol": "LIN",
                "avg_cost": 480.62,
                "first_seen_at": "2026-08-24",
                "stop_order_id": 820,
                "limit_order_id": 821,
            },
        )

        self.assertEqual(record["opened_at"], "2026-08-24")
        self.assertEqual(record["closed_at"], "2026-08-27T13:31:07+00:00")
        self.assertEqual(record["stop_order_id"], 820)
        self.assertEqual(record["limit_order_id"], 821)
        self.assertEqual(record["exit_reason"], "BRACKET_STOP_FILLED")

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

    def test_unmatched_sell_execution_is_persisted_as_broker_observed_close(self) -> None:
        writes: list[dict] = []

        def fake_read(path, default):
            if str(path).endswith("order_requests.json"):
                return {"requests": []}
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
                        "symbol": "LIN",
                        "order_id": 832,
                        "perm_id": 1108835607,
                        "exec_id": "lin-exec",
                        "side": "SLD",
                        "shares": 2,
                        "price": 531.25,
                        "time": "2026-08-27T13:31:07+00:00",
                        "commission": 1.0,
                        "realized_pnl": 14.5,
                    }
                ],
            )

        self.assertEqual(added, 1)
        record = writes[0]["positions"][0]
        self.assertEqual(record["symbol"], "LIN")
        self.assertEqual(record["quantity"], 2)
        self.assertEqual(record["exit_quantity"], 2)
        self.assertEqual(record["exit_execution_id"], "lin-exec")
        self.assertEqual(record["exit_reason"], "BROKER_SELL_EXECUTION")
        self.assertEqual(record["request_id"], "")
        self.assertEqual(record["source"], "IBKR")
        self.assertEqual(record["commission"], 1.0)
        self.assertEqual(record["broker_realized_pnl"], 14.5)

    def test_execution_before_request_is_not_linked_to_later_position(self) -> None:
        order_requests = {
            "requests": [
                {
                    "id": "snap-later",
                    "action": "place",
                    "status": "done",
                    "created_at": "2026-10-13T10:38:07",
                    "updated_at": "2026-10-13T10:38:09",
                    "source": "MARKET_SCREENER_MANUAL",
                    "symbol": "SNAP",
                    "signal": "BUY",
                    "entry": 6.49,
                    "quantity": 77,
                    "result": {
                        "status": "executed",
                        "quantity": 77,
                        "market_order_id": 1032,
                        "stop_order_id": 1033,
                        "limit_order_id": 1034,
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
                before={
                    "SNAP": {
                        "symbol": "SNAP",
                        "quantity": 9,
                        "avg_cost": 6.40917705,
                        "opened_at": "2026-10-13T10:38:09",
                        "market_order_id": 1032,
                        "stop_order_id": 1033,
                        "limit_order_id": 1034,
                    }
                },
                synced=[],
                executions=[
                    {
                        "symbol": "SNAP",
                        "order_id": 10893,
                        "perm_id": 1196543062,
                        "exec_id": "snap-earlier",
                        "side": "SLD",
                        "shares": 109,
                        "price": 6.64,
                        "time": "2026-10-13T13:54:45+00:00",
                        "realized_pnl": None,
                    }
                ],
                open_orders=[
                    {"symbol": "SNAP", "order_id": 1033, "action": "SELL", "type": "STP"},
                    {"symbol": "SNAP", "order_id": 1034, "action": "SELL", "type": "LMT"},
                ],
            )

        self.assertEqual(added, 1)
        record = writes[0]["positions"][0]
        self.assertEqual(record["symbol"], "SNAP")
        self.assertEqual(record["quantity"], 109)
        self.assertEqual(record["exit_quantity"], 109)
        self.assertEqual(record["request_id"], "")
        self.assertEqual(record["source"], "IBKR")
        self.assertEqual(record["entry_price_source"], "unavailable")
        self.assertIsNone(record["broker_realized_pnl"])

    def test_execution_refresh_preserves_existing_entry_details(self) -> None:
        existing = {
            "positions": [
                {
                    "symbol": "SNAP",
                    "quantity": 100,
                    "entry": 6.40917705,
                    "avg_cost": 6.40917705,
                    "opened_at": "2026-10-12T11:46:19",
                    "side": "BUY",
                    "direction": "long",
                    "source": "MARKET_SCREENER_MANUAL",
                    "exit_price": 6.64,
                    "exit_quantity": 100,
                    "exit_execution_id": "snap-fill",
                    "request_id": "",
                    "entry_price_source": "broker_position",
                }
            ]
        }
        writes: list[dict] = []

        def fake_read(path, default):
            if str(path).endswith("order_requests.json"):
                return {"requests": []}
            return existing

        with (
            patch("src.journal.broker_reconcile._read_json", side_effect=fake_read),
            patch("src.journal.broker_reconcile._write_json", side_effect=lambda _path, payload: writes.append(payload)),
        ):
            added = broker_reconcile._record_closed_positions(
                before={},
                synced=[],
                executions=[
                    {
                        "symbol": "SNAP",
                        "order_id": 10893,
                        "perm_id": 1196543062,
                        "exec_id": "snap-fill",
                        "side": "SLD",
                        "shares": 100,
                        "price": 6.64,
                        "time": "2026-10-13T13:54:45+00:00",
                        "realized_pnl": None,
                    }
                ],
            )

        self.assertEqual(added, 0)
        self.assertEqual(len(writes), 1)
        record = existing["positions"][0]
        self.assertEqual(record["avg_cost"], 6.40917705)
        self.assertEqual(record["opened_at"], "2026-10-12T11:46:19")
        self.assertEqual(record["side"], "BUY")
        self.assertEqual(record["source"], "MARKET_SCREENER_MANUAL")
        self.assertEqual(record["exit_quantity"], 100)
        self.assertEqual(record["closed_at"], "2026-10-13T13:54:45+00:00")
        self.assertEqual(record["exit_order_id"], 10893)

    def test_ambiguous_historical_requests_do_not_override_execution_facts(self) -> None:
        order_requests = {
            "requests": [
                {
                    "id": "mrsh-five",
                    "action": "place",
                    "status": "done",
                    "updated_at": "2026-07-29T10:41:19",
                    "symbol": "MRSH",
                    "quantity": 5,
                    "result": {"market_order_id": 627, "stop_order_id": 628, "limit_order_id": 629},
                },
                {
                    "id": "mrsh-two",
                    "action": "place",
                    "status": "done",
                    "updated_at": "2026-07-29T10:45:10",
                    "symbol": "MRSH",
                    "quantity": 2,
                    "result": {"market_order_id": 639, "stop_order_id": 640, "limit_order_id": 641},
                },
            ]
        }
        existing = {
            "positions": [
                {
                    "symbol": "MRSH",
                    "quantity": 2,
                    "request_id": "mrsh-two",
                    "exit_execution_id": "mrsh-today",
                    "exit_quantity": 5,
                }
            ]
        }
        writes: list[dict] = []

        def fake_read(path, default):
            if str(path).endswith("order_requests.json"):
                return order_requests
            return existing

        with (
            patch("src.journal.broker_reconcile._read_json", side_effect=fake_read),
            patch("src.journal.broker_reconcile._write_json", side_effect=lambda _path, payload: writes.append(payload)),
        ):
            added = broker_reconcile._record_closed_positions(
                before={},
                synced=[],
                executions=[
                    {
                        "symbol": "MRSH",
                        "order_id": 831,
                        "perm_id": 1108835606,
                        "exec_id": "mrsh-today",
                        "side": "SLD",
                        "shares": 5,
                        "price": 190.19,
                        "time": "2026-08-27T13:30:25+00:00",
                    }
                ],
            )

        self.assertEqual(added, 0)
        record = writes[0]["positions"][0]
        self.assertEqual(record["symbol"], "MRSH")
        self.assertEqual(record["quantity"], 5)
        self.assertEqual(record["exit_quantity"], 5)
        self.assertEqual(record["request_id"], "")
        self.assertEqual(record["source"], "IBKR")
