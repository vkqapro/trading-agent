from __future__ import annotations

from unittest import TestCase

import pandas as pd

from dashboard_react.market_screener import ScreenerParams, _add_metrics, _detect_signals, run_market_screener


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

