from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.alerts.slack import SlackAlerter


class SlackHeartbeatTests(unittest.TestCase):
    def test_automated_report_is_suppressed_when_disabled(self) -> None:
        alerter = SlackAlerter(webhook_url=None)
        alerter.send_channel_message = Mock(return_value=True)  # type: ignore[method-assign]

        with patch.object(SlackAlerter, "_automated_reports_enabled", return_value=False):
            sent = alerter.send_automated_report("scheduled report")

        self.assertFalse(sent)
        alerter.send_channel_message.assert_not_called()

    def test_automated_report_uses_existing_transport_when_enabled(self) -> None:
        alerter = SlackAlerter(webhook_url=None)
        alerter.send_channel_message = Mock(return_value=True)  # type: ignore[method-assign]

        with patch.object(SlackAlerter, "_automated_reports_enabled", return_value=True):
            sent = alerter.send_automated_report("scheduled report")

        self.assertTrue(sent)
        alerter.send_channel_message.assert_called_once_with("scheduled report")

    def test_automated_file_upload_is_suppressed_before_transport(self) -> None:
        alerter = SlackAlerter(webhook_url=None)
        alerter.bot_client = Mock()  # type: ignore[assignment]
        alerter.send_channel_message = Mock(return_value=True)  # type: ignore[method-assign]

        with patch.object(SlackAlerter, "_automated_reports_enabled", return_value=False):
            sent = alerter.upload_file(
                Path("does-not-exist.xlsx"),
                title="Scheduled report",
                automated_report=True,
            )

        self.assertFalse(sent)
        alerter.bot_client.files_upload_v2.assert_not_called()

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
                "skipped": [{"symbol": "SEI", "reason": "technical_atr_too_low"}],
                "manual_candidates": [],
                "skip_reason_summary": {"technical_atr_too_low": 1},
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
                        "reason": "technical_atr_too_low actual=1.35 required=3.03 actual_pct=0.45% min_pct=1.00%",
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
        self.assertIn("skipped: technical_atr_too_low", messages[0])
        self.assertIn("Skipped: technical_atr_too_low=1", messages[0])


if __name__ == "__main__":
    unittest.main()
