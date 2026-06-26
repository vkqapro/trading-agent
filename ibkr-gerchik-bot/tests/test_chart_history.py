from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from src.data.chart_history import ChartHistorySpec, ensure_required_chart_history


def _bars(rows: int) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=rows, freq="D")
    return pd.DataFrame({
        "date": dates,
        "open": [10.0] * rows,
        "high": [11.0] * rows,
        "low": [9.0] * rows,
        "close": [10.5] * rows,
        "volume": [100.0] * rows,
    })


class ChartHistoryTests(unittest.TestCase):
    def test_ensure_required_chart_history_fetches_missing_timeframes(self) -> None:
        class MarketDataStub:
            def get_daily_bars(self, symbol, duration=None):
                self.daily = (symbol, duration)
                return _bars(3)

            def get_weekly_bars(self, symbol, duration=None):
                self.weekly = (symbol, duration)
                return _bars(2)

            def get_4h_bars(self, symbol, duration=None):
                self.four_h = (symbol, duration)
                return _bars(4)

        store = {
            ("AAPL", "daily"): _bars(0),
            ("AAPL", "weekly"): _bars(2),
            ("AAPL", "intraday_4h"): _bars(0),
        }

        def load(symbol, timeframe):
            return store.get((symbol, timeframe), _bars(0))

        def save(symbol, timeframe, frame):
            store[(symbol, timeframe)] = frame
            return object()

        specs = (
            ChartHistorySpec("daily", 3, "daily"),
            ChartHistorySpec("weekly", 2, "weekly"),
            ChartHistorySpec("intraday_4h", 4, "4h"),
        )
        market_data = MarketDataStub()
        with (
            patch("src.data.chart_history.load_bars", side_effect=load),
            patch("src.data.chart_history.save_bars", side_effect=save),
        ):
            result = ensure_required_chart_history(market_data, "AAPL", specs=specs)

        self.assertTrue(result["ready"])
        self.assertEqual(result["timeframes"]["weekly"]["source"], "stored")
        self.assertEqual(result["timeframes"]["daily"]["source"], "fetched")
        self.assertEqual(result["timeframes"]["intraday_4h"]["source"], "fetched")

    def test_ensure_required_chart_history_reports_missing_after_failed_fetch(self) -> None:
        class MarketDataStub:
            def get_weekly_bars(self, _symbol, duration=None):
                return _bars(1)

        specs = (ChartHistorySpec("weekly", 2, "weekly"),)
        with (
            patch("src.data.chart_history.load_bars", return_value=_bars(0)),
            patch("src.data.chart_history.save_bars", return_value=object()),
        ):
            result = ensure_required_chart_history(MarketDataStub(), "AAPL", specs=specs)

        self.assertFalse(result["ready"])
        self.assertEqual(result["missing"], ["weekly"])

    def test_existing_ready_but_short_history_is_refreshed(self) -> None:
        class MarketDataStub:
            def get_daily_bars(self, symbol, duration=None):
                self.daily = (symbol, duration)
                return _bars(260)

        store = {("AAPL", "daily"): _bars(60)}

        def load(symbol, timeframe):
            return store.get((symbol, timeframe), _bars(0))

        def save(symbol, timeframe, frame):
            store[(symbol, timeframe)] = frame
            return object()

        specs = (ChartHistorySpec("daily", 60, "daily", 252),)
        market_data = MarketDataStub()
        with (
            patch("src.data.chart_history.load_bars", side_effect=load),
            patch("src.data.chart_history.save_bars", side_effect=save),
        ):
            result = ensure_required_chart_history(market_data, "AAPL", specs=specs)

        self.assertTrue(result["ready"])
        self.assertEqual(result["timeframes"]["daily"]["source"], "fetched")
        self.assertEqual(result["timeframes"]["daily"]["rows"], 260)

    def test_weekly_history_can_be_derived_from_daily_bars(self) -> None:
        class MarketDataStub:
            def get_weekly_bars(self, _symbol, duration=None):
                return _bars(0)

            def get_daily_bars(self, _symbol, duration=None):
                return _bars(80)

        store = {("AAPL", "weekly"): _bars(0), ("AAPL", "daily"): _bars(80)}

        def load(symbol, timeframe):
            return store.get((symbol, timeframe), _bars(0))

        def save(symbol, timeframe, frame):
            store[(symbol, timeframe)] = frame
            return object()

        specs = (ChartHistorySpec("weekly", 12, "weekly", 52),)
        with (
            patch("src.data.chart_history.load_bars", side_effect=load),
            patch("src.data.chart_history.save_bars", side_effect=save),
        ):
            result = ensure_required_chart_history(MarketDataStub(), "AAPL", specs=specs)

        self.assertTrue(result["ready"])
        self.assertGreaterEqual(result["timeframes"]["weekly"]["rows"], 12)


if __name__ == "__main__":
    unittest.main()
