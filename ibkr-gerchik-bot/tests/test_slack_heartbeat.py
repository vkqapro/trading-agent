from __future__ import annotations

import unittest

from src.alerts.slack import SlackAlerter


class SlackHeartbeatTests(unittest.TestCase):
    def test_intraday_heartbeat_includes_signal_details(self) -> None:
        alerter = SlackAlerter(webhook_url=None)
        messages: list[str] = []
        alerter.send_channel_message = lambda message: messages.append(message) or True  # type: ignore[method-assign]

        sent = alerter.send_intraday_heartbeat(
            {
                "timestamp": "2026-05-20T12:18:19.623452-04:00",
                "tracked_symbols": [],
                "actions": [],
                "macro_risk": False,
                "entries_enabled": True,
                "interval_seconds": 900,
                "symbols_scanned": 67,
                "signals_detected": 1,
                "executed": [],
                "skipped": [{"symbol": "SEI", "reason": "atr_filter"}],
                "manual_candidates": [],
                "skip_reason_summary": {"atr_filter": 1},
                "signal_details": [
                    {
                        "symbol": "SEI",
                        "strategy": "false_breakout_continuation",
                        "signal": "BUY",
                        "entry": 75.83,
                        "signal_level": 71.05,
                        "signal_level_type": "abnormal_candle",
                        "nearest_level": 74.44,
                        "nearest_level_type": "gap",
                        "reward_risk": 2.6,
                        "status": "skipped",
                        "reason": "atr_filter",
                    }
                ],
            }
        )

        self.assertTrue(sent)
        self.assertEqual(len(messages), 1)
        self.assertIn("Signals:", messages[0])
        self.assertIn("SEI: BUY false_breakout_continuation", messages[0])
        self.assertIn("signal level 71.05 abnormal_candle", messages[0])
        self.assertIn("nearest 74.44 gap", messages[0])
        self.assertIn("skipped: atr_filter", messages[0])


if __name__ == "__main__":
    unittest.main()
