from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

from src.config import SETTINGS
from src.jobs.intraday import run_intraday
from src.reports.intraday_report import nearest_level_details, write_intraday_scan_report


TZ = ZoneInfo("America/New_York")


class IntradayReportTests(unittest.TestCase):
    def test_nearest_level_details_prefers_quote_closest_level(self) -> None:
        plan = {
            "levels": [
                {"center": 100.0, "type": "historical", "strength_score": 8.0},
                {"center": 102.0, "type": "gap", "strength_score": 12.0},
                {"center": 98.5, "type": "mirror", "strength_score": 6.0},
            ]
        }

        level, level_type = nearest_level_details(plan, {"last": 101.7})

        self.assertEqual(level, 102.0)
        self.assertEqual(level_type, "gap")

    def test_nearest_level_details_prefers_intraday_reference_over_stale_quote_close(self) -> None:
        plan = {
            "levels": [
                {"price": 5.98, "center": 5.98, "type": "historical", "strength_score": 16.0},
                {"price": 4.88, "center": 4.88, "type": "mirror", "strength_score": 12.0},
                {"price": 4.61, "center": 4.61, "type": "historical", "strength_score": 20.0},
                {"price": 4.48, "center": 4.48, "type": "consolidation", "strength_score": 22.0},
                {"price": 6.27, "center": 6.27, "type": "historical", "strength_score": 6.0},
            ]
        }

        level, level_type = nearest_level_details(
            plan,
            {"bid": 0.0, "ask": 0.0, "last": 0.0, "close": 5.98, "quote_status": "partial"},
            reference_price=4.58,
        )

        self.assertEqual(level, 4.61)
        self.assertEqual(level_type, "historical")

    def test_nearest_level_details_uses_zone_proximity_but_returns_level_price(self) -> None:
        plan = {
            "levels": [
                {
                    "price": 71.05,
                    "center": 70.75,
                    "zone_low": 69.53,
                    "zone_high": 71.77,
                    "type": "abnormal_candle",
                    "strength_score": 140.0,
                },
                {
                    "price": 74.44,
                    "center": 74.36,
                    "zone_low": 73.19,
                    "zone_high": 75.56,
                    "type": "gap",
                    "strength_score": 66.0,
                },
            ]
        }

        level, level_type = nearest_level_details(plan, {"last": 72.44}, reference_price=72.44)

        self.assertEqual(level, 71.05)
        self.assertEqual(level_type, "abnormal_candle")

    def test_write_intraday_scan_report_creates_workbook(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = write_intraday_scan_report(
                report_rows=[
                    {
                        "stock_symbol": "MSFT",
                        "nearest_level": 421.25,
                        "nearest_level_type": "historical",
                        "reason_not_entered": "no_signal",
                    }
                ],
                scan_time=datetime(2026, 5, 19, 13, 0, tzinfo=TZ),
                report_dir=Path(temp_dir),
            )

            workbook = load_workbook(report_path)
            sheet = workbook.active

        self.assertEqual(sheet.title, "Intraday Scan")
        self.assertEqual(sheet["A2"].value, "MSFT")
        self.assertEqual(sheet["B2"].value, 421.25)
        self.assertEqual(sheet["C2"].value, "historical")
        self.assertEqual(sheet["D2"].value, "no_signal")

    def test_run_intraday_uploads_scan_report_to_slack(self) -> None:
        class BrokerStub:
            def get_positions(self):
                return []

            def get_open_orders(self):
                return []

        class MarketDataStub:
            def __init__(self) -> None:
                self.calls = 0

            def market_is_open(self, _moment):
                self.calls += 1
                return self.calls <= 2

        class AlerterStub:
            def __init__(self) -> None:
                self.uploads = []

            def send_intraday_heartbeat(self, _payload):
                return True

            def upload_file(self, file_path: Path, title: str, initial_comment: str = ""):
                self.uploads.append((file_path, title, initial_comment))
                return True

        class NewsFilterStub:
            def is_macro_risk(self) -> bool:
                return False

        market_data_stub = MarketDataStub()
        alerter = AlerterStub()
        original_reports_dir = SETTINGS.paths.reports_dir

        with tempfile.TemporaryDirectory() as temp_dir:
            report_dir = Path(temp_dir)
            object.__setattr__(SETTINGS.paths, "reports_dir", report_dir)
            try:
                with (
                    patch("src.jobs.intraday.build_job_dependencies", return_value=(market_data_stub, object())),
                    patch("src.jobs.intraday.load_runtime_state", return_value={"watchlist": {"MSFT": {"levels": []}}, "tracked_positions": []}),
                    patch(
                        "src.jobs.intraday.run_entry_scan",
                        return_value={
                            "executed": [],
                            "skipped": [{"symbol": "MSFT", "reason": "no_signal"}],
                            "manual_candidates": [],
                            "report_rows": [
                                {
                                    "stock_symbol": "MSFT",
                                    "nearest_level": 421.25,
                                    "nearest_level_type": "historical",
                                    "reason_not_entered": "no_signal",
                                }
                            ],
                            "signals_detected": 0,
                            "symbols_scanned": 1,
                            "macro_risk": {"risk_level": "LOW"},
                        },
                    ),
                    patch(
                        "src.jobs.intraday.manage_positions",
                        return_value={"actions": [], "macro_risk": False, "kill_switch": False, "reasons": []},
                    ),
                    patch("src.jobs.intraday.persist_tracked_positions", return_value=None),
                    patch("src.jobs.intraday.append_workflow_snapshot", return_value=None),
                    patch("src.jobs.intraday.append_markdown_log", return_value=None),
                    patch("src.jobs.intraday.sleep_until", return_value=None),
                ):
                    run_intraday(
                        BrokerStub(),
                        alerter,
                        NewsFilterStub(),
                        [],
                        account_equity=100_000.0,
                        dry_run=True,
                        now_provider=lambda: datetime(2026, 5, 19, 13, 0, tzinfo=TZ),
                        sleep_provider=lambda _seconds: None,
                    )

                self.assertEqual(len(alerter.uploads), 1)
                uploaded_path, title, initial_comment = alerter.uploads[0]
                self.assertTrue(uploaded_path.exists())
                self.assertIn("Intraday Scan 2026-05-19 13:00", title)
                self.assertIn("Scanned: 1", initial_comment)
            finally:
                object.__setattr__(SETTINGS.paths, "reports_dir", original_reports_dir)


if __name__ == "__main__":
    unittest.main()
