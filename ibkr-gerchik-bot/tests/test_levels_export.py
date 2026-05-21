"""Tests for premarket levels Excel export."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from src.config import SETTINGS
from src.reports.levels_export import export_premarket_levels_report


class LevelsExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._reports_dir = TemporaryDirectory()
        self._original_reports_dir = SETTINGS.paths.reports_dir
        object.__setattr__(SETTINGS.paths, "reports_dir", Path(self._reports_dir.name))

    def tearDown(self) -> None:
        object.__setattr__(SETTINGS.paths, "reports_dir", self._original_reports_dir)
        self._reports_dir.cleanup()

    def test_export_includes_source_and_first_touch_dates(self) -> None:
        watchlist = {
            "AAPL": {
                "levels": [
                    {
                        "price": 275.43,
                        "zone_low": 274.92,
                        "zone_high": 276.62,
                        "touches": 53,
                        "strength_score": 77.89,
                        "type": "historical",
                        "families": ["abnormal_candle", "historical", "structural"],
                        "source_date": "02/04/26",
                        "first_touch_date": "12/24/25",
                    }
                ]
            }
        }

        output_path = export_premarket_levels_report(watchlist)
        try:
            frame = pd.read_excel(output_path)
            self.assertEqual(
                list(frame.columns),
                [
                    "Ticker",
                    "SourceDate",
                    "FirstTouchDate",
                    "Level",
                    "ZoneLow",
                    "ZoneHigh",
                    "Touches",
                    "StrengthScore",
                    "LevelType",
                    "Families",
                    "UsedForTrading",
                    "OptimizationReason",
                    "TradeLevelEligible",
                    "MinTradeLevelTouches",
                    "PreviousRawLevel",
                    "RawCleanGapToPrevious",
                    "RawCleanGapAtrPctPrevious",
                    "NextRawLevel",
                    "RawCleanGapToNext",
                    "RawCleanGapAtrPctNext",
                    "TradeLevelBelow",
                    "TradeLevelBelowTouches",
                    "TradeCleanGapBelow",
                    "TradeCleanGapAtrPctBelow",
                    "TradeableRoomBelow",
                    "SkippedRawLevelsBelow",
                    "TradeLevelAbove",
                    "TradeLevelAboveTouches",
                    "TradeCleanGapAbove",
                    "TradeCleanGapAtrPctAbove",
                    "TradeableRoomAbove",
                    "SkippedRawLevelsAbove",
                ],
            )
            self.assertEqual(frame.loc[0, "SourceDate"], "02/04/26")
            self.assertEqual(frame.loc[0, "FirstTouchDate"], "12/24/25")
            self.assertEqual(frame.loc[0, "Families"], "abnormal_candle, historical, structural")
        finally:
            output_path.unlink(missing_ok=True)

    def test_export_skips_close_raw_levels_for_next_qualified_trade_level(self) -> None:
        watchlist = {
            "AMZN": {
                "daily_atr": 5.4,
                "levels": [
                    {
                        "price": 264.50,
                        "center": 264.50,
                        "zone_low": 263.82,
                        "zone_high": 265.54,
                        "touches": 17,
                        "strength_score": 32.97,
                        "type": "historical",
                        "families": ["gap", "historical", "structural"],
                    },
                    {
                        "price": 267.22,
                        "center": 267.22,
                        "zone_low": 266.54,
                        "zone_high": 267.90,
                        "touches": 6,
                        "strength_score": 11.97,
                        "type": "gap",
                        "families": ["gap"],
                    },
                    {
                        "price": 276.08,
                        "center": 276.08,
                        "zone_low": 275.43,
                        "zone_high": 276.73,
                        "touches": 4,
                        "strength_score": 7.69,
                        "type": "gap",
                        "families": ["gap"],
                    },
                ],
            }
        }

        output_path = export_premarket_levels_report(watchlist)
        try:
            frame = pd.read_excel(output_path)
            amzn_264 = frame[frame["Level"] == 264.50].iloc[0]
            self.assertAlmostEqual(float(amzn_264["NextRawLevel"]), 267.22, places=2)
            self.assertAlmostEqual(float(amzn_264["RawCleanGapToNext"]), 1.0, places=2)
            self.assertAlmostEqual(float(amzn_264["RawCleanGapAtrPctNext"]), 0.1852, places=4)
            self.assertAlmostEqual(float(amzn_264["TradeLevelAbove"]), 276.08, places=2)
            self.assertAlmostEqual(float(amzn_264["TradeCleanGapAbove"]), 9.89, places=2)
            self.assertGreaterEqual(float(amzn_264["TradeCleanGapAtrPctAbove"]), 1.5)
            self.assertEqual(amzn_264["TradeableRoomAbove"], "YES")
            self.assertEqual(int(amzn_264["SkippedRawLevelsAbove"]), 1)
        finally:
            output_path.unlink(missing_ok=True)

    def test_export_uses_nearest_qualified_trade_level_for_room_columns(self) -> None:
        watchlist = {
            "TEST": {
                "daily_atr": 5.0,
                "levels": [
                    {
                        "price": 100.0,
                        "center": 100.0,
                        "zone_low": 99.5,
                        "zone_high": 100.5,
                        "touches": 8,
                        "strength_score": 12.0,
                        "type": "historical",
                        "families": ["historical", "structural"],
                    },
                    {
                        "price": 110.0,
                        "center": 110.0,
                        "zone_low": 109.5,
                        "zone_high": 110.5,
                        "touches": 4,
                        "strength_score": 20.0,
                        "type": "gap",
                        "families": ["gap"],
                    },
                    {
                        "price": 120.0,
                        "center": 120.0,
                        "zone_low": 119.5,
                        "zone_high": 120.5,
                        "touches": 12,
                        "strength_score": 15.0,
                        "type": "historical",
                        "families": ["historical", "structural"],
                    },
                ],
            }
        }

        output_path = export_premarket_levels_report(watchlist)
        try:
            frame = pd.read_excel(output_path)
            row = frame[frame["Level"] == 100.0].iloc[0]
            self.assertAlmostEqual(float(row["TradeLevelAbove"]), 110.0, places=2)
            self.assertEqual(int(row["TradeLevelAboveTouches"]), 4)
            self.assertEqual(row["TradeableRoomAbove"], "YES")
        finally:
            output_path.unlink(missing_ok=True)

    def test_export_marks_raw_levels_removed_from_optimized_trade_set(self) -> None:
        watchlist = {
            "SANM": {
                "daily_atr": 12.5,
                "raw_levels": [
                    {
                        "price": 242.04,
                        "center": 242.04,
                        "zone_low": 239.00,
                        "zone_high": 243.54,
                        "touches": 6,
                        "strength_score": 11.86,
                        "type": "gap",
                        "families": ["gap"],
                    },
                    {
                        "price": 236.27,
                        "center": 236.27,
                        "zone_low": 233.28,
                        "zone_high": 238.59,
                        "touches": 9,
                        "strength_score": 22.75,
                        "type": "historical",
                        "families": ["historical", "structural"],
                    },
                    {
                        "price": 204.98,
                        "center": 204.98,
                        "zone_low": 203.48,
                        "zone_high": 206.48,
                        "touches": 3,
                        "strength_score": 6.53,
                        "type": "gap",
                        "families": ["gap"],
                    },
                ],
                "levels": [
                    {
                        "price": 236.27,
                        "center": 236.27,
                        "zone_low": 233.28,
                        "zone_high": 238.59,
                        "touches": 9,
                        "strength_score": 22.75,
                        "type": "historical",
                        "families": ["historical", "structural"],
                    },
                    {
                        "price": 204.98,
                        "center": 204.98,
                        "zone_low": 203.48,
                        "zone_high": 206.48,
                        "touches": 3,
                        "strength_score": 6.53,
                        "type": "gap",
                        "families": ["gap"],
                    },
                ],
            }
        }

        output_path = export_premarket_levels_report(watchlist)
        try:
            frame = pd.read_excel(output_path)
            raw_242 = frame[frame["Level"] == 242.04].iloc[0]
            optimized_236 = frame[frame["Level"] == 236.27].iloc[0]
            self.assertEqual(raw_242["UsedForTrading"], "NO")
            self.assertEqual(raw_242["OptimizationReason"], "too_close_to_selected_trade_level")
            self.assertEqual(optimized_236["UsedForTrading"], "YES")
            self.assertEqual(optimized_236["OptimizationReason"], "selected")
        finally:
            output_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
