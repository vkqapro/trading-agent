"""Tests for structured workflow log recovery."""

import unittest
import uuid
from pathlib import Path

from src.workflow_log import append_workflow_snapshot, read_latest_workflow_snapshot


class WorkflowLogTests(unittest.TestCase):
    def test_round_trip_latest_snapshot(self) -> None:
        path = Path(__file__).resolve().parent / f"_tmp_workflow_{uuid.uuid4().hex}.md"
        try:
            path.write_text("# Research Log\n", encoding="utf-8")
            append_workflow_snapshot(path, "Premarket", {"watchlist": {"AAPL": {"levels": {}}}})
            append_workflow_snapshot(path, "Open", {"executed": [{"symbol": "AAPL"}]})
            append_workflow_snapshot(path, "Premarket", {"watchlist": {"MSFT": {"levels": {}}}})

            latest = read_latest_workflow_snapshot(path, "Premarket")

            self.assertIsNotNone(latest)
            self.assertEqual(list(latest["watchlist"].keys()), ["MSFT"])
        finally:
            if path.exists():
                path.unlink()


if __name__ == "__main__":
    unittest.main()
