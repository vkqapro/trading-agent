"""Tests for ATR-aware level detection."""

import unittest

import pandas as pd

from src.strategy.levels import (
    Level,
    _first_touch_date_near_price,
    calculate_atr,
    detect_levels,
    filter_weak_levels,
    merge_nearby_levels,
)


class LevelDetectionTests(unittest.TestCase):
    def test_calculate_atr_excludes_abnormal_ranges(self) -> None:
        daily = pd.DataFrame(
            [
                {"date": "2026-04-01", "open": 100, "high": 105, "low": 100, "close": 103},  # 5
                {"date": "2026-04-02", "open": 103, "high": 108, "low": 103, "close": 106},  # 5
                {"date": "2026-04-03", "open": 106, "high": 126, "low": 96, "close": 110},   # 30 abnormal
                {"date": "2026-04-04", "open": 110, "high": 115, "low": 110, "close": 114},  # 5
                {"date": "2026-04-05", "open": 114, "high": 119, "low": 114, "close": 118},  # 5
            ]
        )
        self.assertEqual(calculate_atr(daily, period=5), 5.0)

    def test_detect_levels_returns_daily_levels(self) -> None:
        daily = pd.DataFrame(
            [
                {"date": "2024-01-01", "open": 100, "high": 105, "low": 99, "close": 104},
                {"date": "2024-01-02", "open": 104, "high": 106, "low": 100, "close": 101},
                {"date": "2024-01-03", "open": 101, "high": 107, "low": 100, "close": 106},
                {"date": "2024-01-04", "open": 106, "high": 106, "low": 99, "close": 100},
                {"date": "2024-01-05", "open": 100, "high": 108, "low": 100, "close": 107},
                {"date": "2024-01-08", "open": 107, "high": 109, "low": 101, "close": 108},
            ]
        )
        intraday = pd.DataFrame(
            [
                {"open": 104, "high": 105, "low": 103, "close": 104.5, "volume": 1, "date": "2024-01-01"},
                {"open": 104.5, "high": 106, "low": 104, "close": 105.5, "volume": 1, "date": "2024-01-01"},
            ]
        )
        levels = detect_levels("AAPL", daily, intraday)
        self.assertTrue(levels)
        self.assertTrue(all(level.symbol == "AAPL" for level in levels))
        self.assertTrue(all(level.zone_low <= level.center <= level.zone_high for level in levels))

    def test_first_touch_date_uses_earliest_matching_bar(self) -> None:
        daily = pd.DataFrame(
            [
                {"date": "2026-04-24", "open": 250, "high": 256.07, "low": 249.0, "close": 255.0},
                {"date": "2026-04-27", "open": 254, "high": 256.09, "low": 251.0, "close": 255.5},
                {"date": "2026-04-29", "open": 255, "high": 256.08, "low": 252.0, "close": 255.8},
            ]
        )
        self.assertEqual(_first_touch_date_near_price(daily, 256.07, 0.0025), "04/24/26")

    def test_abnormal_candle_keeps_source_date(self) -> None:
        daily = pd.DataFrame(
            [
                {"date": "2026-02-01", "open": 277.5, "high": 278.90, "low": 276.8, "close": 278.1},
                {"date": "2026-02-02", "open": 278.1, "high": 278.92, "low": 277.1, "close": 277.9},
                {"date": "2026-02-03", "open": 270.0, "high": 272.0, "low": 269.5, "close": 271.0},
                {"date": "2026-02-04", "open": 272.29, "high": 278.95, "low": 272.28, "close": 276.49},
                {"date": "2026-02-05", "open": 276.0, "high": 277.0, "low": 274.0, "close": 275.5},
                {"date": "2026-02-06", "open": 275.5, "high": 276.2, "low": 274.8, "close": 275.2},
                {"date": "2026-02-07", "open": 275.2, "high": 275.8, "low": 274.7, "close": 275.1},
            ]
        )
        intraday = pd.DataFrame([{"date": "2026-02-07", "open": 275.0, "high": 275.5, "low": 274.9, "close": 275.2, "volume": 1}])
        levels = detect_levels("AAPL", daily, intraday)
        abnormal_levels = [level for level in levels if "abnormal_candle" in level.families]
        self.assertTrue(abnormal_levels)
        self.assertIn("02/04/26", {level.source_date for level in abnormal_levels})

    def test_merge_nearby_levels_creates_zone_with_weighted_center(self) -> None:
        daily = pd.DataFrame(
            [
                {"date": "2026-04-24", "open": 239.0, "high": 240.56, "low": 238.5, "close": 240.0},
                {"date": "2026-04-25", "open": 239.2, "high": 240.40, "low": 238.8, "close": 239.7},
                {"date": "2026-04-27", "open": 239.4, "high": 240.43, "low": 238.9, "close": 239.9},
                {"date": "2026-04-29", "open": 239.9, "high": 240.54, "low": 239.0, "close": 240.2},
            ]
        )
        levels = [
            Level(
                "AAPL", 240.55, "historical", "daily", 4, 1, 7.5, "swing_high",
                zone_low=240.49, zone_high=240.61, center=240.55, families=["structural", "historical"],
                touch_indices=[0, 1, 2, 3], last_touch_index=3,
            ),
            Level(
                "AAPL", 240.40, "mirror", "daily", 2, 1, 6.0, "repeated_highs",
                zone_low=240.34, zone_high=240.46, center=240.40, families=["structural", "mirror"],
                touch_indices=[1, 2], last_touch_index=2,
            ),
        ]

        merged = merge_nearby_levels(levels, daily, atr_value=0.8, merge_distance=0.20, ultra_close_distance=0.06, zone_buffer=0.06)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].zone_low, 240.34)
        self.assertEqual(merged[0].zone_high, 240.61)
        self.assertAlmostEqual(merged[0].center, 240.5, places=2)
        self.assertEqual(merged[0].first_touch_date, "04/24/26")
        self.assertIn("mirror", merged[0].families)
        self.assertIn("historical", merged[0].families)

    def test_ultra_close_merges_across_families(self) -> None:
        daily = pd.DataFrame(
            [
                {"date": "2026-04-24", "open": 255.0, "high": 256.08, "low": 254.0, "close": 255.5},
                {"date": "2026-04-29", "open": 255.9, "high": 256.12, "low": 255.2, "close": 256.1},
            ]
        )
        levels = [
            Level("AAPL", 256.07, "historical", "daily", 3, 1, 7.0, "swing_high", zone_low=256.01, zone_high=256.13, center=256.07, families=["structural", "historical"]),
            Level("AAPL", 256.10, "gap", "daily", 2, 1, 5.0, "gap_upper", zone_low=256.04, zone_high=256.16, center=256.10, families=["gap"]),
        ]

        merged = merge_nearby_levels(levels, daily, atr_value=1.0, merge_distance=0.25, ultra_close_distance=0.08, zone_buffer=0.12)
        self.assertEqual(len(merged), 1)

    def test_filter_weak_levels_rejects_too_close_weaker_overlap(self) -> None:
        daily = pd.DataFrame(
            [
                {"date": "2026-04-24", "open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0},
                {"date": "2026-04-25", "open": 99.2, "high": 101.2, "low": 98.2, "close": 100.2},
                {"date": "2026-04-28", "open": 99.4, "high": 101.1, "low": 98.7, "close": 100.3},
            ]
        )
        stronger = Level(
            "AAPL", 100.0, "historical", "daily", 4, 2, 9.0, "swing_high",
            zone_low=99.7, zone_high=100.3, center=100.0, families=["structural", "historical"], touch_indices=[0, 1, 2], last_touch_index=2
        )
        weaker = Level(
            "AAPL", 100.12, "mirror", "daily", 2, 0, 4.0, "repeated_highs",
            zone_low=99.9, zone_high=100.34, center=100.12, families=["structural", "mirror"], touch_indices=[1, 2], last_touch_index=2
        )

        filtered = filter_weak_levels([stronger, weaker], daily, atr_value=3.0, merge_distance=0.75)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].price, 100.0)


if __name__ == "__main__":
    unittest.main()
