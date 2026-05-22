"""Tests for universal trade validation guardrails."""

import unittest

from src.strategy.validator import validate_trade


class ValidatorTests(unittest.TestCase):
    def _base_signal(self) -> dict[str, object]:
        return {
            "symbol": "AAPL",
            "signal": "BUY",
            "direction": "long",
            "entry": 100.0,
            "stop": 99.0,
            "target": 103.0,
            "quantity": 10,
            "risk_amount": 10.0,
            "risk_per_share": 1.0,
            "level_strength": 5.0,
            "atr_used": 0.2,
            "next_major_level": 103.0,
        }

    def _validate(self, signal: dict[str, object]) -> tuple[bool, list[str]]:
        return validate_trade(
            signal,
            account_equity=10000.0,
            current_positions=[],
            open_risk_amount=0.0,
            spread_pct=0.001,
            paper_trading=True,
            tws_connected=True,
            account_synced=True,
            market_open=True,
            first_unstable_minutes=False,
            allow_first_unstable_minutes=False,
            cash_available=10000.0,
        )

    def test_accepts_complete_valid_signal(self) -> None:
        valid, reasons = self._validate(self._base_signal())

        self.assertTrue(valid)
        self.assertEqual(reasons, [])

    def test_rejects_missing_signal_fields(self) -> None:
        signal = self._base_signal()
        signal.update({"signal": "NONE", "entry": None, "stop": None, "target": None, "risk_per_share": 0.0})

        valid, reasons = self._validate(signal)

        self.assertFalse(valid)
        self.assertIn("missing_signal", reasons)
        self.assertIn("missing_entry", reasons)
        self.assertIn("missing_stop_loss", reasons)
        self.assertIn("missing_target", reasons)
        self.assertIn("invalid_risk_per_share", reasons)

    def test_rejects_atr_travel_and_weak_level(self) -> None:
        signal = self._base_signal()
        signal.update({"atr_used": 0.8, "level_strength": 3.0})

        valid, reasons = self._validate(signal)

        self.assertFalse(valid)
        self.assertIn("atr_travel_exhausted", reasons)
        self.assertIn("level_strength_too_low", reasons)


if __name__ == "__main__":
    unittest.main()
