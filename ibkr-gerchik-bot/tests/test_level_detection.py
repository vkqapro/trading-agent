"""Tests for level detection."""

import unittest

import pandas as pd

from src.strategy.levels import detect_levels


class LevelDetectionTests(unittest.TestCase):
    def test_detect_levels_returns_daily_levels(self) -> None:
        daily = pd.DataFrame(
            [
                {"open": 100, "high": 105, "low": 99, "close": 104},
                {"open": 104, "high": 106, "low": 100, "close": 101},
                {"open": 101, "high": 107, "low": 100, "close": 106},
                {"open": 106, "high": 106, "low": 99, "close": 100},
                {"open": 100, "high": 108, "low": 100, "close": 107},
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

