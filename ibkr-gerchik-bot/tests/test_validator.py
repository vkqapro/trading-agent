"""Tests for trade validation."""

import unittest

from src.strategy.validator import validate_trade


class ValidatorTests(unittest.TestCase):
    def test_accepts_valid_trade(self) -> None:
        signal = {
            "symbol": "AAPL",
            "entry": 100.0,
            "stop_loss": 99.0,
            "target": 102.5,
            "risk_amount": 900.0,
        }
        valid, reasons = validate_trade(signal, 100_000, [], 0.0, 0.001)
        self.assertTrue(valid)
        self.assertEqual(reasons, [])

    def test_rejects_duplicate_position(self) -> None:
        signal = {
            "symbol": "AAPL",
            "entry": 100.0,
            "stop_loss": 99.0,
            "target": 102.5,
            "risk_amount": 900.0,
        }
        valid, reasons = validate_trade(signal, 100_000, [{"symbol": "AAPL"}], 0.0, 0.001)
        self.assertFalse(valid)
        self.assertIn("duplicate_position", reasons)


if __name__ == "__main__":
    unittest.main()
