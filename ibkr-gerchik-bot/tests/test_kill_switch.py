"""Tests for kill switch behavior."""

import unittest

from src.risk.kill_switch import should_trigger_kill_switch


class KillSwitchTests(unittest.TestCase):
    def test_kill_switch_triggers_for_disconnection(self) -> None:
        blocked, reasons = should_trigger_kill_switch(
            account_equity=100_000,
            daily_realized_pnl=0.0,
            connection_healthy=False,
            broker_positions=[],
            internal_positions=[],
            macro_risk=False,
            stop_integrity_ok=True,
        )
        self.assertTrue(blocked)
        self.assertIn("ibkr_disconnected", reasons)
