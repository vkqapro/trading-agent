"""Tests for stop and reward/risk logic."""

import unittest

from src.risk.stop_loss import calculate_stop_loss
from src.risk.take_profit import calculate_take_profit, reward_risk_ratio


class StopTakeProfitTests(unittest.TestCase):
    def test_stop_loss_within_limit(self) -> None:
        stop = calculate_stop_loss(100.0, 99.88, "long")
        self.assertIsNotNone(stop)

    def test_reward_risk_validation(self) -> None:
        target = calculate_take_profit(100.0, 99.0, 103.5, "long")
        self.assertEqual(target, 103.5)
        self.assertGreaterEqual(reward_risk_ratio(100.0, 99.0, 103.5), 3.0)

    def test_take_profit_rejects_target_on_wrong_side(self) -> None:
        self.assertIsNone(calculate_take_profit(5.02, 5.01, 4.89, "long"))
        self.assertIsNone(calculate_take_profit(100.0, 101.0, 103.0, "short"))
