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

