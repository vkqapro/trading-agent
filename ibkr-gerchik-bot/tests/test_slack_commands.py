"""Tests for safe Slack command parsing."""

import unittest
from unittest.mock import Mock

from src.alerts.slack import SlackAlerter
from src.slack_commands import SlackCommandProcessor


class SlackCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.processor = SlackCommandProcessor(SlackAlerter())

    def test_parses_prefixed_job_command(self) -> None:
        command = self.processor.parse_command("ibkr rerun premarket")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.kind, "job")
        self.assertEqual(command.job_name, "premarket")

    def test_parses_status_command(self) -> None:
        command = self.processor.parse_command("ibkr status")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.kind, "status")

    def test_ignores_non_prefixed_messages(self) -> None:
        self.assertIsNone(self.processor.parse_command("please rerun premarket"))

    def test_parses_latest_report_command(self) -> None:
        command = self.processor.parse_command("ibkr latest report")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.kind, "latest_report")

    def test_parses_quote_check_command(self) -> None:
        command = self.processor.parse_command("ibkr quote_check --MSFT")
        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.kind, "job")
        self.assertEqual(command.job_name, "quote_check")
        self.assertEqual(command.symbol, "MSFT")

    def test_dispatches_quote_check_with_symbol_and_dry_run_off(self) -> None:
        command = self.processor.parse_command("ibkr quote_check --MSFT")
        assert command is not None

        self.processor.alerter.send_channel_message = Mock()
        callback = Mock(
            return_value={
                "job": "quote_check",
                "symbol": "MSFT",
                "bid": 421.0,
                "ask": 421.52,
                "spread_pct_percent": 0.1234,
                "passes_spread_filter": True,
            }
        )

        self.processor._dispatch(command, callback)

        callback.assert_called_once_with("quote_check", False, {"symbol": "MSFT"})
        self.assertEqual(self.processor.alerter.send_channel_message.call_count, 2)


if __name__ == "__main__":
    unittest.main()
