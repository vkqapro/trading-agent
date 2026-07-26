import unittest
from unittest.mock import patch

import pandas as pd

from src.data.inefficiency_reclaim_history import ensure_irs_history


def _empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])


def _daily_bars(count: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=count, freq="D"),
            "open": [10.0] * count,
            "high": [11.0] * count,
            "low": [9.0] * count,
            "close": [10.5] * count,
            "volume": [100_000] * count,
        }
    )


class FakeMarketData:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_bars(self, symbol, *, duration, bar_size, include_current_session=False):
        self.calls.append(
            {
                "symbol": symbol,
                "duration": duration,
                "bar_size": bar_size,
                "include_current_session": include_current_session,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class IRSHistoryHydrationTests(unittest.TestCase):
    def test_empty_required_full_daily_history_skips_remaining_timeframes(self) -> None:
        market_data = FakeMarketData([_empty_bars()])

        with (
            patch("src.data.inefficiency_reclaim_history.load_bars", return_value=_empty_bars()),
            patch("src.data.inefficiency_reclaim_history.clock.sleep"),
        ):
            result = ensure_irs_history(market_data, "BRKH")

        self.assertEqual(len(market_data.calls), 1)
        self.assertFalse(result["paper_ready"])
        self.assertEqual(
            result["timeframes"]["intraday_1h"]["error"],
            "INSUFFICIENT_DAILY_HISTORY_AFTER_FULL_FETCH",
        )
        self.assertTrue(result["timeframes"]["intraday_15m"]["skipped"])

    def test_partial_daily_history_after_full_fetch_skips_remaining_timeframes(self) -> None:
        existing_daily = _daily_bars(42)
        market_data = FakeMarketData([_empty_bars()])

        def load(symbol, timeframe):
            del symbol
            return existing_daily if timeframe == "daily" else _empty_bars()

        with (
            patch("src.data.inefficiency_reclaim_history.load_bars", side_effect=load),
            patch("src.data.inefficiency_reclaim_history.clock.sleep"),
        ):
            result = ensure_irs_history(market_data, "BRKH")

        self.assertEqual(len(market_data.calls), 1)
        self.assertEqual(result["timeframes"]["daily"]["rows"], 42)
        self.assertEqual(
            result["timeframes"]["intraday_1h"]["error"],
            "INSUFFICIENT_DAILY_HISTORY_AFTER_FULL_FETCH",
        )
        self.assertTrue(result["timeframes"]["intraday_5m"]["skipped"])

    def test_unknown_contract_error_skips_remaining_timeframes(self) -> None:
        market_data = FakeMarketData(
            [Exception("No security definition has been found for the request")]
        )

        with (
            patch("src.data.inefficiency_reclaim_history.load_bars", return_value=_empty_bars()),
            patch("src.data.inefficiency_reclaim_history.clock.sleep"),
        ):
            result = ensure_irs_history(market_data, "BTM")

        self.assertEqual(len(market_data.calls), 1)
        self.assertIn(
            "TERMINAL_IBKR_HISTORY_ERROR",
            result["timeframes"]["intraday_1h"]["error"],
        )
        self.assertTrue(result["timeframes"]["intraday_1m"]["skipped"])

    def test_temporary_timeout_does_not_skip_symbol_immediately(self) -> None:
        market_data = FakeMarketData(
            [
                TimeoutError("API historical data query cancelled"),
                _empty_bars(),
            ]
        )

        with (
            patch("src.data.inefficiency_reclaim_history.load_bars", return_value=_empty_bars()),
            patch("src.data.inefficiency_reclaim_history.clock.sleep"),
        ):
            result = ensure_irs_history(market_data, "BIDU")

        self.assertEqual(len(market_data.calls), 2)
        self.assertIn("daily", result["timeframes"])
        self.assertEqual([call["bar_size"] for call in market_data.calls], ["1 day", "1 day"])
        self.assertTrue(result["timeframes"]["intraday_1h"]["skipped"])
        self.assertNotIn("skipped", result["timeframes"]["daily"])


if __name__ == "__main__":
    unittest.main()
