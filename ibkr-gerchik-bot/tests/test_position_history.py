from __future__ import annotations

from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from src.journal import position_history


class PositionHistoryTests(TestCase):
    def test_archive_retains_last_known_cost_after_position_disappears(self) -> None:
        with TemporaryDirectory() as directory:
            path = position_history.Path(directory) / "position_history.json"
            with patch.object(position_history, "POSITION_HISTORY_PATH", path):
                position_history.archive_positions(
                    [
                        {
                            "symbol": "LIN",
                            "quantity": 2,
                            "avg_cost": 480.62,
                            "stop_order_id": 820,
                        }
                    ]
                )
                position_history.archive_positions([])
                archived = position_history.load_position_history()

        self.assertEqual(archived["LIN"]["quantity"], 2)
        self.assertEqual(archived["LIN"]["avg_cost"], 480.62)
        self.assertEqual(archived["LIN"]["stop_order_id"], 820)

    def test_archive_updates_a_position_without_erasing_other_symbols(self) -> None:
        with TemporaryDirectory() as directory:
            path = position_history.Path(directory) / "position_history.json"
            with patch.object(position_history, "POSITION_HISTORY_PATH", path):
                position_history.archive_positions(
                    [
                        {"symbol": "LIN", "quantity": 2, "avg_cost": 480.62},
                        {"symbol": "MRSH", "quantity": 5, "avg_cost": 191.15},
                    ]
                )
                position_history.archive_positions(
                    [{"symbol": "LIN", "quantity": 3, "avg_cost": 482.0}]
                )
                archived = position_history.load_position_history()

        self.assertEqual(archived["LIN"]["quantity"], 3)
        self.assertEqual(archived["LIN"]["avg_cost"], 482.0)
        self.assertEqual(archived["MRSH"]["avg_cost"], 191.15)

    def test_archive_preserves_opening_metadata_across_later_snapshots(self) -> None:
        with TemporaryDirectory() as directory:
            path = position_history.Path(directory) / "position_history.json"
            with (
                patch.object(position_history, "POSITION_HISTORY_PATH", path),
                patch(
                    "src.journal.position_history._now",
                    side_effect=[
                        "2026-08-24T20:10:03+00:00",
                        "2026-08-27T13:30:00+00:00",
                    ],
                ),
            ):
                position_history.archive_positions(
                    [{"symbol": "LIN", "quantity": 2, "avg_cost": 480.62}]
                )
                position_history.archive_positions(
                    [{"symbol": "LIN", "quantity": 2, "avg_cost": 480.62}]
                )
                archived = position_history.load_position_history()

        self.assertEqual(archived["LIN"]["first_seen_at"], "2026-08-24T20:10:03+00:00")
        self.assertEqual(archived["LIN"]["last_seen_at"], "2026-08-27T13:30:00+00:00")

    def test_archive_preserves_an_authoritative_opened_at(self) -> None:
        with TemporaryDirectory() as directory:
            path = position_history.Path(directory) / "position_history.json"
            with patch.object(position_history, "POSITION_HISTORY_PATH", path):
                position_history.archive_positions(
                    [
                        {
                            "symbol": "MRSH",
                            "quantity": 5,
                            "opened_at": "2026-08-24",
                        }
                    ]
                )
                position_history.archive_positions([{"symbol": "MRSH", "quantity": 5}])
                archived = position_history.load_position_history()

        self.assertEqual(archived["MRSH"]["opened_at"], "2026-08-24")
