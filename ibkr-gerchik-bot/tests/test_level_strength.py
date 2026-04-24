"""Tests for level strength scoring."""

import unittest

from src.strategy.level_strength import filter_strong_levels, score_level
from src.strategy.levels import Level


class LevelStrengthTests(unittest.TestCase):
    def test_round_number_and_touches_increase_score(self) -> None:
        level = Level("AAPL", 100.0, "historical", "daily", 4, 2, 0.0, "repeated_lows", 105.0, 95.0)
        self.assertGreaterEqual(score_level(level), 4.0)

    def test_filter_strong_levels(self) -> None:
        weak = Level("AAPL", 101.13, "consolidation", "intraday", 1, 0, 0.0, "noise", 102.0, 100.0)
        strong = Level("AAPL", 100.0, "historical", "daily", 4, 1, 0.0, "repeated_lows", 105.0, 95.0)
        filtered = filter_strong_levels([weak, strong])
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].price, 100.0)

