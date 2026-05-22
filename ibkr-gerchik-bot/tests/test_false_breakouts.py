"""Tests for Gerchik-style false breakout strategies."""

import unittest

import pandas as pd

from src.strategy.false_breakout_complex import (
    detect_false_breakout as detect_complex_false_breakout,
    detect_false_breakout_complex,
)
from src.strategy.false_breakout_one_bar import (
    detect_false_breakout as detect_one_bar_false_breakout,
    detect_false_breakout_one_bar,
)
from src.strategy.false_breakout_two_bar import (
    detect_false_breakout as detect_two_bar_false_breakout,
    detect_false_breakout_two_bar,
)
from src.strategy.levels import Level


class FalseBreakoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.level = Level(
            symbol="AAPL",
            price=100.0,
            type="historical",
            timeframe="daily",
            touches=4,
            false_breakouts=1,
            strength_score=7.5,
            created_by="swing",
            nearest_upper_level=106.0,
            nearest_lower_level=96.0,
            zone_low=99.85,
            zone_high=100.15,
            center=100.0,
            atr_value=1.8,
        )

    def test_one_bar_false_breakout_buy_signal(self) -> None:
        self.level.atr_value = 2.0
        bars = pd.DataFrame(
            [
                {"open": 103.4, "high": 103.6, "low": 102.9, "close": 103.0},
                {"open": 103.0, "high": 103.1, "low": 102.4, "close": 102.6},
                {"open": 102.0, "high": 102.2, "low": 101.3, "close": 101.5},
                {"open": 101.5, "high": 101.7, "low": 100.5, "close": 100.8},
                {"open": 100.8, "high": 101.0, "low": 99.55, "close": 100.25},
                {"open": 100.12, "high": 101.05, "low": 100.08, "close": 100.95},
            ]
        )
        result = detect_one_bar_false_breakout(bars, self.level, news_context={"risk_level": "LOW"})
        signal = detect_false_breakout_one_bar("AAPL", bars, self.level, news_context={"risk_level": "LOW"})

        self.assertEqual(result["signal"], "BUY")
        self.assertIn("clean false breakout return", result["reason"])
        self.assertEqual(result["context"]["pattern"], "ONE_BAR")
        self.assertEqual(result["position_modifier"], 1.0)
        self.assertIsNotNone(result["risk_per_share"])
        self.assertIsNotNone(result["atr_used"])
        self.assertEqual(result["metadata"]["confirmation_type"], "bullish_confirmation")
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal, "BUY")
        self.assertEqual(signal.metadata["confirmation_type"], "bullish_confirmation")

    def test_two_bar_false_breakout_medium_news_reduces_position(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 103.0, "high": 103.2, "low": 102.4, "close": 102.6},
                {"open": 102.6, "high": 102.8, "low": 101.8, "close": 102.0},
                {"open": 101.9, "high": 102.1, "low": 101.0, "close": 101.2},
                {"open": 101.1, "high": 101.3, "low": 100.3, "close": 100.6},
                {"open": 100.5, "high": 100.7, "low": 99.2, "close": 99.6},
                {"open": 99.7, "high": 100.6, "low": 99.5, "close": 100.2},
                {"open": 100.2, "high": 101.0, "low": 100.1, "close": 100.8},
                {"open": 100.8, "high": 101.3, "low": 100.5, "close": 101.1},
            ]
        )
        result = detect_two_bar_false_breakout(bars, self.level, news_context={"risk_level": "MEDIUM"})
        signal = detect_false_breakout_two_bar("AAPL", bars, self.level, news_context={"risk_level": "MEDIUM"})

        self.assertEqual(result["signal"], "BUY")
        self.assertEqual(result["position_modifier"], 0.5)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal, "BUY")
        self.assertIn("position_modifier=0.5", signal.notes)

    def test_complex_false_breakout_buy_signal(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 103.2, "high": 103.4, "low": 102.6, "close": 102.8},
                {"open": 102.8, "high": 103.0, "low": 102.0, "close": 102.1},
                {"open": 102.1, "high": 102.3, "low": 101.4, "close": 101.7},
                {"open": 101.7, "high": 101.9, "low": 100.8, "close": 101.0},
                {"open": 100.2, "high": 100.3, "low": 99.5, "close": 99.7},
                {"open": 99.8, "high": 100.0, "low": 99.4, "close": 99.6},
                {"open": 99.7, "high": 99.9, "low": 99.3, "close": 99.55},
                {"open": 99.6, "high": 99.8, "low": 99.25, "close": 99.5},
                {"open": 99.8, "high": 100.5, "low": 99.7, "close": 100.25},
                {"open": 100.3, "high": 101.1, "low": 100.2, "close": 100.9},
            ]
        )
        result = detect_complex_false_breakout(bars, self.level, news_context={"risk_level": "LOW"})
        signal = detect_false_breakout_complex("AAPL", bars, self.level, news_context={"risk_level": "LOW"})

        self.assertEqual(result["signal"], "BUY")
        self.assertIn("clean false breakout return", result["reason"])
        self.assertEqual(result["context"]["pattern"], "COMPLEX")
        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal, "BUY")

    def test_high_news_rejects_signal(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 102.0, "high": 102.2, "low": 101.3, "close": 101.5},
                {"open": 101.5, "high": 101.7, "low": 100.5, "close": 100.8},
                {"open": 100.8, "high": 101.0, "low": 99.55, "close": 100.15},
                {"open": 100.12, "high": 101.05, "low": 100.08, "close": 100.95},
            ]
        )
        result = detect_one_bar_false_breakout(bars, self.level, news_context={"risk_level": "HIGH"})
        signal = detect_false_breakout_one_bar("AAPL", bars, self.level, news_context={"risk_level": "HIGH"})

        self.assertEqual(result["signal"], "NONE")
        self.assertIn("news risk high", result["reason"])
        self.assertEqual(result["context"]["news_risk"], "HIGH")
        self.assertIsNone(signal)
