"""Tests for breakout signal."""

import unittest

import pandas as pd

from src.strategy.breakout import detect_breakout
from src.strategy.levels import Level


class BreakoutSignalTests(unittest.TestCase):
    def test_long_breakout_requires_confirmation(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 99.84, "high": 100.05, "low": 99.75, "close": 99.90},
                {"open": 99.90, "high": 100.04, "low": 99.82, "close": 99.95},
                {"open": 99.94, "high": 100.08, "low": 99.83, "close": 99.98},
                {"open": 99.98, "high": 100.28, "low": 99.92, "close": 100.22},
                {"open": 100.20, "high": 100.40, "low": 100.12, "close": 100.32},
            ]
        )
        level = Level("AAPL", 100.0, "mirror", "daily", 4, 1, 5.0, "swing_high", 102.2, 98.0, atr_value=3.0)

        self.assertIsNone(detect_breakout("AAPL", bars.iloc[:-1], level))

        signal = detect_breakout("AAPL", bars, level)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal, "BUY")
        self.assertEqual(signal.strategy, "confirmed_breakout")
        self.assertEqual(signal.entry, 100.32)
        self.assertEqual(signal.metadata["confirmation_type"], "bullish_confirmation")
