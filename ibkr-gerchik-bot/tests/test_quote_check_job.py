from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

from src.main import _quote_check_once, _run_connected_job


class _BrokerStub:
    def __init__(self) -> None:
        self.is_connected = False

    def connect(self) -> None:
        self.is_connected = True

    def disconnect(self) -> None:
        self.is_connected = False

    def get_account_summary(self) -> List[Dict[str, object]]:
        return [
            {"tag": "NetLiquidation", "value": 100000.0},
            {"tag": "AvailableFunds", "value": 80000.0},
        ]

    def get_positions(self) -> List[Dict[str, object]]:
        return []

    def get_open_orders(self) -> List[Dict[str, object]]:
        return []


class _MarketDataServiceStub:
    def __init__(self, _broker: object) -> None:
        self.delayed = False

    def enable_delayed_fallback(self) -> None:
        self.delayed = True

    def get_quote(self, symbol: str) -> Dict[str, object]:
        if symbol == "MSFT":
            quote = {"bid": 421.0, "ask": 421.52, "last": 421.3, "close": 420.5}
            return {**quote, "market_data_type": "delayed" if self.delayed else "live"}
        if symbol == "NANQ":
            quote = {"bid": float("nan"), "ask": float("nan"), "last": float("nan"), "close": 0.0}
            return {**quote, "market_data_type": "delayed" if self.delayed else "live"}
        quote = {"bid": 0.0, "ask": 0.0, "last": 0.0, "close": 0.0}
        return {**quote, "market_data_type": "delayed" if self.delayed else "live"}


class _NewsRiskFilterStub:
    def __init__(self, _news_service: object) -> None:
        pass

    def is_macro_risk(self) -> bool:
        return False


class _OrderManagerStub:
    def __init__(
        self,
        broker: object,
        market_data: object,
        alerter: object,
        news_filter: object,
        dry_run: bool = False,
        alert_on_manual_candidates: bool = True,
    ) -> None:
        del broker, market_data, alerter, news_filter, dry_run, alert_on_manual_candidates


class QuoteCheckJobTests(unittest.TestCase):
    def test_quote_check_reports_spread_pass_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            with (
                patch("src.main.IBKRClient", _BrokerStub),
                patch("src.main.NewsService", lambda broker=None: object()),
                patch("src.main.NewsRiskFilter", _NewsRiskFilterStub),
                patch("src.main.MarketDataService", _MarketDataServiceStub),
                patch("src.main.OrderManager", _OrderManagerStub),
            ):
                result = _run_connected_job(
                    "quote_check",
                    state_path=state_path,
                    state={"tracked_positions": []},
                    dry_run=True,
                    command_context={"symbol": "MSFT"},
                )

        self.assertEqual(result["job"], "quote_check")
        self.assertEqual(result["symbol"], "MSFT")
        self.assertEqual(result["spread"], 0.52)
        self.assertAlmostEqual(result["mid"], 421.26, places=6)
        self.assertAlmostEqual(result["spread_pct"], 0.001234, places=6)
        self.assertEqual(result["spread_pct_percent"], 0.1234)
        self.assertTrue(result["passes_spread_filter"])
        self.assertEqual(result["market_data_type"], "delayed")

    def test_quote_check_requires_bid_and_ask_for_spread_pass(self) -> None:
        market_data = _MarketDataServiceStub(object())
        market_data.enable_delayed_fallback()
        market_data.get_quote = lambda _symbol: {
            "bid": 0.0,
            "ask": 0.0,
            "last": 85.08,
            "quote_status": "partial",
            "market_data_type": "delayed",
        }

        result = _quote_check_once("DAL", market_data)

        self.assertEqual(result["last"], 85.08)
        self.assertEqual(result["spread_pct"], 1.0)
        self.assertFalse(result["passes_spread_filter"])

    def test_quote_check_treats_nan_quote_as_failed_spread(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            with (
                patch("src.main.IBKRClient", _BrokerStub),
                patch("src.main.NewsService", lambda broker=None: object()),
                patch("src.main.NewsRiskFilter", _NewsRiskFilterStub),
                patch("src.main.MarketDataService", _MarketDataServiceStub),
                patch("src.main.OrderManager", _OrderManagerStub),
            ):
                result = _run_connected_job(
                    "quote_check",
                    state_path=state_path,
                    state={"tracked_positions": []},
                    dry_run=True,
                    command_context={"symbol": "NANQ"},
                )

        self.assertEqual(result["symbol"], "NANQ")
        self.assertEqual(result["bid"], 0.0)
        self.assertEqual(result["ask"], 0.0)
        self.assertEqual(result["last"], 0.0)
        self.assertEqual(result["spread_pct"], 1.0)
        self.assertFalse(result["passes_spread_filter"])


if __name__ == "__main__":
    unittest.main()
