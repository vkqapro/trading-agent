from __future__ import annotations

from unittest import TestCase

import pandas as pd

from dashboard_react.market_screener import (
    ScreenerParams,
    _add_metrics,
    _detect_signals,
    _passes_filters,
    _pivot_levels,
    run_market_screener,
)


def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = _add_metrics(pd.DataFrame(rows))
    frame["atr_clean_14"] = 1.0
    frame["median_volume_20"] = 1_000_000
    frame["avg_volume_20"] = 1_000_000
    frame["median_dollar_volume_20"] = 50_000_000
    return frame


class MarketScreenerTests(TestCase):
    def test_lp1_long_detects_support_reclaim(self) -> None:
        rows = []
        for index in range(30):
            rows.append(
                {
                    "date": f"2026-06-{index + 1:02d}",
                    "open": 100.4,
                    "high": 101.0,
                    "low": 99.8,
                    "close": 100.5,
                    "volume": 1_200_000,
                }
            )
        rows[-6].update({"open": 102.1, "high": 102.4, "low": 101.8, "close": 102.0})
        rows[-5].update({"open": 101.9, "high": 102.0, "low": 101.4, "close": 101.6})
        rows[-4].update({"open": 101.5, "high": 101.7, "low": 101.0, "close": 101.2})
        rows[-3].update({"open": 101.1, "high": 101.3, "low": 100.5, "close": 100.7})
        rows[-2].update({"open": 100.05, "high": 101.0, "low": 99.2, "close": 100.8, "volume": 1_800_000})
        rows[-1].update({"open": 100.4, "high": 101.2, "low": 100.2, "close": 100.9, "volume": 1_300_000})
        frame = _add_metrics(pd.DataFrame(rows))
        frame["atr_clean_14"] = frame["atr_clean_14"].fillna(1.0)
        signals = _detect_signals(
            "TEST",
            frame,
            [{"price": 100.0, "kind": "support", "touches": 3, "strength": 0.8, "tolerance": 0.2}],
            ScreenerParams(strategies=("LP1",)),
        )

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["strategy"], "LP1")
        self.assertEqual(signals[0]["side"], "LONG")
        self.assertEqual(signals[0]["status"], "READY")
        self.assertEqual(signals[0]["signal_bar_date"], "2026-06-29")
        self.assertEqual(signals[0]["entry_day"], "2026-06-30")
        self.assertEqual(signals[0]["stop_variant"], "behind_signal_bar")
        self.assertEqual(signals[0]["pattern_bar_dates"], ["2026-06-29"])

    def test_lp2_requires_false_side_close(self) -> None:
        rows = [
            {"date": "2026-06-29", "open": 102.2, "high": 102.4, "low": 101.8, "close": 102.0, "volume": 1_100_000},
            {"date": "2026-06-30", "open": 101.8, "high": 102.0, "low": 101.3, "close": 101.5, "volume": 1_100_000},
            {"date": "2026-07-01", "open": 101.3, "high": 101.5, "low": 100.6, "close": 100.8, "volume": 1_100_000},
            {"date": "2026-07-02", "open": 100.6, "high": 101.0, "low": 99.4, "close": 100.2, "volume": 1_100_000},
            {"date": "2026-07-03", "open": 100.1, "high": 101.4, "low": 99.4, "close": 100.8, "volume": 1_100_000},
            {"date": "2026-07-06", "open": 100.4, "high": 101.0, "low": 100.1, "close": 100.7, "volume": 1_100_000},
        ]
        frame = _frame(rows)

        signals = _detect_signals(
            "TEST",
            frame,
            [{"price": 100.0, "kind": "support", "touches": 3, "strength": 0.8, "tolerance": 0.2}],
            ScreenerParams(strategies=("LP2",)),
        )

        self.assertEqual(signals, [])

    def test_lp2_uses_two_pattern_bars_and_next_entry_day(self) -> None:
        rows = [
            {"date": "2026-06-29", "open": 102.2, "high": 102.4, "low": 101.8, "close": 102.0, "volume": 1_100_000},
            {"date": "2026-06-30", "open": 101.8, "high": 102.0, "low": 101.3, "close": 101.5, "volume": 1_100_000},
            {"date": "2026-07-01", "open": 101.3, "high": 101.5, "low": 100.6, "close": 100.8, "volume": 1_100_000},
            {"date": "2026-07-02", "open": 100.5, "high": 100.7, "low": 99.2, "close": 99.7, "volume": 1_100_000},
            {"date": "2026-07-03", "open": 99.8, "high": 101.2, "low": 99.5, "close": 100.8, "volume": 1_300_000},
            {"date": "2026-07-06", "open": 100.4, "high": 101.0, "low": 100.1, "close": 100.7, "volume": 1_100_000},
        ]
        signals = _detect_signals(
            "TEST",
            _frame(rows),
            [{"price": 100.0, "kind": "resistance", "touches": 3, "strength": 0.8, "tolerance": 0.2}],
            ScreenerParams(strategies=("LP2",)),
        )

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["level_type"], "support")
        self.assertEqual(signals[0]["level_origin_type"], "resistance")
        self.assertEqual(signals[0]["pattern_bar_dates"], ["2026-07-02", "2026-07-03"])
        self.assertEqual(signals[0]["entry_day"], "2026-07-06")
        self.assertEqual(signals[0]["stop_variant"], "behind_two_bar_structure")

    def test_all_four_strategies_detect_the_short_side(self) -> None:
        cases = {
            "LP1": [
                {"date": "2026-06-29", "open": 98.0, "high": 98.3, "low": 97.8, "close": 98.0, "volume": 1_000_000},
                {"date": "2026-06-30", "open": 98.3, "high": 98.6, "low": 98.1, "close": 98.4, "volume": 1_000_000},
                {"date": "2026-07-01", "open": 98.7, "high": 99.0, "low": 98.5, "close": 98.8, "volume": 1_000_000},
                {"date": "2026-07-02", "open": 99.3, "high": 99.6, "low": 99.1, "close": 99.4, "volume": 1_000_000},
                {"date": "2026-07-03", "open": 100.0, "high": 100.8, "low": 99.0, "close": 99.2, "volume": 1_600_000},
                {"date": "2026-07-06", "open": 99.5, "high": 99.8, "low": 99.0, "close": 99.2, "volume": 1_100_000},
            ],
            "LP2": [
                {"date": "2026-06-29", "open": 98.0, "high": 98.3, "low": 97.8, "close": 98.0, "volume": 1_000_000},
                {"date": "2026-06-30", "open": 98.3, "high": 98.6, "low": 98.1, "close": 98.4, "volume": 1_000_000},
                {"date": "2026-07-01", "open": 98.7, "high": 99.0, "low": 98.5, "close": 98.8, "volume": 1_000_000},
                {"date": "2026-07-02", "open": 99.5, "high": 100.8, "low": 99.2, "close": 100.3, "volume": 1_100_000},
                {"date": "2026-07-03", "open": 100.2, "high": 100.5, "low": 99.0, "close": 99.2, "volume": 1_300_000},
                {"date": "2026-07-06", "open": 99.5, "high": 99.8, "low": 99.0, "close": 99.2, "volume": 1_100_000},
            ],
            "PRB1": [
                {"date": "2026-06-29", "open": 102.0, "high": 102.3, "low": 101.8, "close": 102.0, "volume": 1_000_000},
                {"date": "2026-06-30", "open": 101.7, "high": 101.9, "low": 101.4, "close": 101.6, "volume": 1_000_000},
                {"date": "2026-07-01", "open": 101.3, "high": 101.5, "low": 101.0, "close": 101.2, "volume": 1_000_000},
                {"date": "2026-07-02", "open": 100.8, "high": 101.0, "low": 100.4, "close": 100.6, "volume": 1_000_000},
                {"date": "2026-07-03", "open": 100.1, "high": 100.4, "low": 98.8, "close": 99.2, "volume": 1_600_000},
                {"date": "2026-07-06", "open": 99.8, "high": 100.0, "low": 99.0, "close": 99.3, "volume": 1_100_000},
            ],
            "PRB2": [
                {"date": "2026-06-29", "open": 102.0, "high": 102.3, "low": 101.8, "close": 102.0, "volume": 1_000_000},
                {"date": "2026-06-30", "open": 101.7, "high": 101.9, "low": 101.4, "close": 101.6, "volume": 1_000_000},
                {"date": "2026-07-01", "open": 101.0, "high": 101.2, "low": 100.6, "close": 100.8, "volume": 1_000_000},
                {"date": "2026-07-02", "open": 100.1, "high": 100.4, "low": 98.7, "close": 99.1, "volume": 1_600_000},
                {"date": "2026-07-03", "open": 99.6, "high": 100.1, "low": 99.2, "close": 99.7, "volume": 1_100_000},
                {"date": "2026-07-06", "open": 99.8, "high": 100.0, "low": 99.2, "close": 99.4, "volume": 1_100_000},
            ],
        }

        for strategy, rows in cases.items():
            with self.subTest(strategy=strategy):
                signals = _detect_signals(
                    "TEST",
                    _frame(rows),
                    [{"price": 100.0, "kind": "support", "touches": 3, "strength": 0.8, "tolerance": 0.2}],
                    ScreenerParams(strategies=(strategy,)),
                )
                self.assertEqual(len(signals), 1)
                self.assertEqual(signals[0]["side"], "SHORT")

    def test_prb1_uses_prior_breakout_bar_and_latest_entry_day(self) -> None:
        rows = [
            {"date": "2026-07-01", "open": 99.0, "high": 100.0, "low": 98.5, "close": 99.5, "volume": 1_000_000},
            {"date": "2026-07-02", "open": 99.6, "high": 100.2, "low": 99.0, "close": 99.8, "volume": 1_000_000},
            {"date": "2026-07-03", "open": 99.8, "high": 101.2, "low": 99.4, "close": 100.8, "volume": 1_600_000},
            {"date": "2026-07-06", "open": 100.2, "high": 101.0, "low": 100.0, "close": 100.7, "volume": 1_200_000},
        ]
        frame = _frame(rows)

        signals = _detect_signals(
            "TEST",
            frame,
            [{"price": 100.0, "kind": "resistance", "touches": 3, "strength": 0.8, "tolerance": 0.2}],
            ScreenerParams(strategies=("PRB1",)),
        )

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["strategy"], "PRB1")
        self.assertEqual(signals[0]["signal_bar_date"], "2026-07-03")
        self.assertEqual(signals[0]["entry_day"], "2026-07-06")
        self.assertIn("behind_signal_bar", {item["variant"] for item in signals[0]["stop_options"]})

    def test_prb2_requires_breakout_day_hold_day_and_entry_day(self) -> None:
        rows = [
            {"date": "2026-07-01", "open": 99.2, "high": 100.0, "low": 98.8, "close": 99.6, "volume": 1_000_000},
            {"date": "2026-07-02", "open": 99.7, "high": 100.1, "low": 99.1, "close": 99.8, "volume": 1_000_000},
            {"date": "2026-07-03", "open": 99.8, "high": 101.3, "low": 99.5, "close": 100.9, "volume": 1_600_000},
            {"date": "2026-07-06", "open": 100.4, "high": 101.2, "low": 100.1, "close": 100.7, "volume": 1_200_000},
            {"date": "2026-07-07", "open": 100.3, "high": 101.5, "low": 100.2, "close": 101.0, "volume": 1_200_000},
        ]
        frame = _frame(rows)

        signals = _detect_signals(
            "TEST",
            frame,
            [{"price": 100.0, "kind": "resistance", "touches": 3, "strength": 0.8, "tolerance": 0.2}],
            ScreenerParams(strategies=("PRB2",)),
        )

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["strategy"], "PRB2")
        self.assertEqual(signals[0]["signal_bar_date"], "2026-07-03")
        self.assertEqual(signals[0]["entry_day"], "2026-07-07")
        self.assertIn("behind_day1", {item["variant"] for item in signals[0]["stop_options"]})
        self.assertEqual(signals[0]["stop_variant"], "behind_day1")
        self.assertEqual(signals[0]["pattern_bar_dates"], ["2026-07-03", "2026-07-06"])

    def test_prb2_long_rejects_completed_entry_day_that_breaks_the_level(self) -> None:
        rows = [
            {"date": "2026-07-01", "open": 99.2, "high": 100.0, "low": 98.8, "close": 99.6, "volume": 1_000_000},
            {"date": "2026-07-02", "open": 99.7, "high": 100.1, "low": 99.1, "close": 99.8, "volume": 1_000_000},
            {"date": "2026-07-03", "open": 99.8, "high": 101.3, "low": 99.5, "close": 100.9, "volume": 1_600_000},
            {"date": "2026-07-06", "open": 100.4, "high": 101.2, "low": 100.1, "close": 100.7, "volume": 1_200_000},
            {"date": "2026-07-07", "open": 100.3, "high": 101.5, "low": 99.0, "close": 99.4, "volume": 1_200_000},
        ]
        signals = _detect_signals(
            "TEST",
            _frame(rows),
            [{"price": 100.0, "kind": "resistance", "touches": 3, "strength": 0.8, "tolerance": 0.2}],
            ScreenerParams(strategies=("PRB2",)),
            entry_day_partial=False,
        )

        self.assertEqual(signals, [])

    def test_prb2_partial_entry_day_uses_open_and_marks_signal_in_progress(self) -> None:
        rows = [
            {"date": "2026-07-01", "open": 99.2, "high": 100.0, "low": 98.8, "close": 99.6, "volume": 1_000_000},
            {"date": "2026-07-02", "open": 99.7, "high": 100.1, "low": 99.1, "close": 99.8, "volume": 1_000_000},
            {"date": "2026-07-03", "open": 99.8, "high": 101.3, "low": 99.5, "close": 100.9, "volume": 1_600_000},
            {"date": "2026-07-06", "open": 100.4, "high": 101.2, "low": 100.1, "close": 100.7, "volume": 1_200_000},
            {"date": "2026-07-07", "open": 100.3, "high": 101.5, "low": 100.0, "close": 99.85, "volume": 1_200_000},
        ]
        signals = _detect_signals(
            "TEST",
            _frame(rows),
            [{"price": 100.0, "kind": "resistance", "touches": 3, "strength": 0.8, "tolerance": 0.2}],
            ScreenerParams(strategies=("PRB2",)),
            entry_day_partial=True,
        )

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["side"], "LONG")
        self.assertEqual(signals[0]["entry_day_status"], "IN_PROGRESS")

    def test_explicit_anchor_without_bar_is_reported(self) -> None:
        rows = [
            {
                "date": str((pd.Timestamp("2026-01-01") + pd.Timedelta(days=index)).date()),
                "open": 20.0,
                "high": 20.5,
                "low": 19.5,
                "close": 20.0,
                "volume": 1_500_000,
            }
            for index in range(45)
        ]

        result = run_market_screener(
            symbols=["ANCHOR"],
            bars_loader=lambda _symbol, _timeframe: pd.DataFrame(rows),
            watchlist={},
            params=ScreenerParams(anchor_date="2026-02-20"),
        )

        self.assertEqual(result["anchor_status"], "MISSING")
        self.assertEqual(result["latest_available_date"], "2026-02-14")
        self.assertEqual(result["anchor_warnings"][0]["reason"], "anchor_data_unavailable")

    def test_prb1_rejects_a_downward_approach_to_resistance(self) -> None:
        rows = [
            {"date": "2026-07-13", "open": 70.0, "high": 71.0, "low": 69.0, "close": 70.0, "volume": 1_000_000},
            {"date": "2026-07-14", "open": 68.8, "high": 69.2, "low": 67.8, "close": 68.2, "volume": 1_000_000},
            {"date": "2026-07-15", "open": 65.0, "high": 66.0, "low": 63.5, "close": 64.0, "volume": 1_000_000},
            {"date": "2026-07-16", "open": 61.0, "high": 62.0, "low": 59.8, "close": 60.2, "volume": 1_000_000},
            {"date": "2026-07-17", "open": 59.2, "high": 61.0, "low": 58.0, "close": 60.4, "volume": 1_600_000},
            {"date": "2026-07-20", "open": 60.2, "high": 61.0, "low": 59.9, "close": 60.5, "volume": 1_200_000},
        ]
        signals = _detect_signals(
            "SEI",
            _frame(rows),
            [{"price": 59.78, "kind": "resistance", "touches": 3, "strength": 0.8, "tolerance": 0.1}],
            ScreenerParams(strategies=("PRB1",)),
        )

        self.assertEqual(signals, [])

    def test_lp1_can_use_role_flipped_resistance_as_support(self) -> None:
        rows = [
            {"date": "2026-07-13", "open": 64.0, "high": 64.5, "low": 63.5, "close": 64.0, "volume": 1_100_000},
            {"date": "2026-07-14", "open": 63.0, "high": 63.4, "low": 62.5, "close": 63.0, "volume": 1_100_000},
            {"date": "2026-07-15", "open": 62.0, "high": 62.4, "low": 61.4, "close": 61.8, "volume": 1_100_000},
            {"date": "2026-07-16", "open": 60.8, "high": 61.0, "low": 60.0, "close": 60.4, "volume": 1_100_000},
            {"date": "2026-07-17", "open": 59.7, "high": 60.8, "low": 58.4, "close": 60.4, "volume": 1_600_000},
            {"date": "2026-07-20", "open": 60.2, "high": 61.0, "low": 59.9, "close": 60.6, "volume": 1_200_000},
        ]
        signals = _detect_signals(
            "SEI",
            _frame(rows),
            [{"price": 59.78, "kind": "resistance", "touches": 3, "strength": 0.8, "tolerance": 0.1}],
            ScreenerParams(strategies=("LP1",)),
        )

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["strategy"], "LP1")
        self.assertEqual(signals[0]["level_type"], "support")
        self.assertEqual(signals[0]["level_origin_type"], "resistance")

    def test_prb1_marks_a_chased_entry_gap(self) -> None:
        rows = [
            {"date": "2026-07-01", "open": 99.0, "high": 99.6, "low": 98.5, "close": 99.2, "volume": 1_000_000},
            {"date": "2026-07-02", "open": 99.3, "high": 99.8, "low": 99.0, "close": 99.7, "volume": 1_000_000},
            {"date": "2026-07-03", "open": 99.8, "high": 101.2, "low": 99.4, "close": 100.8, "volume": 1_600_000},
            {"date": "2026-07-06", "open": 101.5, "high": 102.0, "low": 101.2, "close": 101.8, "volume": 1_200_000},
        ]
        signals = _detect_signals(
            "TEST",
            _frame(rows),
            [{"price": 100.0, "kind": "resistance", "touches": 3, "strength": 0.8, "tolerance": 0.2}],
            ScreenerParams(strategies=("PRB1",), gap_max_atr=3.0),
        )

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["status"], "chased_gap_skip")
        self.assertGreater(signals[0]["entry_chase_atr"], 0.3)

    def test_strict_quality_and_execution_costs_are_reflected_in_signal(self) -> None:
        rows = [
            {"date": "2026-07-01", "open": 99.0, "high": 99.6, "low": 98.5, "close": 99.2, "volume": 1_000_000},
            {"date": "2026-07-02", "open": 99.3, "high": 99.8, "low": 99.0, "close": 99.7, "volume": 1_000_000},
            {"date": "2026-07-03", "open": 99.8, "high": 101.2, "low": 99.4, "close": 100.8, "volume": 1_600_000},
            {"date": "2026-07-06", "open": 100.2, "high": 101.0, "low": 100.0, "close": 100.7, "volume": 1_200_000},
        ]
        metrics = {
            "spread_ok": None,
            "corporate_action_ok": None,
            "event_ok": True,
            "listing_status_ok": None,
            "split_adjusted": None,
            "missing_quality_data": ["spread", "corporate_actions", "listing_status", "split_adjustment"],
            "data_quality_complete": False,
            "median_spread_pct_20": None,
            "signal_spread_pct": None,
        }
        signals = _detect_signals(
            "TEST",
            _frame(rows),
            [{"price": 100.0, "kind": "resistance", "touches": 3, "strength": 0.8, "tolerance": 0.2}],
            ScreenerParams(
                strategies=("PRB1",),
                require_complete_quality=True,
                stop_variant="behind_signal_bar",
                slippage_per_share=0.02,
                fees_per_share=0.01,
            ),
            metrics=metrics,
        )

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["status"], "review_required")
        self.assertEqual(signals[0]["stop_variant"], "behind_signal_bar")
        self.assertEqual(signals[0]["execution_cost_per_share"], 0.03)
        self.assertGreater(signals[0]["sized_risk_per_share"], signals[0]["price_risk_per_share"])

    def test_strong_breakout_does_not_remove_historical_resistance(self) -> None:
        rows = []
        for index, date in enumerate(pd.bdate_range("2026-05-01", periods=30)):
            high = 100.0 if index in {6, 18} else 96.0
            rows.append({"date": str(date.date()), "open": 95.0, "high": high, "low": 94.0, "close": 95.0, "volume": 1_000_000})
        rows[-1].update({"open": 99.5, "high": 103.0, "low": 99.0, "close": 102.0})
        frame = _frame(rows)

        levels = _pivot_levels(frame, k=2, min_touches=2, tolerance_atr=0.10)

        self.assertTrue(any(level["kind"] == "resistance" and abs(level["price"] - 100.0) < 0.01 for level in levels))

    def test_public_scan_rejects_bad_spread_when_spread_data_exists(self) -> None:
        rows = []
        for index, date in enumerate(pd.bdate_range("2026-04-01", periods=45)):
            rows.append(
                {
                    "date": str(date.date()),
                    "open": 20.0,
                    "high": 20.8 if index % 7 == 0 else 20.2,
                    "low": 19.2 if index % 7 == 3 else 19.8,
                    "close": 20.0,
                    "volume": 1_500_000,
                    "spread_pct": 0.006,
                }
            )

        def loader(_symbol: str, _timeframe: str) -> pd.DataFrame:
            return pd.DataFrame(rows)

        result = run_market_screener(
            symbols=["WIDE"],
            bars_loader=loader,
            watchlist={},
            params=ScreenerParams(),
        )

        self.assertEqual(result["rejected"][0]["reason"], "bad_spread")
        self.assertIn("bad_spread", result["reason_counts"])

    def test_public_scan_uses_anchor_date_as_bar_cutoff(self) -> None:
        rows = [
            {
                "date": str((pd.Timestamp("2026-01-01") + pd.Timedelta(days=index)).date()),
                "open": 20.0,
                "high": 20.5,
                "low": 19.5,
                "close": 20.0,
                "volume": 1_500_000,
            }
            for index in range(45)
        ]

        def loader(_symbol: str, _timeframe: str) -> pd.DataFrame:
            return pd.DataFrame(rows)

        result = run_market_screener(
            symbols=["ANCHOR"],
            bars_loader=loader,
            watchlist={},
            params=ScreenerParams(anchor_date="2026-01-10"),
        )

        self.assertEqual(result["anchor_date"], "2026-01-10")
        self.assertEqual(result["rejected"][0]["reason"], "insufficient_data")

    def test_historical_anchor_ignores_current_undated_news_block(self) -> None:
        frame = _frame(
            [
                {
                    "date": str((pd.Timestamp("2026-01-01") + pd.Timedelta(days=index)).date()),
                    "open": 20.0,
                    "high": 20.5,
                    "low": 19.5,
                    "close": 20.0,
                    "volume": 1_500_000,
                }
                for index in range(45)
            ]
        )

        ok, reasons, metrics = _passes_filters(frame, {"news_blocked": True, "earnings_event": {"headline": "current earnings risk"}})

        self.assertTrue(ok)
        self.assertNotIn("news_risk", reasons)
        self.assertNotIn("earnings_risk", reasons)
        self.assertTrue(metrics["event_ok"])

    def test_public_scan_returns_rejection_reason_counts(self) -> None:
        def loader(_symbol: str, _timeframe: str) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "date": str(date.date()),
                        "open": 2.0,
                        "high": 2.1,
                        "low": 1.9,
                        "close": 2.0,
                        "volume": 10_000,
                    }
                    for date in pd.bdate_range("2026-04-01", periods=45)
                ]
            )

        result = run_market_screener(
            symbols=["LOWP"],
            bars_loader=loader,
            watchlist={},
            params=ScreenerParams(),
        )

        self.assertEqual(result["universe_size"], 1)
        self.assertIn(result["rejected"][0]["reason"], {"price_out_of_range", "illiquid", "no_level"})
        self.assertIsInstance(result["reason_counts"], dict)
