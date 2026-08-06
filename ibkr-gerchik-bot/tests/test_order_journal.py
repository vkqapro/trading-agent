from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from src.journal import order_journal


class OrderJournalTests(TestCase):
    def _read_json(self, order_requests: dict, closed_positions: dict):
        def fake_read(path, default):
            path_text = str(path)
            if path_text.endswith("order_requests.json"):
                return order_requests
            if path_text.endswith("closed_positions.json"):
                return closed_positions
            return default

        return fake_read

    def test_observed_broker_close_wins_over_bracket_bar_inference(self) -> None:
        order_requests = {
            "requests": [
                {
                    "id": "entry1",
                    "action": "place",
                    "status": "done",
                    "created_at": "2026-07-09T15:16:26",
                    "updated_at": "2026-07-09T15:16:32",
                    "source": "PROJECTED",
                    "symbol": "GCTS",
                    "signal": "BUY",
                    "entry": 2.57,
                    "stop": 2.36,
                    "target": 3.1475,
                    "quantity": 71,
                    "result": {"market_order_id": 458, "quantity": 71, "price": 2.57},
                }
            ]
        }
        closed_positions = {
            "positions": [
                {
                    "symbol": "GCTS",
                    "market_order_id": 458,
                    "opened_at": "2026-07-09T15:16:32",
                    "closed_at": "2026-07-15T14:30:00+00:00",
                    "exit_execution_id": "gcts-exec",
                    "exit_reason": "BROKER_POSITION_CLOSED",
                }
            ]
        }

        with (
            patch("src.journal.order_journal._read_json", side_effect=self._read_json(order_requests, closed_positions)),
            patch("src.journal.order_journal._infer_bracket_exit_from_bars") as infer_exit,
        ):
            rows = order_journal._rows_from_order_requests({})

        infer_exit.assert_not_called()
        self.assertEqual(rows[0]["status"], "CLOSED_OBSERVED")
        self.assertEqual(rows[0]["closed_at"], "2026-07-15T14:30:00+00:00")
        self.assertEqual(rows[0]["exit_reason"], "BROKER_POSITION_CLOSED")

    def test_unverified_broker_position_close_is_ignored(self) -> None:
        order_requests = {
            "requests": [
                {
                    "id": "trex-working-entry",
                    "action": "place",
                    "status": "done",
                    "created_at": "2026-08-05T11:30:45",
                    "updated_at": "2026-08-05T11:30:49",
                    "source": "MARKET_SCREENER_MANUAL",
                    "symbol": "TREX",
                    "signal": "BUY",
                    "entry": 47.70,
                    "stop": 45.50,
                    "target": 52.10,
                    "quantity": 12,
                    "result": {
                        "status": "executed",
                        "quantity": 12,
                        "market_order_id": 725,
                        "stop_order_id": 726,
                        "limit_order_id": 727,
                        "broker_statuses": {
                            "market_order": "Submitted",
                            "stop_order": "PreSubmitted",
                            "limit_order": "PreSubmitted",
                        },
                    },
                }
            ]
        }
        stale_close = {
            "positions": [
                {
                    "symbol": "TREX",
                    "market_order_id": 725,
                    "opened_at": "2026-08-05T11:30:49",
                    "closed_at": "2026-08-05T16:30:58+00:00",
                    "exit_reason": "BROKER_POSITION_CLOSED",
                }
            ]
        }

        with patch(
            "src.journal.order_journal._read_json",
            side_effect=self._read_json(order_requests, stale_close),
        ):
            rows = order_journal._rows_from_order_requests({})

        self.assertEqual(rows[0]["status"], "UNFILLED")
        self.assertIsNone(rows[0]["closed_at"])
        self.assertIsNone(rows[0]["actual_entry"])

    def test_real_order_without_close_event_does_not_infer_exit_from_bars(self) -> None:
        order_requests = {
            "requests": [
                {
                    "id": "entry1",
                    "action": "place",
                    "status": "done",
                    "created_at": "2026-07-09T15:16:26",
                    "updated_at": "2026-07-09T15:16:32",
                    "source": "PROJECTED",
                    "symbol": "GCTS",
                    "signal": "BUY",
                    "entry": 2.57,
                    "stop": 2.36,
                    "target": 3.1475,
                    "quantity": 71,
                    "result": {"market_order_id": 458, "quantity": 71, "price": 2.57, "dry_run": False},
                }
            ]
        }

        with (
            patch("src.journal.order_journal._read_json", side_effect=self._read_json(order_requests, {"positions": []})),
            patch("src.journal.order_journal._infer_bracket_exit_from_bars") as infer_exit,
        ):
            rows = order_journal._rows_from_order_requests({})

        infer_exit.assert_not_called()
        self.assertEqual(rows[0]["status"], "CLOSED_UNKNOWN")
        self.assertIsNone(rows[0]["closed_at"])
        self.assertEqual(rows[0]["exit_reason"], "UNKNOWN_OR_BROKER")

    def test_tradingview_round_trip_is_one_canonical_filled_row(self) -> None:
        order_requests = {
            "requests": [
                {
                    "id": "entry1",
                    "action": "place",
                    "status": "done",
                    "created_at": "2026-07-28T11:30:13",
                    "updated_at": "2026-07-28T11:30:18",
                    "source": "tradingview",
                    "symbol": "AMZN",
                    "signal": "BUY",
                    "market_only": True,
                    "quantity": 2,
                    "result": {
                        "status": "Filled",
                        "filled": 2,
                        "avg_fill_price": 230.26,
                    },
                },
                {
                    "id": "close1",
                    "action": "close",
                    "status": "done",
                    "created_at": "2026-07-30T10:30:13",
                    "updated_at": "2026-07-30T10:30:20",
                    "source": "dashboard",
                    "symbol": "AMZN",
                    "result": {
                        "status": "Filled",
                        "filled": 2,
                        "avg_fill_price": 237.28,
                    },
                },
            ]
        }
        stock_state = {
            "executions": [
                {
                    "id": "tv-entry",
                    "created_at": "2026-07-28T15:30:13+00:00",
                    "action": "BUY",
                    "symbol": "AMZN",
                    "quantity": 2,
                    "tv_position_size": 2,
                    "status": "queued",
                    "request_id": "entry1",
                },
                {
                    "id": "tv-close",
                    "created_at": "2026-07-30T14:30:13+00:00",
                    "action": "SELL",
                    "symbol": "AMZN",
                    "tv_position_size": 0,
                    "status": "queued",
                    "request_id": "close1",
                },
            ]
        }

        def fake_read(path, default):
            path_text = str(path)
            if path_text.endswith("order_requests.json"):
                return order_requests
            if path_text.endswith("tradingview_stock_state.json"):
                return stock_state
            if path_text.endswith("tradingview_forex_state.json"):
                return {"executions": []}
            if path_text.endswith("closed_positions.json"):
                return {"positions": []}
            return default

        with patch("src.journal.order_journal._read_json", side_effect=fake_read):
            payload = order_journal.load_order_journal()

        rows = [row for row in payload["rows"] if row["symbol"] == "AMZN"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "stock:req:entry1")
        self.assertEqual(rows[0]["status"], "CLOSED")
        self.assertEqual(rows[0]["actual_entry"], 230.26)
        self.assertEqual(rows[0]["actual_exit"], 237.28)
        self.assertEqual(rows[0]["quantity"], 2.0)
        self.assertAlmostEqual(rows[0]["pnl"], 14.04)

    def test_unfilled_tradingview_market_request_is_not_a_trade(self) -> None:
        order_requests = {
            "requests": [
                {
                    "id": "entry-unfilled",
                    "action": "place",
                    "status": "done",
                    "created_at": "2026-07-24T11:30:19",
                    "updated_at": "2026-07-25T11:56:49",
                    "source": "tradingview",
                    "symbol": "AMZN",
                    "signal": "BUY",
                    "market_only": True,
                    "quantity": 2,
                    "result": {
                        "status": "PreSubmitted",
                        "filled": 0,
                        "remaining": 2,
                        "avg_fill_price": 0,
                    },
                }
            ]
        }

        with (
            patch("src.journal.order_journal._read_json", side_effect=self._read_json(order_requests, {"positions": []})),
            patch("src.journal.order_journal._infer_bracket_exit_from_bars") as infer_exit,
        ):
            rows = order_journal._rows_from_order_requests({})

        infer_exit.assert_not_called()
        self.assertEqual(rows, [])

    def test_open_bracket_position_matches_by_child_order_id(self) -> None:
        order_requests = {
            "requests": [
                {
                    "id": "trex-entry",
                    "action": "place",
                    "status": "done",
                    "created_at": "2026-07-27T10:01:37",
                    "updated_at": "2026-07-27T10:01:41",
                    "source": "MARKET_SCREENER_MANUAL",
                    "symbol": "TREX",
                    "signal": "BUY",
                    "entry": 44.08,
                    "stop": 41.23,
                    "target": 49.79,
                    "quantity": 8,
                    "result": {
                        "status": "executed",
                        "entry": 44.08,
                        "quantity": 8,
                        "market_order_id": 589,
                        "stop_order_id": 590,
                        "limit_order_id": 591,
                    },
                }
            ]
        }
        open_positions = {
            "TREX": {
                "symbol": "TREX",
                "quantity": 8,
                "avg_cost": 44.205,
                "direction": "long",
                "stop_order_id": 590,
                "current_stop_loss": 41.23,
                "limit_order_id": 591,
                "current_target": 49.79,
            }
        }

        with patch(
            "src.journal.order_journal._read_json",
            side_effect=self._read_json(order_requests, {"positions": []}),
        ):
            rows = order_journal._rows_from_order_requests(open_positions)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "OPEN")
        self.assertEqual(rows[0]["quantity"], 8.0)
        self.assertEqual(rows[0]["actual_entry"], 44.205)

    def test_tradingview_position_matches_by_open_timestamp_without_order_id(self) -> None:
        order_requests = {
            "requests": [
                {
                    "id": "ko-entry",
                    "action": "place",
                    "status": "done",
                    "created_at": "2026-07-28T09:30:19",
                    "updated_at": "2026-07-28T09:30:24",
                    "source": "tradingview",
                    "symbol": "KO",
                    "signal": "BUY",
                    "market_only": True,
                    "quantity": 6,
                    "result": {"status": "Filled", "filled": 6, "avg_fill_price": 87.26},
                }
            ]
        }
        open_positions = {
            "KO": {
                "symbol": "KO",
                "quantity": 6,
                "opened_at": "2026-07-28T09:30:24",
                "avg_cost": 87.42666665,
                "direction": "long",
            }
        }

        with patch(
            "src.journal.order_journal._read_json",
            side_effect=self._read_json(order_requests, {"positions": []}),
        ):
            rows = order_journal._rows_from_order_requests(open_positions)

        self.assertEqual(rows[0]["status"], "OPEN")
        self.assertEqual(rows[0]["actual_entry"], 87.42666665)

    def test_unfilled_manual_screener_request_is_unfilled_not_open(self) -> None:
        order_requests = {
            "requests": [
                {
                    "id": "mrsh-unfilled",
                    "action": "place",
                    "status": "done",
                    "created_at": "2026-07-29T10:45:08",
                    "updated_at": "2026-07-29T10:45:10",
                    "source": "MARKET_SCREENER_MANUAL",
                    "symbol": "MRSH",
                    "signal": "BUY",
                    "entry": 191.63,
                    "quantity": 2,
                    "result": {
                        "status": "executed",
                        "entry": 191.63,
                        "quantity": 2,
                        "market_order_id": 639,
                        "stop_order_id": 640,
                        "limit_order_id": 641,
                        "broker_statuses": {
                            "market_order": "Submitted",
                            "stop_order": "PreSubmitted",
                            "limit_order": "PreSubmitted",
                        },
                    },
                }
            ]
        }

        with patch(
            "src.journal.order_journal._read_json",
            side_effect=self._read_json(order_requests, {"positions": []}),
        ):
            rows = order_journal._rows_from_order_requests({})

        self.assertEqual(rows[0]["status"], "UNFILLED")
        self.assertEqual(rows[0]["exit_reason"], "")
        self.assertIsNone(rows[0]["actual_entry"])
