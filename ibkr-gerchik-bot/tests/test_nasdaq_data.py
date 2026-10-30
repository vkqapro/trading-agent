from __future__ import annotations

import unittest

from src.config import NasdaqDataConfig
from src.data.nasdaq_data import NasdaqDataClient


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _Session:
    def __init__(self, *, get_payloads: list[dict], post_payloads: list[dict] | None = None) -> None:
        self.get_payloads = list(get_payloads)
        self.post_payloads = list(post_payloads or [])
        self.get_calls: list[dict] = []
        self.post_calls: list[dict] = []

    def get(self, url: str, **kwargs) -> _Response:
        self.get_calls.append({"url": url, **kwargs})
        return _Response(self.get_payloads.pop(0))

    def post(self, url: str, **kwargs) -> _Response:
        self.post_calls.append({"url": url, **kwargs})
        return _Response(self.post_payloads.pop(0))


class NasdaqDataClientTests(unittest.TestCase):
    def test_get_daily_bars_parses_data_link_time_series(self) -> None:
        session = _Session(
            get_payloads=[
                {
                    "dataset_data": {
                        "column_names": ["Date", "Open", "High", "Low", "Close", "Volume"],
                        "data": [
                            ["2026-07-12", 10, 11, 9, 10.5, 1000],
                            ["2026-07-11", 9, 10, 8, 9.5, 900],
                        ],
                    }
                }
            ]
        )
        config = NasdaqDataConfig(enabled=True, data_link_api_key="key")
        client = NasdaqDataClient(config, session=session)

        result = client.get_daily_bars("atai", "10 Y")

        self.assertEqual([d.strftime("%Y-%m-%d") for d in result["date"]], ["2026-07-11", "2026-07-12"])
        self.assertEqual(float(result.iloc[-1]["close"]), 10.5)
        self.assertIn("/datasets/EOD/ATAI/data.json", session.get_calls[0]["url"])
        self.assertEqual(session.get_calls[0]["params"]["api_key"], "key")
        self.assertEqual(session.get_calls[0]["params"]["order"], "asc")

    def test_get_intraday_bars_parses_cloud_bars_after_auth(self) -> None:
        session = _Session(
            post_payloads=[{"access_token": "token", "expires_in": 3600}],
            get_payloads=[
                {
                    "data": {
                        "AAPL": [
                            {"t": "2026-07-14T09:30:00-04:00", "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 1500}
                        ]
                    }
                }
            ],
        )
        config = NasdaqDataConfig(
            enabled=True,
            cloud_base_url="https://api.example.test",
            cloud_client_id="client",
            cloud_client_secret="secret",
        )
        client = NasdaqDataClient(config, session=session)

        result = client.get_intraday_bars("AAPL", "5 D", "15 mins")

        self.assertEqual(len(result), 1)
        self.assertEqual(float(result.iloc[0]["close"]), 100.5)
        self.assertEqual(session.post_calls[0]["url"], "https://api.example.test/v1/auth/token")
        self.assertIn("/equities/bars/AAPL/15minute/false/5d", session.get_calls[0]["url"])
        self.assertEqual(session.get_calls[0]["headers"]["Authorization"], "Bearer token")

    def test_get_intraday_bars_ignores_cloud_for_long_backfills(self) -> None:
        session = _Session(get_payloads=[])
        config = NasdaqDataConfig(
            enabled=True,
            cloud_base_url="https://api.example.test",
            cloud_client_id="client",
            cloud_client_secret="secret",
        )
        client = NasdaqDataClient(config, session=session)

        result = client.get_intraday_bars("AAPL", "2 Y", "15 mins")

        self.assertTrue(result.empty)
        self.assertEqual(session.post_calls, [])
        self.assertEqual(session.get_calls, [])


if __name__ == "__main__":
    unittest.main()
