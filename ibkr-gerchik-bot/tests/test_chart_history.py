from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from src.data.chart_history import ChartHistorySpec, build_partial_daily_frame, ensure_required_chart_history


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
    def test_partial_daily_frame_replaces_stored_current_day_and_honors_as_of(self) -> None:
        daily = pd.DataFrame([{
            "date": "2026-10-01", "open": 100.0, "high": 101.0,
            "low": 99.0, "close": 100.5, "volume": 1_000,
        }])
        intraday = pd.DataFrame([
            {"date": "2026-10-01T13:30:00Z", "open": 100.0, "high": 102.0, "low": 99.5, "close": 101.5, "volume": 100},
            {"date": "2026-10-01T13:35:00Z", "open": 101.5, "high": 103.0, "low": 101.0, "close": 102.5, "volume": 200},
            {"date": "2026-10-01T13:40:00Z", "open": 102.5, "high": 104.0, "low": 102.0, "close": 103.5, "volume": 300},
        ])
        frame = build_partial_daily_frame(daily, intraday, as_of="2026-10-01T13:36:00Z")
        row = frame.iloc[-1]
        self.assertEqual((row["open"], row["high"], row["low"], row["close"], row["volume"]), (100.0, 103.0, 99.5, 102.5, 300.0))

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
