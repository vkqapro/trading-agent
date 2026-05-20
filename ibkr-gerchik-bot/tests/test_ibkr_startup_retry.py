from __future__ import annotations

import unittest
from unittest.mock import patch

from src.config import SETTINGS
from src.main import _connect_broker_with_startup_retry


class _FlakyBrokerStub:
    attempts = 0
    disconnects = 0

    def __init__(self) -> None:
        self.is_connected = False

    def connect(self) -> None:
        type(self).attempts += 1
        if type(self).attempts < 3:
            raise ConnectionError("IBKR busy")
        self.is_connected = True

    def disconnect(self) -> None:
        type(self).disconnects += 1
        self.is_connected = False


class _AlwaysBusyBrokerStub:
    attempts = 0

    def __init__(self) -> None:
        self.is_connected = False

    def connect(self) -> None:
        type(self).attempts += 1
        raise ConnectionError("IBKR busy")

    def disconnect(self) -> None:
        self.is_connected = False


class IBKRStartupRetryTests(unittest.TestCase):
    def test_intraday_waits_and_retries_startup_connection(self) -> None:
        original_window = SETTINGS.broker.startup_retry_window_seconds
        original_delay = SETTINGS.broker.startup_retry_delay_seconds
        _FlakyBrokerStub.attempts = 0
        _FlakyBrokerStub.disconnects = 0

        try:
            object.__setattr__(SETTINGS.broker, "startup_retry_window_seconds", 60)
            object.__setattr__(SETTINGS.broker, "startup_retry_delay_seconds", 5)
            sleeps: list[float] = []
            with (
                patch("src.main.IBKRClient", _FlakyBrokerStub),
                patch("src.main.time.sleep", lambda seconds: sleeps.append(seconds)),
            ):
                broker = _connect_broker_with_startup_retry("intraday")

            self.assertTrue(broker.is_connected)
            self.assertEqual(_FlakyBrokerStub.attempts, 3)
            self.assertEqual(_FlakyBrokerStub.disconnects, 2)
            self.assertEqual(sleeps, [5, 5])
        finally:
            object.__setattr__(SETTINGS.broker, "startup_retry_window_seconds", original_window)
            object.__setattr__(SETTINGS.broker, "startup_retry_delay_seconds", original_delay)

    def test_non_intraday_jobs_do_not_use_startup_grace_window(self) -> None:
        original_window = SETTINGS.broker.startup_retry_window_seconds
        original_delay = SETTINGS.broker.startup_retry_delay_seconds
        _AlwaysBusyBrokerStub.attempts = 0

        try:
            object.__setattr__(SETTINGS.broker, "startup_retry_window_seconds", 60)
            object.__setattr__(SETTINGS.broker, "startup_retry_delay_seconds", 5)
            with (
                patch("src.main.IBKRClient", _AlwaysBusyBrokerStub),
                patch("src.main.time.sleep", side_effect=AssertionError("should not sleep")),
                self.assertRaises(ConnectionError),
            ):
                _connect_broker_with_startup_retry("premarket")

            self.assertEqual(_AlwaysBusyBrokerStub.attempts, 1)
        finally:
            object.__setattr__(SETTINGS.broker, "startup_retry_window_seconds", original_window)
            object.__setattr__(SETTINGS.broker, "startup_retry_delay_seconds", original_delay)


if __name__ == "__main__":
    unittest.main()
