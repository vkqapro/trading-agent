"""Tests for position sizing."""

import unittest

from src.risk.position_size import calculate_position_size, position_value_ok


class PositionSizeTests(unittest.TestCase):
    def test_calculates_integer_share_size(self) -> None:
        shares = calculate_position_size(100_000, 0.01, 50.0, 49.0)
        self.assertEqual(shares, 1000)

    def test_position_value_guard(self) -> None:
        self.assertTrue(position_value_ok(100, 50.0, 10_000))
        self.assertFalse(position_value_ok(1000, 50.0, 10_000))

