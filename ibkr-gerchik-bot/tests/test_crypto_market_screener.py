from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
from starlette.requests import Request

from dashboard_react import server
from dashboard_react.market_screener import ScreenerParams, _add_metrics, _detect_signals, _passes_filters


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [],
            "client": ("test", 123),
            "server": ("testserver", 80),
        }
    )


def _strategy_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = _add_metrics(pd.DataFrame(rows))
    frame["atr_clean_14"] = 1.0
    frame["median_volume_20"] = 1_000_000
    frame["avg_volume_20"] = 1_000_000
    frame["median_dollar_volume_20"] = 50_000_000
    return frame


class CryptoMarketScreenerTests(unittest.TestCase):
    def test_stock_route_keeps_stock_universe_and_stock_sizing_defaults(self) -> None:
        result = {"asset": "stock", "signals": [], "universe_size": 2}
        with (
            patch("src.symbol_universe.load_stock_symbols", return_value=["AAPL"]),
            patch.object(server.da, "load_watchlist", return_value={"MSFT": {}}),
            patch.object(server, "run_market_screener", return_value=result) as scanner,
        ):
            payload = server.api_market_screener(_request("/api/market-screener"))

        self.assertEqual(payload, result)
        self.assertEqual(scanner.call_args.kwargs["symbols"], ["AAPL", "MSFT"])
        self.assertEqual(scanner.call_args.kwargs["params"].asset_class, "stock")

    def test_crypto_endpoint_uses_configured_okx_universe_and_shared_scanner(self) -> None:
        result = {"asset": "crypto", "signals": [], "universe_size": 2}
        with (
            patch.object(
                server.da,
                "load_crypto_symbols",
                return_value=[{"inst_id": "BTC-USDT"}, {"inst_id": "ETH-USDT"}],
            ),
            patch.object(
                server.da,
                "load_crypto_dashboard_state",
                return_value={"watchlist": {"BTC-USDT": {"status": "active"}}},
            ),
            patch.object(server, "run_market_screener", return_value=result) as scanner,
        ):
            payload = server.api_market_screener(_request("/api/crypto/market-screener"))

        self.assertEqual(payload, result)
        self.assertEqual(scanner.call_args.kwargs["symbols"], ["BTC-USDT", "ETH-USDT"])
        self.assertEqual(scanner.call_args.kwargs["params"].asset_class, "crypto")
        loaded = scanner.call_args.kwargs["bars_loader"]("BTC-USDT", "1D")
        self.assertIsInstance(loaded, pd.DataFrame)

    def test_crypto_filter_accepts_high_price_assets_and_uses_quote_liquidity(self) -> None:
        rows = [
            {
                "date": date,
                "open": 70_000.0,
                "high": 70_500.0,
                "low": 69_500.0,
                "close": 70_000.0,
                "volume": 1_000.0,
            }
            for date in pd.date_range("2026-06-01", periods=45, freq="D")
        ]
        frame = _add_metrics(pd.DataFrame(rows))

        ok, reasons, metrics = _passes_filters(
            frame,
            {},
            ScreenerParams(asset_class="crypto"),
        )

        self.assertTrue(ok)
        self.assertNotIn("price_out_of_range", reasons)
        self.assertNotIn("illiquid", reasons)
        self.assertTrue(metrics["price_range_ok"])
        self.assertTrue(metrics["volume_ok"])
        self.assertEqual(metrics["asset_class"], "crypto")
        self.assertNotIn("corporate_actions", metrics["missing_quality_data"])
        self.assertNotIn("earnings_calendar", metrics["missing_quality_data"])

    def test_crypto_signal_sizing_keeps_fractional_units(self) -> None:
        rows = [
            {"date": "2026-06-29", "open": 102.2, "high": 102.4, "low": 101.8, "close": 102.0, "volume": 1_100_000},
            {"date": "2026-06-30", "open": 101.8, "high": 102.0, "low": 101.3, "close": 101.5, "volume": 1_100_000},
            {"date": "2026-07-01", "open": 101.3, "high": 101.5, "low": 100.6, "close": 100.8, "volume": 1_100_000},
            {"date": "2026-07-02", "open": 100.5, "high": 100.7, "low": 99.2, "close": 99.7, "volume": 1_100_000},
            {"date": "2026-07-03", "open": 99.8, "high": 101.2, "low": 99.5, "close": 100.8, "volume": 1_300_000},
            {"date": "2026-07-06", "open": 100.4, "high": 101.0, "low": 100.1, "close": 100.7, "volume": 1_100_000},
        ]
        signals = _detect_signals(
            "TEST-USDT",
            _strategy_frame(rows),
            [{"price": 100.0, "kind": "resistance", "touches": 3, "strength": 0.8, "tolerance": 0.2}],
            ScreenerParams(
                asset_class="crypto",
                strategies=("LP2",),
                equity=100.0,
                risk_pct=0.005,
            ),
        )

        self.assertEqual(len(signals), 1)
        self.assertGreater(signals[0]["position_size_units"], 0)
        self.assertLess(signals[0]["position_size_units"], 1)
        self.assertNotEqual(signals[0]["status"], "capital_insufficient")

    def test_crypto_and_stock_share_the_same_lp_prb_pattern_result(self) -> None:
        rows = [
            {"date": "2026-06-29", "open": 102.2, "high": 102.4, "low": 101.8, "close": 102.0, "volume": 1_100_000},
            {"date": "2026-06-30", "open": 101.8, "high": 102.0, "low": 101.3, "close": 101.5, "volume": 1_100_000},
            {"date": "2026-07-01", "open": 101.3, "high": 101.5, "low": 100.6, "close": 100.8, "volume": 1_100_000},
            {"date": "2026-07-02", "open": 100.5, "high": 100.7, "low": 99.2, "close": 99.7, "volume": 1_100_000},
            {"date": "2026-07-03", "open": 99.8, "high": 101.2, "low": 99.5, "close": 100.8, "volume": 1_300_000},
            {"date": "2026-07-06", "open": 100.4, "high": 101.0, "low": 100.1, "close": 100.7, "volume": 1_100_000},
        ]
        frame = _strategy_frame(rows)
        levels = [{"price": 100.0, "kind": "resistance", "touches": 3, "strength": 0.8, "tolerance": 0.2}]
        stock = _detect_signals(
            "TEST",
            frame,
            levels,
            ScreenerParams(asset_class="stock", strategies=("LP2",)),
        )[0]
        crypto = _detect_signals(
            "TEST-USDT",
            frame,
            levels,
            ScreenerParams(asset_class="crypto", strategies=("LP2",)),
        )[0]

        parity_fields = (
            "strategy",
            "side",
            "level_type",
            "level_origin_type",
            "level_price",
            "signal_bar_date",
            "entry_day",
            "pattern_bar_dates",
            "entry_price",
            "planned_entry_price",
            "stop_variant",
            "stop_price",
            "take_profit_price",
            "rr",
            "entry_triggered",
            "reasoning",
        )
        self.assertEqual(
            {field: stock[field] for field in parity_fields},
            {field: crypto[field] for field in parity_fields},
        )

    def test_crypto_chart_resamples_five_minute_bars_for_all_stock_chart_tools(self) -> None:
        source = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2026-08-24T00:00:00Z") + timedelta(minutes=5 * index),
                    "open": 100.0 + index,
                    "high": 101.0 + index,
                    "low": 99.0 + index,
                    "close": 100.5 + index,
                    "volume": 10.0,
                }
                for index in range(6)
            ]
        )

        def bars(_symbol: str, timeframe: str) -> pd.DataFrame:
            return source if timeframe == "intraday_5m" else pd.DataFrame()

        with patch.object(server.da, "get_crypto_bars", side_effect=bars):
            payload = server.api_crypto_bars("BTC-USDT", "intraday_15m")

        self.assertEqual(len(payload["bars"]), 2)
        self.assertFalse(payload["stale"])
        self.assertEqual(payload["bars"][0]["volume"], 30.0)

    def test_dashboard_exposes_a_separate_crypto_screener_tab_and_safe_routes(self) -> None:
        html = (Path(server.ROOT) / "dashboard_react" / "index.html").read_text(encoding="utf-8")

        self.assertIn("{id:'crypto_screener',label:'Screener Crypto'", html)
        self.assertIn('tab===\'crypto_screener\' && <TabMarketScreener refreshKey={refreshKey} asset="crypto"/>', html)
        self.assertIn("`${API}/crypto/market-screener`", html)
        self.assertIn("`${API}/crypto/order/place`", html)
        self.assertIn("`${API}/crypto/bars/", html)
        self.assertIn("SHORT signal is scan-only in OKX SPOT mode.", html)


if __name__ == "__main__":
    unittest.main()
