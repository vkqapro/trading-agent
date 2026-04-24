"""Tests for breakout signal."""

import unittest

import pandas as pd

from src.strategy.breakout import detect_breakout
from src.strategy.levels import Level


class BreakoutSignalTests(unittest.TestCase):
    def test_long_breakout(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 99.88, "high": 100.00, "low": 99.82, "close": 99.90},
                {"open": 99.89, "high": 100.04, "low": 99.84, "close": 99.93},
                {"open": 99.92, "high": 100.06, "low": 99.87, "close": 99.96},
                {"open": 99.97, "high": 100.72, "low": 99.94, "close": 100.45},
            ]
        )
        level = Level("AAPL", 100.0, "mirror", "daily", 4, 1, 5.0, "swing_high", 104.0, 98.0)
        signal = detect_breakout("AAPL", bars, level)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal, "BUY")
