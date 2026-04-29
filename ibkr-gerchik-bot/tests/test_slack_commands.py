"""Tests for safe Slack command parsing."""

import unittest

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


if __name__ == "__main__":
    unittest.main()
