"""Tests for level detection."""

import unittest

import pandas as pd

from src.strategy.levels import Level, _first_touch_date_near_price, detect_levels, merge_nearby_levels


class LevelDetectionTests(unittest.TestCase):
    def test_detect_levels_returns_daily_levels(self) -> None:
        daily = pd.DataFrame(
            [
                {"date": "2024-01-01", "open": 100, "high": 105, "low": 99, "close": 104},
                {"date": "2024-01-02", "open": 104, "high": 106, "low": 100, "close": 101},
                {"date": "2024-01-03", "open": 101, "high": 107, "low": 100, "close": 106},
                {"date": "2024-01-04", "open": 106, "high": 106, "low": 99, "close": 100},
                {"date": "2024-01-05", "open": 100, "high": 108, "low": 100, "close": 107},
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

    def test_first_touch_date_uses_earliest_matching_bar(self) -> None:
        daily = pd.DataFrame(
            [
                {"date": "2026-04-24", "open": 250, "high": 256.07, "low": 249.0, "close": 255.0},
                {"date": "2026-04-27", "open": 254, "high": 256.09, "low": 251.0, "close": 255.5},
                {"date": "2026-04-29", "open": 255, "high": 256.08, "low": 252.0, "close": 255.8},
            ]
        )
        self.assertEqual(_first_touch_date_near_price(daily, 256.07, 0.0025), "04/24/26")

    def test_merge_nearby_levels_creates_one_zone(self) -> None:
        daily = pd.DataFrame(
            [
                {"date": "2026-04-24", "open": 239.0, "high": 240.56, "low": 238.5, "close": 240.0},
                {"date": "2026-04-27", "open": 239.4, "high": 240.43, "low": 238.9, "close": 239.9},
                {"date": "2026-04-29", "open": 239.9, "high": 240.54, "low": 239.0, "close": 240.2},
            ]
        )
        levels = [
            Level("AAPL", 240.55, "historical", "daily", 3, 1, 0.0, "swing_high", first_touch_date="04/24/26"),
            Level("AAPL", 240.40, "mirror", "daily", 3, 1, 0.0, "repeated_highs", first_touch_date="04/27/26"),
        ]

        merged = merge_nearby_levels(levels, daily)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].zone_low, 240.4)
        self.assertEqual(merged[0].zone_high, 240.55)
        self.assertEqual(merged[0].first_touch_date, "04/24/26")

    def test_merge_nearby_levels_deduplicates_touch_bars(self) -> None:
        daily = pd.DataFrame(
            [
                {"date": "2026-04-24", "open": 239.0, "high": 240.56, "low": 238.5, "close": 240.0},
                {"date": "2026-04-27", "open": 239.4, "high": 240.43, "low": 238.9, "close": 239.9},
                {"date": "2026-04-29", "open": 239.9, "high": 240.54, "low": 239.0, "close": 240.2},
            ]
        )
        levels = [
            Level("AAPL", 240.55, "historical", "daily", 3, 1, 0.0, "swing_high"),
            Level("AAPL", 240.40, "historical", "daily", 3, 1, 0.0, "swing_high"),
        ]

        merged = merge_nearby_levels(levels, daily)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].touches, 3)
