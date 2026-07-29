from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from src.scanners.inefficiency_reclaim import (
    HISTORICAL_ANALYSIS_SOFT_WARNING,
    _diagnose_no_candidate,
    _historical_analysis_candidate,
    frame_to_bars,
    resample_rth,
    run_inefficiency_reclaim_screener,
)
from src.storage.inefficiency_reclaim_store import InefficiencyReclaimStore
from src.strategy.inefficiency_reclaim import (
    Bar,
    ConfirmationEvent,
    ConfirmationType,
    Direction,
    IRSConfig,
    InefficiencyZone,
    ScoreBreakdown,
    SetupState,
    StrategyCandidate,
    StrategyProfile,
    ZoneType,
)


ET = ZoneInfo("America/New_York")
D = Decimal


def _runtime_rejected_candidate(now: datetime) -> StrategyCandidate:
    zone = InefficiencyZone(
        zone_id="zone-test",
        symbol="TEST",
        direction=Direction.LONG,
        zone_type=ZoneType.STRICT_THREE_BAR_GAP,
        source_timeframe="1 hour",
        created_at=now - pd.Timedelta(hours=1),
        displacement_bar_time=now - pd.Timedelta(hours=1),
        zone_low=D("100"),
        zone_high=D("101"),
        zone_mid=D("100.5"),
        zone_width=D("1"),
        zone_width_atr=D("0.20"),
        displacement_atr_multiple=D("1.50"),
        body_ratio=D("0.80"),
        close_location=D("0.85"),
        relative_volume=D("1.60"),
        source_bar_ids=("bar-test",),
        structure_reference_id="level-test",
        expires_at=now + pd.Timedelta(hours=1),
    )
    confirmation = ConfirmationEvent(
        confirmation_type=ConfirmationType.SWEEP_AND_RECLAIM,
        timestamp=now,
        bar_id="confirm-test",
        high=D("102"),
        low=D("100"),
        boundary=D("100.5"),
        score=D("0.90"),
        metrics={},
    )
    return StrategyCandidate(
        signal_id="signal-test",
        symbol="TEST",
        strategy="INEFFICIENCY_RECLAIM",
        profile=StrategyProfile.INTRADAY,
        direction=Direction.LONG,
        state=SetupState.REJECTED_BY_RISK,
        score=D("90"),
        score_breakdown=ScoreBreakdown(
            structure=D("15"),
            displacement=D("15"),
            zone=D("10"),
            volume_order_flow=D("15"),
            retrace=D("10"),
            confirmation=D("15"),
            regime=D("10"),
            target_space=D("0"),
        ),
        zone=zone,
        displacement_metrics={},
        retrace_metrics={"first_touch_time": now},
        confirmation=confirmation,
        order_plan=None,
        expires_at=now + pd.Timedelta(minutes=30),
        hard_rejections=("MISSING_QUOTE", "BUYING_POWER", "SESSION_CUTOFF"),
        soft_warnings=(),
        diagnostics={},
        explanation="TEST | IRS_LONG | Score 90 PAPER. No order: MISSING_QUOTE, BUYING_POWER, SESSION_CUTOFF.",
    )


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

    def test_persist_false_does_not_audit_even_when_store_is_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = InefficiencyReclaimStore(Path(directory) / "irs.db")

            def loader(_symbol: str, _timeframe: str) -> pd.DataFrame:
                return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

            run_inefficiency_reclaim_screener(
                symbols=["EMPTY"],
                bars_loader=loader,
                watchlist={},
                anchor_date="2026-07-01",
                config=IRSConfig(),
                store=store,
                persist=False,
            )

            self.assertEqual(store.table_count("scanner_runs"), 0)
            self.assertEqual(store.table_count("strategy_setups"), 0)

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

    def test_anchor_scan_marks_runtime_risk_unevaluated_instead_of_missing_quote(self) -> None:
        now = datetime(2026, 7, 24, 16, 0, tzinfo=ET)
        candidate = _historical_analysis_candidate(_runtime_rejected_candidate(now))

        self.assertEqual(candidate.hard_rejections, ())
        self.assertEqual(candidate.state, SetupState.ENTRY_ARMED)
        self.assertIn(HISTORICAL_ANALYSIS_SOFT_WARNING, candidate.soft_warnings)
        self.assertEqual(
            candidate.diagnostics["runtime_risk_evaluation"],
            "not_evaluated_for_historical_anchor",
        )
        self.assertIn(
            "MISSING_QUOTE",
            candidate.diagnostics["suppressed_runtime_rejections"],
        )
        self.assertNotIn("MISSING_QUOTE", candidate.explanation)


if __name__ == "__main__":
    unittest.main()
