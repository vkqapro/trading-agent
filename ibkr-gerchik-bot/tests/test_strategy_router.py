"""Tests for router integration with false breakout news risk context."""

import unittest
from unittest.mock import patch

import pandas as pd

from src.strategy.levels import Level
from src.strategy.signal_models import TradeSignal
from src.strategy.strategy_router import route_strategies


class StrategyRouterNewsContextTests(unittest.TestCase):
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
            nearest_upper_level=104.0,
            nearest_lower_level=96.0,
            zone_low=99.85,
            zone_high=100.15,
            center=100.0,
            atr_value=1.8,
        )
        self.bars = pd.DataFrame(
            [
                {"open": 103.4, "high": 103.6, "low": 102.9, "close": 103.0},
                {"open": 103.0, "high": 103.1, "low": 102.4, "close": 102.6},
                {"open": 102.0, "high": 102.2, "low": 101.3, "close": 101.5},
                {"open": 101.5, "high": 101.7, "low": 100.5, "close": 100.8},
                {"open": 100.8, "high": 101.0, "low": 99.55, "close": 100.25},
                {"open": 100.12, "high": 101.05, "low": 100.08, "close": 100.95},
            ]
        )

    def test_router_blocks_false_breakout_on_high_news_risk(self) -> None:
        with (
            patch("src.strategy.strategy_router.calculate_stop_loss", side_effect=lambda entry, stop, direction: stop),
            patch("src.strategy.false_breakout_one_bar._persist_daily_decision"),
            patch("src.strategy.false_breakout_two_bar._persist_daily_decision"),
            patch("src.strategy.false_breakout_complex._persist_daily_decision"),
            patch("src.strategy.false_breakout_continuation._persist_daily_decision"),
        ):
            signals = route_strategies("AAPL", self.bars, [self.level], news_context={"risk_level": "HIGH"})
        self.assertFalse(any(signal.strategy.startswith("false_breakout") for signal in signals))

    def test_router_allows_false_breakout_on_medium_news_risk(self) -> None:
        level = Level(
            symbol="AAPL",
            price=100.0,
            type="historical",
            timeframe="daily",
            touches=4,
            false_breakouts=1,
            strength_score=7.5,
            created_by="swing",
            nearest_upper_level=108.0,
            nearest_lower_level=96.0,
            zone_low=99.85,
            zone_high=100.15,
            center=100.0,
            atr_value=1.8,
        )
        medium_bars = pd.DataFrame(
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
        with (
            patch("src.strategy.strategy_router.calculate_stop_loss", side_effect=lambda entry, stop, direction: stop),
            patch("src.strategy.false_breakout_one_bar._persist_daily_decision"),
            patch("src.strategy.false_breakout_two_bar._persist_daily_decision"),
            patch("src.strategy.false_breakout_complex._persist_daily_decision"),
            patch("src.strategy.false_breakout_continuation._persist_daily_decision"),
        ):
            signals = route_strategies("AAPL", medium_bars, [level], news_context={"risk_level": "MEDIUM"})
        self.assertTrue(any(signal.strategy == "false_breakout_two_bar" for signal in signals))

    def test_router_persists_rejection_for_raw_signal_that_cannot_be_ordered(self) -> None:
        raw_signal = TradeSignal(
            symbol="AGPU",
            strategy="false_breakout_continuation",
            signal="BUY",
            direction="long",
            entry=5.02,
            stop=5.01,
            target=4.89,
            level_price=4.43,
            level_type="abnormal_candle",
            nearest_upper_level=4.89,
            nearest_lower_level=4.07,
            notes=[
                "reasons=prior false breakdown continuation",
                "confidence=1.0",
                "position_modifier=0.5",
            ],
        )
        persisted = []

        with (
            patch("src.strategy.strategy_router.detect_rebound", return_value=None),
            patch("src.strategy.strategy_router.detect_breakout", return_value=None),
            patch("src.strategy.strategy_router.detect_false_breakout_one_bar", return_value=None),
            patch("src.strategy.strategy_router.detect_false_breakout_two_bar", return_value=None),
            patch("src.strategy.strategy_router.detect_false_breakout_complex", return_value=None),
            patch("src.strategy.strategy_router.detect_false_breakout_continuation", return_value=raw_signal),
            patch("src.strategy.false_breakout_one_bar._persist_daily_decision", side_effect=lambda *args: persisted.append(args)),
        ):
            signals = route_strategies("AGPU", self.bars, [self.level], news_context={"risk_level": "LOW"})

        self.assertEqual(signals, [])
        self.assertEqual(len(persisted), 1)
        _symbol, _level, strategy_name, result = persisted[0]
        self.assertEqual(strategy_name, "false_breakout_continuation")
        self.assertEqual(result["signal"], "NONE")
        self.assertEqual(result["router_status"], "rejected")
        self.assertEqual(result["raw_signal"], "BUY")
        self.assertIn("router_rejected: stop_too_wide_after_rounding", result["reason"][0])


if __name__ == "__main__":
    unittest.main()
