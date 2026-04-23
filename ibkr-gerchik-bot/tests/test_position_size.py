"""Tests for position sizing."""

import unittest

from src.risk.position_size import calculate_position_size


class PositionSizeTests(unittest.TestCase):
    def test_calculates_integer_share_size(self) -> None:
        shares = calculate_position_size(100_000, 0.01, 50.0, 49.0)
        self.assertEqual(shares, 1000)

    def test_returns_zero_for_invalid_inputs(self) -> None:
        self.assertEqual(calculate_position_size(100_000, 0.01, 50.0, 50.0), 0)


if __name__ == "__main__":
    unittest.main()
