"""Tests for deterministic strategy signals."""

import unittest

import pandas as pd

from src.strategy.false_breakout import detect_false_breakout
from src.strategy.rebound import detect_rebound
from src.strategy.third_touch import detect_third_touch


class StrategyTests(unittest.TestCase):
    def test_false_breakout_short_signal(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 99.0, "high": 101.5, "low": 98.8, "close": 100.7},
                {"open": 100.6, "high": 100.8, "low": 99.0, "close": 99.4},
            ]
        )
        signal = detect_false_breakout(bars, level=100.0, direction="short")
        self.assertEqual(signal["signal"], "SELL")

    def test_rebound_long_signal(self) -> None:
        bars = pd.DataFrame([{"open": 99.8, "high": 101.0, "low": 99.7, "close": 100.6}])
        signal = detect_rebound(bars, level=100.0, direction="long")
        self.assertEqual(signal["signal"], "BUY")

    def test_third_touch_long_signal(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 100.5, "high": 101.0, "low": 100.0, "close": 100.8},
                {"open": 101.0, "high": 102.0, "low": 100.7, "close": 101.5},
                {"open": 101.6, "high": 102.1, "low": 100.8, "close": 101.7},
                {"open": 101.8, "high": 102.3, "low": 100.0, "close": 101.9},
                {"open": 101.7, "high": 102.5, "low": 101.0, "close": 102.1},
                {"open": 102.0, "high": 102.8, "low": 101.1, "close": 102.3},
                {"open": 101.9, "high": 102.7, "low": 100.0, "close": 102.2},
            ]
        )
        signal = detect_third_touch(bars, level=100.0, direction="long")
        self.assertEqual(signal["signal"], "BUY")


if __name__ == "__main__":
    unittest.main()
