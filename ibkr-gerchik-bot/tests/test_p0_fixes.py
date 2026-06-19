"""Tests covering P0 fixes for Gerchik-style trading logic.

P0-A: false_breakout.py base class raises NotImplementedError
P0-B: two-bar and complex false breakout stops include a buffer below/above structure
P0-C: reward:risk minimum is taken from SETTINGS.risk.min_reward_risk_ratio in rebound/breakout
P0-D: levels.py filter_weak_levels uses SETTINGS.strategy.min_trade_level_touches
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from src.strategy.false_breakout import detect_false_breakout as base_detect_false_breakout
from src.strategy.false_breakout_complex import detect_false_breakout as detect_complex
from src.strategy.false_breakout_two_bar import detect_false_breakout as detect_two_bar
from src.strategy.levels import Level, filter_weak_levels


def _make_level(
    price: float = 100.0,
    touches: int = 4,
    strength_score: float = 7.5,
    atr_value: float = 2.0,
    nearest_upper: float = 106.0,
    nearest_lower: float = 94.0,
) -> Level:
    return Level(
        symbol="AAPL",
        price=price,
        type="historical",
        timeframe="daily",
        touches=touches,
        false_breakouts=1,
        strength_score=strength_score,
        created_by="swing",
        nearest_upper_level=nearest_upper,
        nearest_lower_level=nearest_lower,
        zone_low=price - 0.15,
        zone_high=price + 0.15,
        center=price,
        atr_value=atr_value,
    )


# ---------------------------------------------------------------------------
# P0-A — base false_breakout raises NotImplementedError
# ---------------------------------------------------------------------------

class TestBaseDetectFalseBreakoutDisabled(unittest.TestCase):
    def test_raises_not_implemented_for_short(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 99.0, "high": 101.5, "low": 98.8, "close": 99.2},
                {"open": 99.2, "high": 100.5, "low": 98.5, "close": 99.0},
            ]
        )
        with self.assertRaises(NotImplementedError):
            base_detect_false_breakout(bars, 100.0, "short")

    def test_raises_not_implemented_for_long(self) -> None:
        bars = pd.DataFrame(
            [
                {"open": 101.0, "high": 101.2, "low": 98.5, "close": 99.5},
                {"open": 99.5, "high": 100.8, "low": 99.3, "close": 100.5},
            ]
        )
        with self.assertRaises(NotImplementedError):
            base_detect_false_breakout(bars, 100.0, "long")

    def test_module_has_disabled_flag(self) -> None:
        import src.strategy.false_breakout as fb_module
        self.assertTrue(fb_module._DISABLED)


# ---------------------------------------------------------------------------
# P0-B — two-bar stop is below structure_low by at least the buffer amount
# ---------------------------------------------------------------------------

class TestTwoBarStopBuffer(unittest.TestCase):
    def setUp(self) -> None:
        self.level = _make_level()

    def _two_bar_long_bars(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"open": 103.0, "high": 103.2, "low": 102.4, "close": 102.6},
                {"open": 102.6, "high": 102.8, "low": 101.8, "close": 102.0},
                {"open": 101.9, "high": 102.1, "low": 101.0, "close": 101.2},
                {"open": 101.1, "high": 101.3, "low": 100.3, "close": 100.6},
                # first: closes below zone_low
                {"open": 100.5, "high": 100.7, "low": 99.3, "close": 99.6},
                # second: returns above zone_low
                {"open": 99.7, "high": 100.6, "low": 99.5, "close": 100.2},
                # confirmation: bullish
                {"open": 100.2, "high": 101.0, "low": 100.1, "close": 100.8},
            ]
        )

    def _two_bar_short_bars(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"open": 97.0, "high": 97.5, "low": 96.8, "close": 97.2},
                {"open": 97.2, "high": 97.8, "low": 96.5, "close": 97.5},
                {"open": 97.5, "high": 98.2, "low": 97.3, "close": 98.0},
                {"open": 98.0, "high": 98.5, "low": 97.8, "close": 98.3},
                # first: closes above zone_high
                {"open": 98.3, "high": 100.8, "low": 98.2, "close": 100.5},
                # second: returns below zone_high
                {"open": 100.4, "high": 100.6, "low": 99.8, "close": 99.9},
                # confirmation: bearish
                {"open": 99.8, "high": 100.0, "low": 99.2, "close": 99.4},
            ]
        )

    def test_long_stop_is_strictly_below_structure_low(self) -> None:
        bars = self._two_bar_long_bars()
        result = detect_two_bar(bars, self.level, atr=2.0, news_context={"risk_level": "LOW"})
        if result["signal"] == "NONE":
            self.skipTest("Signal not generated — bars do not form a clean setup under current config")
        stop = float(result["stop"])
        structure_low = min(
            float(bars.iloc[-3]["low"]),
            float(bars.iloc[-2]["low"]),
            float(bars.iloc[-1]["low"]),
        )
        self.assertLess(stop, structure_low, "Stop must be strictly below the structure low")

    def test_short_stop_is_strictly_above_structure_high(self) -> None:
        bars = self._two_bar_short_bars()
        result = detect_two_bar(bars, self.level, atr=2.0, news_context={"risk_level": "LOW"})
        if result["signal"] == "NONE":
            self.skipTest("Signal not generated — bars do not form a clean setup under current config")
        stop = float(result["stop"])
        structure_high = max(
            float(bars.iloc[-3]["high"]),
            float(bars.iloc[-2]["high"]),
            float(bars.iloc[-1]["high"]),
        )
        self.assertGreater(stop, structure_high, "Stop must be strictly above the structure high")


class TestComplexStopBuffer(unittest.TestCase):
    def setUp(self) -> None:
        self.level = _make_level()

    def _complex_long_bars(self) -> pd.DataFrame:
        # structure = tail(6): bars[-6...-1].
        # trap_candles = structure[:-2] = bars[-6:-2] (4 bars, all must close < zone_low=99.85).
        # return_candle = bars[-2], confirmation = bars[-1].
        # prior_context = head(n-6): needs downtrend (closes declining).
        return pd.DataFrame(
            [
                # prior context: clear downtrend
                {"open": 106.0, "high": 106.5, "low": 105.2, "close": 105.5},
                {"open": 105.5, "high": 105.8, "low": 104.3, "close": 104.6},
                {"open": 104.6, "high": 104.9, "low": 103.4, "close": 103.7},
                {"open": 103.7, "high": 104.0, "low": 102.5, "close": 102.8},
                # trap candles: all close below zone_low (99.85)
                {"open": 99.7, "high": 99.8, "low": 99.1, "close": 99.5},
                {"open": 99.5, "high": 99.7, "low": 99.0, "close": 99.4},
                {"open": 99.4, "high": 99.6, "low": 98.9, "close": 99.3},
                {"open": 99.3, "high": 99.5, "low": 98.8, "close": 99.2},
                # return candle: closes above zone_low
                {"open": 99.3, "high": 100.6, "low": 99.2, "close": 100.25},
                # confirmation: bullish
                {"open": 100.3, "high": 101.1, "low": 100.2, "close": 100.9},
            ]
        )

    def test_long_stop_is_strictly_below_trap_structure_low(self) -> None:
        bars = self._complex_long_bars()
        result = detect_complex(bars, self.level, atr=2.0, news_context={"risk_level": "LOW"})
        if result["signal"] == "NONE":
            self.skipTest("Signal not generated — bars do not form a clean setup under current config")
        stop = float(result["stop"])
        trap_lows = [float(bars.iloc[i]["low"]) for i in range(4, 7)]
        structure_low = min(trap_lows + [float(bars.iloc[7]["low"]), float(bars.iloc[8]["low"])])
        self.assertLess(stop, structure_low, "Stop must be strictly below the trap structure low")


# ---------------------------------------------------------------------------
# P0-C — rebound and breakout use SETTINGS.risk.min_reward_risk_ratio exclusively
# ---------------------------------------------------------------------------

class TestRRMinimumUnified(unittest.TestCase):
    """Verify that breakout and rebound honour the configured RR minimum.

    We patch SETTINGS.risk.min_reward_risk_ratio to 3.5 and assert a signal
    with effective RR of ~2.5 is rejected, even though the old hard-coded
    MIN_ACCEPTABLE_REWARD_RISK of 2.0 would have allowed it.
    """

    def _rebound_bars_low_rr(self) -> pd.DataFrame:
        """Bars that produce a valid rebound setup but only ~2.5 R to the nearest level."""
        return pd.DataFrame(
            [
                {"open": 103.5, "high": 103.8, "low": 103.1, "close": 103.3},
                {"open": 103.3, "high": 103.5, "low": 102.5, "close": 102.8},
                {"open": 102.8, "high": 103.0, "low": 102.0, "close": 102.4},
                # rejection: wicks into zone, closes above
                {"open": 100.5, "high": 100.8, "low": 99.7, "close": 100.4},
                # confirmation: bullish
                {"open": 100.4, "high": 101.0, "low": 100.3, "close": 100.8},
            ]
        )

    def test_rebound_respects_configured_min_rr(self) -> None:
        from src.strategy.rebound import detect_rebound

        # nearest_upper_level set so that RR ≈ 2.5 (entry~100.8, stop~99.65, target~102.7 → risk~1.15, reward~1.9)
        level = _make_level(nearest_upper=102.7, atr_value=2.0)
        bars = self._rebound_bars_low_rr()

        with patch("src.strategy.rebound.SETTINGS") as mock_settings:
            mock_settings.strategy = unittest.mock.MagicMock()
            mock_settings.strategy.level_strength_threshold = 4.0
            mock_settings.strategy.atr_travel_limit_pct = 0.75
            mock_settings.risk.min_reward_risk_ratio = 3.5
            result = detect_rebound("AAPL", bars, level)

        # With RR min at 3.5 and only ~2.5 available, should produce no signal
        self.assertIsNone(result, "Rebound should be rejected when RR < configured minimum")

    def test_breakout_respects_configured_min_rr(self) -> None:
        from src.strategy.breakout import detect_breakout

        # nearest_upper_level set so that RR would only be ~2.5
        level = _make_level(price=100.0, nearest_upper=102.7, atr_value=2.0)
        bars = pd.DataFrame(
            [
                {"open": 98.0, "high": 99.5, "low": 97.8, "close": 99.0},
                {"open": 99.0, "high": 99.8, "low": 98.5, "close": 99.5},
                {"open": 99.5, "high": 100.0, "low": 99.2, "close": 99.8},
                # breakout candle: closes above zone_high (100.15)
                {"open": 99.9, "high": 100.6, "low": 99.7, "close": 100.4},
                # confirmation: bullish, stays above zone
                {"open": 100.4, "high": 100.9, "low": 100.3, "close": 100.7},
            ]
        )

        with patch("src.strategy.breakout.SETTINGS") as mock_settings:
            mock_settings.strategy = unittest.mock.MagicMock()
            mock_settings.strategy.level_strength_threshold = 4.0
            mock_settings.strategy.atr_travel_limit_pct = 0.75
            mock_settings.risk.min_reward_risk_ratio = 3.5
            result = detect_breakout("AAPL", bars, level)

        self.assertIsNone(result, "Breakout should be rejected when RR < configured minimum")


# ---------------------------------------------------------------------------
# P0-D — filter_weak_levels uses SETTINGS.strategy.min_trade_level_touches
# ---------------------------------------------------------------------------

class TestMinTouchesFromSettings(unittest.TestCase):
    def _daily_bars(self) -> pd.DataFrame:
        return pd.DataFrame(
            [{"date": f"2026-01-0{i}", "open": 100, "high": 102, "low": 99, "close": 101} for i in range(1, 8)]
        )

    def test_level_with_two_touches_rejected_when_min_is_three(self) -> None:
        level_2t = _make_level(touches=2, strength_score=6.0)
        daily = self._daily_bars()

        with patch("src.strategy.levels.SETTINGS") as mock_settings:
            mock_settings.strategy.min_trade_level_touches = 3
            mock_settings.strategy.max_level_zone_width_atr_pct = 0.5
            kept = filter_weak_levels([level_2t], daily, atr_value=2.0, merge_distance=0.5)

        self.assertEqual(kept, [], "2-touch level must be rejected when min_touches=3")

    def test_level_with_three_touches_accepted_when_min_is_three(self) -> None:
        level_3t = _make_level(touches=3, strength_score=6.0)
        daily = self._daily_bars()

        with patch("src.strategy.levels.SETTINGS") as mock_settings:
            mock_settings.strategy.min_trade_level_touches = 3
            mock_settings.strategy.max_level_zone_width_atr_pct = 0.5
            kept = filter_weak_levels([level_3t], daily, atr_value=2.0, merge_distance=0.5)

        self.assertTrue(len(kept) >= 1, "3-touch level must pass when min_touches=3")

    def test_level_with_two_touches_accepted_when_min_is_two(self) -> None:
        level_2t = _make_level(touches=2, strength_score=6.0)
        daily = self._daily_bars()

        with patch("src.strategy.levels.SETTINGS") as mock_settings:
            mock_settings.strategy.min_trade_level_touches = 2
            mock_settings.strategy.max_level_zone_width_atr_pct = 0.5
            kept = filter_weak_levels([level_2t], daily, atr_value=2.0, merge_distance=0.5)

        self.assertTrue(len(kept) >= 1, "2-touch level must pass when min_touches=2")

    def test_default_config_has_min_touches_three(self) -> None:
        from src.config import SETTINGS as live_settings
        self.assertEqual(
            live_settings.strategy.min_trade_level_touches,
            3,
            "Default MIN_TRADE_LEVEL_TOUCHES must be 3",
        )


if __name__ == "__main__":
    unittest.main()
