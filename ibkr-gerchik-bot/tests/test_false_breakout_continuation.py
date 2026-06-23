"""Tests for false-breakout continuation entries."""

import unittest
from unittest.mock import patch

import pandas as pd

from src.config import SETTINGS
from src.strategy.false_breakout_continuation import detect_false_breakout_continuation
from src.strategy.levels import Level
from src.strategy.strategy_router import route_strategies


class FalseBreakoutContinuationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.level = Level(
            symbol="SEI",
            price=100.0,
            type="historical",
            timeframe="daily",
            touches=5,
            false_breakouts=1,
            strength_score=12.0,
            created_by="support",
            nearest_upper_level=104.0,
            nearest_lower_level=96.0,
            zone_low=99.8,
            zone_high=100.2,
            center=100.0,
            atr_value=2.0,
        )

    def test_detects_prior_false_breakdown_continuation_long(self) -> None:
        bars = pd.DataFrame(
            [
                {"date": "2026-05-19 09:35", "open": 101.0, "high": 101.2, "low": 100.4, "close": 100.7},
                {"date": "2026-05-19 10:10", "open": 100.7, "high": 100.8, "low": 99.4, "close": 100.3},
                {"date": "2026-05-19 15:55", "open": 100.3, "high": 100.9, "low": 100.2, "close": 100.6},
                {"date": "2026-05-20 09:35", "open": 100.5, "high": 100.9, "low": 100.25, "close": 100.8},
                {"date": "2026-05-20 09:40", "open": 100.8, "high": 101.2, "low": 100.7, "close": 101.0},
                {"date": "2026-05-20 09:45", "open": 101.0, "high": 101.4, "low": 100.95, "close": 101.3},
            ]
        )

        signal = detect_false_breakout_continuation("SEI", bars, self.level, news_context={"risk_level": "LOW"})

        self.assertIsNotNone(signal)
        self.assertEqual(signal.signal, "BUY")
        self.assertEqual(signal.strategy, "false_breakout_continuation")
        self.assertEqual(signal.target, 104.0)

    def test_router_includes_false_breakout_continuation(self) -> None:
        bars = pd.DataFrame(
            [
                {"date": "2026-05-19 09:35", "open": 101.0, "high": 101.2, "low": 100.4, "close": 100.7},
                {"date": "2026-05-19 10:10", "open": 100.7, "high": 100.8, "low": 99.4, "close": 100.3},
                {"date": "2026-05-19 15:55", "open": 100.3, "high": 100.9, "low": 100.2, "close": 100.6},
                {"date": "2026-05-20 09:35", "open": 100.5, "high": 100.9, "low": 100.25, "close": 100.8},
                {"date": "2026-05-20 09:40", "open": 100.8, "high": 101.2, "low": 100.7, "close": 101.0},
                {"date": "2026-05-20 09:45", "open": 101.0, "high": 101.4, "low": 100.95, "close": 101.3},
            ]
        )

        original_enabled = SETTINGS.strategy.enabled_strategies
        with (
            patch("src.strategy.strategy_router.detect_rebound", return_value=None),
            patch("src.strategy.strategy_router.detect_breakout", return_value=None),
            patch("src.strategy.strategy_router.detect_false_breakout_one_bar", return_value=None),
            patch("src.strategy.strategy_router.detect_false_breakout_two_bar", return_value=None),
            patch("src.strategy.strategy_router.detect_false_breakout_complex", return_value=None),
            patch("src.strategy.decision_log.persist"),
        ):
            object.__setattr__(
                SETTINGS.strategy,
                "enabled_strategies",
                ("rebound", "confirmed_breakout", "false_breakout_one_bar", "false_breakout_continuation"),
            )
            try:
                signals = route_strategies("SEI", bars, [self.level], news_context={"risk_level": "LOW"})
            finally:
                object.__setattr__(SETTINGS.strategy, "enabled_strategies", original_enabled)

        self.assertTrue(any(signal.strategy == "false_breakout_continuation" for signal in signals))

    def test_rejects_without_prior_false_breakdown(self) -> None:
        bars = pd.DataFrame(
            [
                {"date": "2026-05-19 09:35", "open": 101.0, "high": 101.2, "low": 100.4, "close": 100.7},
                {"date": "2026-05-19 15:55", "open": 100.7, "high": 101.0, "low": 100.4, "close": 100.8},
                {"date": "2026-05-20 09:35", "open": 100.8, "high": 101.1, "low": 100.7, "close": 101.0},
                {"date": "2026-05-20 09:40", "open": 101.0, "high": 101.4, "low": 100.9, "close": 101.3},
                {"date": "2026-05-20 09:45", "open": 101.3, "high": 101.6, "low": 101.2, "close": 101.5},
            ]
        )

        signal = detect_false_breakout_continuation("SEI", bars, self.level, news_context={"risk_level": "LOW"})

        self.assertIsNone(signal)


if __name__ == "__main__":
    unittest.main()
