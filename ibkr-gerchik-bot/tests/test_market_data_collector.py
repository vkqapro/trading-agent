from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from src.jobs.market_data_collector import run_market_data_collector
from src.jobs.session_utils import collect_watchlist_intraday_bars


TZ = ZoneInfo("America/New_York")


class MarketDataCollectorTests(unittest.TestCase):
    def test_collection_persists_symbols_without_trading_checks(self) -> None:
        bars = pd.DataFrame([
            {"date": "2026-06-22 09:30:00-04:00", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
        ])

        class MarketDataStub:
            def get_intraday_bars(self, symbol, **_kwargs):
                return bars.assign(close=10.5 if symbol == "AAPL" else 20.5)

        saved = []
        with patch("src.jobs.session_utils.save_bars", side_effect=lambda symbol, timeframe, frame: saved.append((symbol, timeframe, len(frame))) or object()):
            result = collect_watchlist_intraday_bars(
                MarketDataStub(),
                {"AAPL": {"news_blocked": True}, "MSFT": {"levels": []}},
            )

        self.assertEqual(result["symbols_persisted"], 2)
        self.assertEqual(saved, [("AAPL", "intraday_5m", 1), ("MSFT", "intraday_5m", 1)])

    def test_standalone_collector_can_run_one_cycle(self) -> None:
        class MarketDataStub:
            def market_is_open(self, _now):
                return True

        with patch(
            "src.jobs.market_data_collector.collect_watchlist_intraday_bars",
            return_value={
                "bars_by_symbol": {},
                "symbols_requested": 2,
                "symbols_persisted": 2,
                "failed": [],
            },
        ):
            result = run_market_data_collector(
                MarketDataStub(),
                {"AAPL": {}, "MSFT": {}},
                once=True,
                now_provider=lambda: datetime(2026, 6, 22, 10, 0, tzinfo=TZ),
            )

        self.assertEqual(result["cycles"], 1)
        self.assertEqual(result["symbols_persisted"], 2)


if __name__ == "__main__":
    unittest.main()
