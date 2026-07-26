from __future__ import annotations

import unittest

from src.jobs.inefficiency_reclaim import notify_inefficiency_reclaim_scan


class AlerterStub:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)

    def send_error(self, message: str) -> None:
        self.messages.append(message)


class IRSNotificationTests(unittest.TestCase):
    def test_armed_and_eod_alerts_are_paper_and_use_order_plan_values(self) -> None:
        alerter = AlerterStub()
        result = {
            "newly_armed": [
                {
                    "ticker": "TEST",
                    "direction": "IRS_LONG",
                    "score": 91,
                    "planned_entry": 101,
                    "entry_limit": 101.05,
                    "planned_stop": 99,
                    "planned_target": 104,
                    "structural_R": 1.4,
                    "position_size": 10,
                    "signal_id": "signal-1",
                }
            ],
            "universe_size": 1,
            "signals_found": 1,
            "rejected": [],
        }
        notify_inefficiency_reclaim_scan(
            result, mode="EOD_REPORT", alerter=alerter
        )
        self.assertEqual(len(alerter.messages), 2)
        self.assertTrue(all("PAPER" in message for message in alerter.messages))
        self.assertIn("entry=101/101.05", alerter.messages[0])
        self.assertIn("stop=99", alerter.messages[0])
        self.assertIn("target=104", alerter.messages[0])

    def test_premarket_unknown_news_emits_one_fail_closed_alert(self) -> None:
        alerter = AlerterStub()
        notify_inefficiency_reclaim_scan(
            {
                "newly_armed": [],
                "reason_counts": {"NEWS_STATUS_UNAVAILABLE": 3},
            },
            mode="PREMARKET_CONTEXT",
            alerter=alerter,
        )
        self.assertEqual(len(alerter.messages), 1)
        self.assertIn("PAPER", alerter.messages[0])
        self.assertIn("entries remain blocked", alerter.messages[0])


if __name__ == "__main__":
    unittest.main()
