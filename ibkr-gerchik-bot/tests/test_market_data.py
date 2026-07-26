from __future__ import annotations

import unittest

import pandas as pd

from src.data.market_data import MarketDataService


class _BrokerStub:
    def __init__(self, bars: pd.DataFrame) -> None:
        self._bars = bars
        self.requests: list[dict] = []

    def get_historical_bars(self, **kwargs) -> pd.DataFrame:
        self.requests.append(kwargs)
        return self._bars.copy()


class _QuoteBrokerStub:
    def __init__(self) -> None:
        self.market_data_types: list[int] = []

    def request_market_data_type(self, market_data_type: int) -> None:
        self.market_data_types.append(market_data_type)

    def get_market_price(self, _symbol: str) -> dict[str, object]:
        return {
            "bid": 49.90,
            "ask": 50.10,
            "last": 50.00,
            "quote_status": "ok",
        }


class _DurationBrokerStub:
    def __init__(self, completed: pd.DataFrame, current: pd.DataFrame) -> None:
        self.completed = completed
        self.current = current
        self.requests: list[str] = []

    def get_historical_bars(self, **kwargs) -> pd.DataFrame:
        duration = str(kwargs["duration"])
        self.requests.append(duration)
        return (self.current if duration == "1 D" else self.completed).copy()


class _NasdaqProviderStub:
    def __init__(self, *, daily: pd.DataFrame | None = None, intraday: pd.DataFrame | None = None) -> None:
        self.daily = daily if daily is not None else pd.DataFrame()
        self.intraday = intraday if intraday is not None else pd.DataFrame()
        self.daily_requests: list[dict] = []
        self.intraday_requests: list[dict] = []

    def get_daily_bars(self, symbol: str, duration: str) -> pd.DataFrame:
        self.daily_requests.append({"symbol": symbol, "duration": duration})
        return self.daily.copy()

    def get_intraday_bars(self, symbol: str, *, duration: str, bar_size: str) -> pd.DataFrame:
        self.intraday_requests.append({"symbol": symbol, "duration": duration, "bar_size": bar_size})
        return self.intraday.copy()


class MarketDataServiceTests(unittest.TestCase):
    def test_delayed_fallback_marks_quote_source(self) -> None:
        broker = _QuoteBrokerStub()
        service = MarketDataService(broker)

        service.enable_delayed_fallback()
        quote = service.get_quote("DAL")

        self.assertEqual(broker.market_data_types, [3])
        self.assertEqual(quote["market_data_type"], "delayed")

    def test_get_intraday_bars_sorts_oldest_to_newest(self) -> None:
        bars = pd.DataFrame(
            [
                {"date": "2026-05-19 15:40:00", "open": 5.90, "high": 6.00, "low": 5.80, "close": 5.98, "volume": 1000},
                {"date": "2026-05-19 15:45:00", "open": 4.70, "high": 4.72, "low": 4.55, "close": 4.58, "volume": 1200},
                {"date": "2026-05-19 15:35:00", "open": 6.10, "high": 6.12, "low": 5.88, "close": 5.95, "volume": 900},
            ]
        )
        service = MarketDataService(_BrokerStub(bars))

        result = service.get_intraday_bars("BBBY", duration="2 D", bar_size="5 mins")

        self.assertEqual(list(result["close"]), [5.95, 5.98, 4.58])
        self.assertEqual(float(result.iloc[-1]["close"]), 4.58)

    def test_get_intraday_bars_prefers_nasdaq_provider(self) -> None:
        broker = _BrokerStub(pd.DataFrame())
        nasdaq = _NasdaqProviderStub(
            intraday=pd.DataFrame(
                [
                    {"date": "2026-05-19 09:35:00", "open": 5, "high": 6, "low": 4, "close": 5.5, "volume": 1000},
                ]
            )
        )
        service = MarketDataService(broker, nasdaq_provider=nasdaq)

        result = service.get_intraday_bars("BBBY", duration="5 D", bar_size="5 mins")

        self.assertEqual(float(result.iloc[0]["close"]), 5.5)
        self.assertEqual(broker.requests, [])
        self.assertEqual(nasdaq.intraday_requests[0]["symbol"], "BBBY")

    def test_get_intraday_bars_falls_back_to_broker_when_nasdaq_empty(self) -> None:
        broker = _BrokerStub(
            pd.DataFrame(
                [
                    {"date": "2026-05-19 09:35:00", "open": 5, "high": 6, "low": 4, "close": 5.5, "volume": 1000},
                ]
            )
        )
        service = MarketDataService(broker, nasdaq_provider=_NasdaqProviderStub())

        result = service.get_intraday_bars("BBBY", duration="5 D", bar_size="5 mins")

        self.assertEqual(float(result.iloc[0]["close"]), 5.5)
        self.assertEqual(len(broker.requests), 1)

    def test_get_intraday_bars_stitches_current_partial_session(self) -> None:
        completed = pd.DataFrame([
            {"date": "2026-06-22 15:55:00-04:00", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
        ])
        current = pd.DataFrame([
            {"date": "2026-06-23 09:30:00-04:00", "open": 11, "high": 12, "low": 10, "close": 11.5, "volume": 200},
            {"date": "2026-06-23 09:35:00-04:00", "open": 11.5, "high": 12.5, "low": 11, "close": 12, "volume": 250},
        ])
        broker = _DurationBrokerStub(completed, current)
        service = MarketDataService(broker)

        result = service.get_intraday_bars(
            "AAPL",
            duration="3 D",
            bar_size="5 mins",
            include_current_session=True,
        )

        self.assertEqual(broker.requests, ["3 D", "1 D"])
        self.assertEqual(len(result), 3)
        self.assertEqual(str(result.iloc[-1]["date"]), "2026-06-23 09:35:00-04:00")

    def test_get_daily_bars_sorts_oldest_to_newest(self) -> None:
        bars = pd.DataFrame(
            [
                {"date": "2026-05-06", "open": 79.0, "high": 79.3, "low": 77.5, "close": 78.0, "volume": 100},
                {"date": "2026-05-04", "open": 71.0, "high": 72.0, "low": 70.0, "close": 71.5, "volume": 100},
                {"date": "2026-05-05", "open": 72.0, "high": 74.0, "low": 71.4, "close": 73.0, "volume": 100},
            ]
        )
        service = MarketDataService(_BrokerStub(bars))

        result = service.get_daily_bars("SEI")

        self.assertEqual([d.strftime("%Y-%m-%d") for d in result["date"]], ["2026-05-04", "2026-05-05", "2026-05-06"])

    def test_get_daily_bars_prefers_nasdaq_provider(self) -> None:
        broker = _BrokerStub(pd.DataFrame())
        nasdaq = _NasdaqProviderStub(
            daily=pd.DataFrame(
                [
                    {"date": "2026-05-05", "open": 72.0, "high": 74.0, "low": 71.4, "close": 73.0, "volume": 100},
                ]
            )
        )
        service = MarketDataService(broker, nasdaq_provider=nasdaq)

        result = service.get_daily_bars("SEI", duration="10 Y")

        self.assertEqual(float(result.iloc[0]["close"]), 73.0)
        self.assertEqual(broker.requests, [])
        self.assertEqual(nasdaq.daily_requests[0], {"symbol": "SEI", "duration": "10 Y"})


if __name__ == "__main__":
    unittest.main()
