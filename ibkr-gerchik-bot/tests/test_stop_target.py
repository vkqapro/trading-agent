"""Tests for technical stop and target handling."""

import unittest

from src.risk.stop_loss import calculate_stop_loss
from src.risk.take_profit import calculate_take_profit, reward_risk_ratio


class StopTargetTests(unittest.TestCase):
    def test_confirmed_breakout_can_keep_wider_technical_stop(self) -> None:
        self.assertIsNone(calculate_stop_loss(100.0, 99.0, "long"))
        self.assertEqual(calculate_stop_loss(100.0, 99.0, "long", enforce_max_distance=False), 98.97)

    def test_target_requires_configured_reward_risk(self) -> None:
        self.assertIsNone(calculate_take_profit(100.0, 99.0, 102.5, "long"))
        target = calculate_take_profit(100.0, 99.0, 103.0, "long")
        self.assertEqual(target, 103.0)
        self.assertGreaterEqual(reward_risk_ratio(100.0, 99.0, target), 3.0)

    def test_target_can_fallback_to_three_r_when_next_level_missing(self) -> None:
        target = calculate_take_profit(100.0, 99.0, None, "long", allow_fallback=True)

        self.assertEqual(target, 103.0)


if __name__ == "__main__":
    unittest.main()
