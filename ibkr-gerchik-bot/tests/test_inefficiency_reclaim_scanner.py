from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from src.scanners.inefficiency_reclaim import (
    _diagnose_no_candidate,
    frame_to_bars,
    resample_rth,
    run_inefficiency_reclaim_screener,
)
from src.storage.inefficiency_reclaim_store import InefficiencyReclaimStore
from src.strategy.inefficiency_reclaim import Bar, IRSConfig


ET = ZoneInfo("America/New_York")


class SavedBarScannerTests(unittest.TestCase):
    def test_historical_diagnostics_ignore_future_incomplete_bars(self) -> None:
        anchor = datetime(2026, 7, 23, 16, 0, tzinfo=ET)
        bars = [
            Bar(
                symbol="TEST",
                timeframe="1 hour",
                timestamp=anchor.replace(hour=10) - pd.Timedelta(days=130 - index),
                open=100,
                high=101,
                low=99,
                close=100,
                volume=100_000,
            )
            for index in range(130)
        ]
        bars.extend(
            Bar(
                symbol="TEST",
                timeframe="1 hour",
                timestamp=anchor + pd.Timedelta(hours=index + 1),
                open=100,
                high=101,
                low=99,
                close=100,
                volume=100_000,
                is_complete=False,
            )
            for index in range(50)
        )

        reasons = _diagnose_no_candidate(bars, (), IRSConfig())

        self.assertNotIn("INCOMPLETE_SETUP_BAR", reasons)
        self.assertIn("DIRECTION_MISSING", reasons)

    def test_incomplete_15m_bar_is_marked_and_ignored_by_strategy(self) -> None:
        frame = pd.DataFrame(
            [
                {"date": "2026-07-01 09:30:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
                {"date": "2026-07-01 09:45:00", "open": 100.5, "high": 101, "low": 100, "close": 100.8, "volume": 1000},
                {"date": "2026-07-01 10:00:00", "open": 100.8, "high": 101.2, "low": 100.6, "close": 101, "volume": 500},
            ]
        )
        bars = frame_to_bars(
            "TEST",
            "15 mins",
            frame,
            as_of=datetime(2026, 7, 1, 10, 10, tzinfo=ET),
        )
        self.assertTrue(bars[0].is_complete)
        self.assertTrue(bars[1].is_complete)
        self.assertFalse(bars[2].is_complete)

    def test_anchor_date_marks_future_daily_bars_incomplete(self) -> None:
        frame = pd.DataFrame(
            [
                {"date": "2026-07-01", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
                {"date": "2026-07-02", "open": 101, "high": 102, "low": 100, "close": 101, "volume": 1000},
            ]
        )
        bars = frame_to_bars(
            "TEST",
            "1 day",
            frame,
            as_of=datetime(2026, 7, 1, 16, 0, tzinfo=ET),
        )
        self.assertTrue(bars[0].is_complete)
        self.assertFalse(bars[1].is_complete)

    def test_resample_excludes_extended_hours(self) -> None:
        frame = pd.DataFrame(
            [
                {"date": "2026-07-01 08:00:00", "open": 90, "high": 91, "low": 89, "close": 90, "volume": 100},
                {"date": "2026-07-01 09:30:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 100},
                {"date": "2026-07-01 09:35:00", "open": 100.5, "high": 102, "low": 100, "close": 101, "volume": 100},
            ]
        )
        result = resample_rth(frame, "15min")
        self.assertEqual(len(result), 1)
        self.assertEqual(float(result.iloc[0]["open"]), 100.0)
        self.assertEqual(float(result.iloc[0]["high"]), 102.0)

    def test_missing_data_fails_closed_and_scan_is_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = InefficiencyReclaimStore(Path(directory) / "irs.db")

            def loader(_symbol: str, _timeframe: str) -> pd.DataFrame:
                return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

            result = run_inefficiency_reclaim_screener(
                symbols=["EMPTY"],
                bars_loader=loader,
                watchlist={},
                anchor_date="2026-07-01",
                config=IRSConfig(),
                store=store,
            )

            self.assertEqual(result["signals_found"], 0)
            self.assertEqual(result["rejected"][0]["primary_reason"], "INSUFFICIENT_DAILY_HISTORY")
            self.assertFalse(result["data_readiness"]["EMPTY"]["paper_ready"])
            self.assertEqual(
                result["observability"]["irs_symbols_scanned_total"],
                1,
            )
            self.assertGreaterEqual(
                result["observability"]["irs_scan_duration_seconds"],
                0,
            )
            self.assertEqual(store.table_count("scanner_runs"), 1)

    def test_scan_uses_explicit_minimum_daily_history_rows(self) -> None:
        daily = pd.DataFrame(
            [
                {
                    "date": timestamp.strftime("%Y-%m-%d"),
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                    "volume": 1_000_000,
                }
                for timestamp in pd.bdate_range("2026-05-01", periods=25)
            ]
        )

        def loader(_symbol: str, timeframe: str) -> pd.DataFrame:
            if timeframe == "daily":
                return daily
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

        result = run_inefficiency_reclaim_screener(
            symbols=["TEST"],
            bars_loader=loader,
            watchlist={},
            anchor_date="2026-07-01",
            min_daily_history_rows=20,
            config=IRSConfig(),
            persist=False,
        )

        self.assertEqual(result["min_daily_history_rows"], 20)
        self.assertNotIn(
            "INSUFFICIENT_DAILY_HISTORY",
            result["rejected"][0]["rejection_reasons"],
        )

    def test_live_scan_fetches_delayed_quote_only_after_technical_candidate(self) -> None:
        empty_frame = pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "volume"]
        )
        quote_requests: list[str] = []

        def quote_loader(symbol: str) -> dict[str, object]:
            quote_requests.append(symbol)
            return {
                "bid": 49.90,
                "ask": 50.10,
                "last": 50.00,
                "market_data_type": "delayed",
            }

        with patch(
            "src.scanners.inefficiency_reclaim.evaluate_strategy",
            side_effect=[(object(),), ()],
        ) as evaluate:
            result = run_inefficiency_reclaim_screener(
                symbols=["TEST"],
                bars_loader=lambda _symbol, _timeframe: empty_frame,
                watchlist={},
                as_of=datetime(2026, 7, 1, 14, tzinfo=ET),
                quote_loader=quote_loader,
                config=IRSConfig(),
                persist=False,
            )

        self.assertEqual(quote_requests, ["TEST"])
        self.assertEqual(evaluate.call_count, 2)
        self.assertEqual(result["observability"]["irs_quote_requests_total"], 1)
        self.assertEqual(result["observability"]["irs_quotes_available_total"], 1)
        self.assertEqual(result["observability"]["irs_delayed_quotes_total"], 1)


if __name__ == "__main__":
    unittest.main()
