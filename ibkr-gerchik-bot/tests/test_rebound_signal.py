"""Tests for rebound signal."""

import unittest

import pandas as pd

from src.strategy.levels import Level
from src.strategy.rebound import detect_rebound


class ReboundSignalTests(unittest.TestCase):
    def test_long_rebound_waits_for_confirmation(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 100.6, "high": 101.2, "low": 100.4, "close": 100.8},
                {"open": 100.6, "high": 100.7, "low": 99.75, "close": 100.25},
                {"open": 100.24, "high": 101.0, "low": 100.2, "close": 100.9},
            ]
        )
        level = Level("AAPL", 100.0, "historical", "daily", 4, 1, 5.0, "swing_low", 105.0, 98.0, atr_value=10.0)

        self.assertIsNone(detect_rebound("AAPL", bars.iloc[:-1], level))

        signal = detect_rebound("AAPL", bars, level)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal, "BUY")
        self.assertEqual(signal.entry, 100.9)
        self.assertEqual(signal.metadata["confirmation_type"], "bullish_confirmation")

    def test_short_rebound_waits_for_confirmation(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 99.4, "high": 99.7, "low": 99.0, "close": 99.2},
                {"open": 99.4, "high": 100.35, "low": 99.3, "close": 99.75},
                {"open": 99.76, "high": 99.8, "low": 98.9, "close": 99.1},
            ]
        )
        level = Level("MSFT", 100.0, "historical", "daily", 4, 1, 5.0, "swing_high", 102.0, 95.0, atr_value=10.0)

        self.assertIsNone(detect_rebound("MSFT", bars.iloc[:-1], level))

        signal = detect_rebound("MSFT", bars, level)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal, "SELL")
        self.assertEqual(signal.entry, 99.1)
        self.assertEqual(signal.metadata["confirmation_type"], "bearish_confirmation")
