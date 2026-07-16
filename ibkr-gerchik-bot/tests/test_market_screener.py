from __future__ import annotations

from unittest import TestCase

import pandas as pd

from dashboard_react.market_screener import ScreenerParams, _add_metrics, _detect_signals, _passes_filters, run_market_screener


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
        rows[-1].update({"open": 100.05, "high": 101.0, "low": 99.2, "close": 100.8, "volume": 1_800_000})
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

    def test_lp2_requires_false_side_close(self) -> None:
        rows = [
            {"date": "2026-07-01", "open": 101.0, "high": 101.2, "low": 100.5, "close": 100.9, "volume": 1_100_000},
            {"date": "2026-07-02", "open": 100.8, "high": 101.0, "low": 100.4, "close": 100.7, "volume": 1_100_000},
            {"date": "2026-07-03", "open": 100.6, "high": 101.0, "low": 99.7, "close": 100.2, "volume": 1_100_000},
            {"date": "2026-07-06", "open": 100.1, "high": 101.4, "low": 99.4, "close": 100.8, "volume": 1_100_000},
        ]
        frame = _frame(rows)

        signals = _detect_signals(
            "TEST",
            frame,
            [{"price": 100.0, "kind": "support", "touches": 3, "strength": 0.8, "tolerance": 0.2}],
            ScreenerParams(strategies=("LP2",)),
        )

        self.assertEqual(signals, [])

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

    def test_public_scan_rejects_bad_spread_when_spread_data_exists(self) -> None:
        rows = []
        for index in range(45):
            day = (index % 28) + 1
            rows.append(
                {
                    "date": f"2026-05-{day:02d}",
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
                        "date": f"2026-05-{(index % 28) + 1:02d}",
                        "open": 2.0,
                        "high": 2.1,
                        "low": 1.9,
                        "close": 2.0,
                        "volume": 10_000,
                    }
                    for index in range(45)
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
