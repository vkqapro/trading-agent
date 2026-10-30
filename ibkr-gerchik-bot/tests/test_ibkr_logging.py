from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from src.brokers.ibkr import IBKRClient


class _FakeEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.handlers = [item for item in self.handlers if item != handler]
        return self

    def emit(self, *args):
        for handler in list(self.handlers):
            handler(*args)


class _FakeIB:
    def __init__(self) -> None:
        self._connected = False
        self.errorEvent = _FakeEvent()

    def isConnected(self) -> bool:
        return self._connected

    def connect(self, host: str, port: int, clientId: int, timeout: int) -> None:
        del host, port, clientId, timeout
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False


class IBKRLoggingTests(unittest.TestCase):
    def test_client_initializes_in_worker_thread_without_event_loop_error(self) -> None:
        with patch("src.brokers.ibkr.IB", _FakeIB):
            with ThreadPoolExecutor(max_workers=1) as executor:
                client = executor.submit(IBKRClient).result()
            self.assertIsInstance(client.ib, _FakeIB)

    def test_subscription_errors_are_logged_and_handler_detaches(self) -> None:
        contract = SimpleNamespace(symbol="MSFT", exchange="SMART", primaryExchange="NASDAQ", currency="USD")

        with (
            patch("src.brokers.ibkr.IB", _FakeIB),
            patch("src.brokers.ibkr.LOGGER") as logger,
        ):
            client = IBKRClient()
            client.connect()
            self.assertEqual(len(client.ib.errorEvent.handlers), 1)

            client.ib.errorEvent.emit(149, 10089, "Requested market data requires additional subscription", contract)

            logger.warning.assert_called_once()
            warning_message = logger.warning.call_args.args[0]
            self.assertIn("code=10089", warning_message)
            self.assertIn("reqId=149", warning_message)
            self.assertIn("MSFT", warning_message)

            client.disconnect()
            self.assertEqual(len(client.ib.errorEvent.handlers), 0)


if __name__ == "__main__":
    unittest.main()
