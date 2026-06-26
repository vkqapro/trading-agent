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
        with (
            patch("src.jobs.session_utils.ensure_required_chart_history", return_value={"ready": True, "missing": []}),
            patch("src.jobs.session_utils.save_bars", side_effect=lambda symbol, timeframe, frame: saved.append((symbol, timeframe, len(frame))) or object()),
        ):
            result = collect_watchlist_intraday_bars(
                MarketDataStub(),
                {"AAPL": {"news_blocked": True}, "MSFT": {"levels": []}},
            )

        self.assertEqual(result["symbols_persisted"], 2)
        self.assertEqual(saved, [("AAPL", "intraday_5m", 1), ("MSFT", "intraday_5m", 1)])

    def test_collection_reports_previous_session_bars_as_stale_after_open(self) -> None:
        bars = pd.DataFrame([
            {"date": "2026-06-22 15:55:00-04:00", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
        ])

        class MarketDataStub:
            def get_intraday_bars(self, _symbol, **_kwargs):
                return bars

        with (
            patch("src.jobs.session_utils.ensure_required_chart_history", return_value={"ready": True, "missing": []}),
            patch("src.jobs.session_utils.load_bars", return_value=bars),
            patch("src.jobs.session_utils.save_bars", return_value=object()),
        ):
            result = collect_watchlist_intraday_bars(
                MarketDataStub(),
                {"AAPL": {}},
                current_time=datetime(2026, 6, 23, 9, 35, 17, tzinfo=TZ),
            )

        self.assertEqual(result["symbols_persisted"], 1)
        self.assertEqual(result["symbols_stale"], 1)
        self.assertEqual(result["stale"][0]["symbol"], "AAPL")

    def test_collection_blocks_symbol_until_required_chart_history_is_ready(self) -> None:
        class MarketDataStub:
            def get_intraday_bars(self, _symbol, **_kwargs):
                raise AssertionError("5-minute bars should not be requested before required chart history is ready")

        with patch(
            "src.jobs.session_utils.ensure_required_chart_history",
            return_value={"ready": False, "missing": ["weekly", "intraday_4h"]},
        ):
            result = collect_watchlist_intraday_bars(
                MarketDataStub(),
                {"AAPL": {}},
                current_time=datetime(2026, 6, 23, 9, 35, 17, tzinfo=TZ),
            )

        self.assertEqual(result["symbols_persisted"], 0)
        self.assertEqual(result["symbols_chart_history_blocked"], 1)
        self.assertEqual(result["chart_history_blocked"][0]["missing"], ["weekly", "intraday_4h"])

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

    def test_standalone_collector_runs_during_post_close_backfill_window(self) -> None:
        class MarketDataStub:
            pass

        with patch(
            "src.jobs.market_data_collector.collect_watchlist_intraday_bars",
            return_value={
                "bars_by_symbol": {},
                "symbols_requested": 1,
                "symbols_persisted": 1,
                "symbols_advanced": 1,
                "symbols_stale": 0,
                "failed": [],
            },
        ) as collect:
            result = run_market_data_collector(
                MarketDataStub(),
                {"AAPL": {}},
                once=True,
                now_provider=lambda: datetime(2026, 6, 23, 16, 30, tzinfo=TZ),
            )

        self.assertEqual(collect.call_count, 1)
        self.assertEqual(result["cycles"], 1)
        self.assertEqual(result["symbols_persisted"], 1)

    def test_standalone_collector_stops_after_post_close_backfill_window(self) -> None:
        class MarketDataStub:
            pass

        with patch("src.jobs.market_data_collector.collect_watchlist_intraday_bars") as collect:
            result = run_market_data_collector(
                MarketDataStub(),
                {"AAPL": {}},
                once=True,
                now_provider=lambda: datetime(2026, 6, 23, 17, 31, tzinfo=TZ),
            )

        self.assertEqual(collect.call_count, 0)
        self.assertEqual(result["cycles"], 0)

    def test_standalone_collector_reconnects_and_retries_majority_stale_cycle(self) -> None:
        class MarketDataStub:
            def __init__(self):
                self.reconnects = 0

            def market_is_open(self, _now):
                return True

            def reconnect(self):
                self.reconnects += 1

        market_data = MarketDataStub()
        stale_result = {
            "bars_by_symbol": {},
            "symbols_requested": 2,
            "symbols_persisted": 2,
            "symbols_advanced": 0,
            "symbols_stale": 2,
            "stale": [{"symbol": "AAPL"}, {"symbol": "MSFT"}],
            "failed": [],
        }
        fresh_result = {
            "bars_by_symbol": {},
            "symbols_requested": 2,
            "symbols_persisted": 2,
            "symbols_advanced": 2,
            "symbols_stale": 0,
            "stale": [],
            "failed": [],
        }
        with patch(
            "src.jobs.market_data_collector.collect_watchlist_intraday_bars",
            side_effect=[stale_result, fresh_result],
        ) as collect:
            result = run_market_data_collector(
                market_data,
                {"AAPL": {}, "MSFT": {}},
                once=True,
                now_provider=lambda: datetime(2026, 6, 23, 9, 35, 17, tzinfo=TZ),
            )

        self.assertEqual(market_data.reconnects, 1)
        self.assertEqual(collect.call_count, 2)
        self.assertEqual(result["symbols_stale"], 0)
        self.assertEqual(result["symbols_advanced"], 2)


if __name__ == "__main__":
    unittest.main()
