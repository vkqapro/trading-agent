"""Tests for rebound signal."""

import unittest

import pandas as pd

from src.strategy.levels import Level
from src.strategy.rebound import detect_rebound


class ReboundSignalTests(unittest.TestCase):
    def test_long_rebound(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 100.4, "high": 101.2, "low": 99.9, "close": 100.8},
                {"open": 100.6, "high": 101.0, "low": 99.8, "close": 100.9},
            ]
        )
        level = Level("AAPL", 100.0, "historical", "daily", 4, 1, 5.0, "swing_low", 104.0, 98.0)
        signal = detect_rebound("AAPL", bars, level)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal, "BUY")

