"""Tests for premarket levels Excel export."""

from __future__ import annotations

import unittest

import pandas as pd

from src.reports.levels_export import export_premarket_levels_report


class LevelsExportTests(unittest.TestCase):
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
                ],
            )
            self.assertEqual(frame.loc[0, "SourceDate"], "02/04/26")
            self.assertEqual(frame.loc[0, "FirstTouchDate"], "12/24/25")
            self.assertEqual(frame.loc[0, "Families"], "abnormal_candle, historical, structural")
        finally:
            output_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
