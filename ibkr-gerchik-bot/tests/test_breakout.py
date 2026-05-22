"""Tests for Gerchik-style confirmed breakout detection."""

import unittest

import pandas as pd

from src.strategy.breakout import detect_breakout
from src.strategy.levels import Level


class ConfirmedBreakoutTests(unittest.TestCase):
    def test_long_breakout_enters_on_confirmation_close(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 99.84, "high": 100.05, "low": 99.75, "close": 99.90},
                {"open": 99.90, "high": 100.04, "low": 99.82, "close": 99.95},
                {"open": 99.94, "high": 100.08, "low": 99.83, "close": 99.98},
                {"open": 99.98, "high": 100.28, "low": 99.92, "close": 100.22},
                {"open": 100.20, "high": 100.40, "low": 100.12, "close": 100.32},
            ]
        )
        level = Level("AAPL", 100.0, "historical", "daily", 4, 1, 6.0, "swing_high", 102.2, 98.0, atr_value=3.0)

        signal = detect_breakout("AAPL", bars, level)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal, "BUY")
        self.assertEqual(signal.entry, 100.32)
        self.assertLess(signal.stop, signal.metadata["zone_low"])
        self.assertGreaterEqual(signal.reward_risk, 3.0)

    def test_short_breakout_enters_on_confirmation_close(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 50.18, "high": 50.25, "low": 50.02, "close": 50.12},
                {"open": 50.12, "high": 50.20, "low": 50.01, "close": 50.08},
                {"open": 50.08, "high": 50.16, "low": 49.96, "close": 50.02},
                {"open": 50.02, "high": 50.06, "low": 49.78, "close": 49.85},
                {"open": 49.86, "high": 49.90, "low": 49.70, "close": 49.75},
            ]
        )
        level = Level("MSFT", 50.0, "historical", "daily", 4, 1, 6.0, "swing_low", 52.0, 48.5, atr_value=2.0)

        signal = detect_breakout("MSFT", bars, level)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal, "SELL")
        self.assertEqual(signal.entry, 49.75)
        self.assertGreater(signal.stop, signal.metadata["zone_high"])
        self.assertGreaterEqual(signal.reward_risk, 3.0)

    def test_rejects_overextended_breakout_candle(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 99.95, "high": 100.00, "low": 99.90, "close": 99.96},
                {"open": 99.96, "high": 100.01, "low": 99.91, "close": 99.97},
                {"open": 99.97, "high": 100.02, "low": 99.92, "close": 99.98},
                {"open": 99.98, "high": 100.58, "low": 99.94, "close": 100.22},
                {"open": 100.20, "high": 100.40, "low": 100.12, "close": 100.32},
            ]
        )
        level = Level("AAPL", 100.0, "historical", "daily", 4, 1, 6.0, "swing_high", 102.2, 98.0, atr_value=3.0)

        self.assertIsNone(detect_breakout("AAPL", bars, level))

    def test_rejects_atr_travel_without_new_high_exception(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 99.90, "high": 103.00, "low": 99.80, "close": 99.90},
                {"open": 99.90, "high": 100.04, "low": 99.82, "close": 99.95},
                {"open": 99.94, "high": 100.08, "low": 99.83, "close": 99.98},
                {"open": 99.98, "high": 100.28, "low": 99.92, "close": 100.22},
                {"open": 100.20, "high": 100.40, "low": 100.12, "close": 100.32},
            ]
        )
        level = Level("AAPL", 100.0, "historical", "daily", 4, 1, 6.0, "swing_high", 102.2, 98.0, atr_value=2.7)

        self.assertIsNone(detect_breakout("AAPL", bars, level))

    def test_rejects_when_next_level_cannot_pay_minimum_reward_risk(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 99.84, "high": 100.05, "low": 99.75, "close": 99.90},
                {"open": 99.90, "high": 100.04, "low": 99.82, "close": 99.95},
                {"open": 99.94, "high": 100.08, "low": 99.83, "close": 99.98},
                {"open": 99.98, "high": 100.28, "low": 99.92, "close": 100.22},
                {"open": 100.20, "high": 100.40, "low": 100.12, "close": 100.32},
            ]
        )
        level = Level("AAPL", 100.0, "historical", "daily", 4, 1, 6.0, "swing_high", 100.60, 98.0, atr_value=3.0)

        self.assertIsNone(detect_breakout("AAPL", bars, level))


if __name__ == "__main__":
    unittest.main()
