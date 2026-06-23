from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from dashboard import data_access as da


class DashboardDataAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        da.clear_caches()

    def tearDown(self) -> None:
        da.clear_caches()

    def test_json_read_recovers_after_malformed_content_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            path.write_text('{"watchlist":', encoding="utf-8")

            self.assertEqual(da._read_json(path), {})

            path.write_text(
                json.dumps({"watchlist": {"AAPL": {"levels": []}}}),
                encoding="utf-8",
            )
            self.assertIn("AAPL", da._read_json(path)["watchlist"])

    def test_workflow_parser_keeps_latest_valid_snapshot_when_tail_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "research.md"
            path.write_text(
                "\n".join(
                    [
                        "# Research",
                        "## Workflow Premarket (2026-06-19)",
                        "```json",
                        '{"stage":"Premarket","payload":{"watchlist":{"AAPL":{"levels":[]}}}}',
                        "```",
                        "## Workflow Intraday (2026-06-19)",
                        "```json",
                        '{"stage":"Intraday","payload":{"executed":[{"symbol":"AAPL"}]}}',
                        "```",
                        "## Workflow Premarket (partial)",
                        "```json",
                        '{"stage":"Premarket","payload":',
                    ]
                ),
                encoding="utf-8",
            )
            with patch.object(da, "RESEARCH_LOG_PATH", path):
                snapshots = da.load_workflow_snapshots()

            self.assertEqual(list(snapshots["Premarket"]["watchlist"]), ["AAPL"])
            self.assertEqual(snapshots["Intraday"]["executed"][0]["symbol"], "AAPL")

    def test_decisions_to_frame_normalizes_nested_values(self) -> None:
        frame = da.decisions_to_frame(
            [
                {
                    "timestamp": "2026-06-19T10:00:00",
                    "signal": "NONE",
                    "reason": ["no return", "weak close"],
                    "context": {"zone": [100, 101], "trend": "RANGE"},
                }
            ]
        )

        self.assertEqual(frame.loc[0, "reason"], "no return; weak close")
        self.assertEqual(frame.loc[0, "zone"], "100 - 101")
        self.assertEqual(frame.loc[0, "trend"], "RANGE")

    def test_bar_loader_rejects_invalid_schema_and_deduplicates_valid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "AAPL__daily.csv"
            path.write_text("date,close\n2026-01-01,10\n", encoding="utf-8")
            with patch.object(da, "bar_path", return_value=path):
                self.assertTrue(da.get_bars("AAPL", "daily").empty)

            path.write_text(
                "\n".join(
                    [
                        "date,open,high,low,close,volume",
                        "2026-01-01,10,12,9,11,100",
                        "2026-01-01,10,13,9,12,200",
                    ]
                ),
                encoding="utf-8",
            )
            da.clear_caches()
            with patch.object(da, "bar_path", return_value=path):
                frame = da.get_bars("AAPL", "daily")

            self.assertEqual(len(frame), 1)
            self.assertEqual(frame.loc[0, "close"], 12)

    def test_report_index_groups_filename_by_kind_and_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            report = directory / "intraday_scan_20260619_101500.xlsx"
            pd.DataFrame([{"Ticker": "AAPL"}]).to_excel(report, index=False)
            (directory / "~$intraday_scan_20260619_101500.xlsx").write_bytes(b"lock")

            with patch.object(da, "REPORTS_DIR", directory):
                reports = da.list_report_info()

            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0].kind, "Intraday Scan")
            self.assertEqual(reports[0].report_date, "2026-06-19")

    def test_source_health_reports_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing"
            with (
                patch.object(da, "STATE_PATH", missing / "state.json"),
                patch.object(da, "DAILY_DECISIONS_PATH", missing / "decisions.json"),
                patch.object(da, "RESEARCH_LOG_PATH", missing / "research.md"),
                patch.object(da, "TRADE_LOG_PATH", missing / "trade.md"),
            ):
                health = da.source_health()

            self.assertEqual({item.status for item in health}, {"missing"})


if __name__ == "__main__":
    unittest.main()
