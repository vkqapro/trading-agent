"""Tests for one-bar, two-bar, and complex false breakouts."""

import unittest

import pandas as pd

from src.strategy.false_breakout_complex import detect_false_breakout_complex
from src.strategy.false_breakout_one_bar import detect_false_breakout_one_bar
from src.strategy.false_breakout_two_bar import detect_false_breakout_two_bar
from src.strategy.levels import Level


class FalseBreakoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.level = Level("AAPL", 100.0, "historical", "daily", 4, 1, 5.0, "swing", 104.0, 96.0)

    def test_one_bar_false_breakout(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 100.8, "high": 101.0, "low": 100.2, "close": 100.4},
                {"open": 100.3, "high": 100.7, "low": 99.6, "close": 100.3},
            ]
        )
        signal = detect_false_breakout_one_bar("AAPL", bars, self.level)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal, "BUY")

    def test_two_bar_false_breakout(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 100.5, "high": 100.7, "low": 99.3, "close": 99.6},
                {"open": 99.7, "high": 100.8, "low": 99.5, "close": 100.4},
            ]
        )
        signal = detect_false_breakout_two_bar("AAPL", bars, self.level)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal, "BUY")

    def test_complex_false_breakout(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 99.8, "high": 100.0, "low": 99.2, "close": 99.5},
                {"open": 99.5, "high": 99.9, "low": 99.1, "close": 99.4},
                {"open": 99.4, "high": 99.8, "low": 99.0, "close": 99.3},
                {"open": 99.4, "high": 100.6, "low": 99.2, "close": 100.3},
            ]
        )
        signal = detect_false_breakout_complex("AAPL", bars, self.level)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal, "BUY")

