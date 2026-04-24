"""Tests for ATR logic."""

import unittest

import pandas as pd

from src.strategy.atr import calculate_daily_atr, calculate_technical_atr, technical_atr_has_room


class AtrTests(unittest.TestCase):
    def test_daily_atr_excludes_abnormal_candle(self) -> None:
        bars = pd.DataFrame(
            [
                {"high": 110, "low": 100},
                {"high": 109, "low": 101},
                {"high": 111, "low": 100},
                {"high": 130, "low": 90},
                {"high": 112, "low": 103},
            ]
        )
        atr = calculate_daily_atr(bars)
        self.assertLess(atr, 20)

    def test_technical_atr(self) -> None:
        self.assertEqual(calculate_technical_atr(100.0, 95.0, 103.0), 3.0)
        self.assertTrue(technical_atr_has_room(2.0, 100.0))

