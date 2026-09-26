from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import pandas as pd

from dashboard_react import server
from src.crypto.symbols import load_crypto_symbol_inputs, remove_crypto_symbol


class CryptoStrategyMonitorTests(unittest.TestCase):
    def test_remove_crypto_symbol_preserves_comments_and_other_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            symbols_path = Path(temp_dir) / "crypto_symbols.txt"
            symbols_path.write_text("# tracked\nBTCUSDT,ETH-USDT\nSOL-USDT\n", encoding="utf-8")

            result = remove_crypto_symbol("BTC-USDT", path=symbols_path)

            self.assertEqual(result, {"removed": True, "symbol": "BTC-USDT"})
            self.assertEqual(load_crypto_symbol_inputs(symbols_path), ["ETH-USDT", "SOL-USDT"])
            self.assertTrue(symbols_path.read_text(encoding="utf-8").startswith("# tracked\n"))

    def test_crypto_strategy_payload_uses_crypto_symbols_and_scanner(self) -> None:
        row = {
            "symbol": "BTC-USDT",
            "signal": "NEAR_LONG",
            "side": "LONG",
            "urgency": "near",
            "gap_pct": 0.2,
        }
        with (
            patch.object(server.da, "load_crypto_symbols", return_value=[{"inst_id": "BTC-USDT"}]),
            patch.object(server.da, "crypto_bars_index", return_value={"OLD-USDT": {}}),
            patch.object(server, "_bmsb_scan_symbol", return_value=row) as scanner,
        ):
            payload = server._strategy_payload("bmsb", "crypto")

        self.assertEqual(payload["asset"], "crypto")
        self.assertEqual(payload["symbols"], ["BTC-USDT"])
        self.assertEqual(payload["matches"], 1)
        scanner.assert_called_once()
        self.assertEqual(scanner.call_args.kwargs["asset"], "crypto")

    def test_crypto_daily_frame_reads_crypto_candles(self) -> None:
        daily = pd.DataFrame(
            [{"date": "2026-08-22", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]
        )
        with patch.object(server.da, "get_crypto_bars", return_value=daily) as get_bars:
            result = server._daily_live_frame("BTC-USDT", "crypto")

        self.assertTrue(result.equals(daily))
        get_bars.assert_called_once_with("BTC-USDT", "daily")

    def test_crypto_bulk_add_reports_added_duplicates_and_invalid(self) -> None:
        add = AsyncMock(
            side_effect=[
                {"ok": True, "symbol": "BTC-USDT", "added": True, "duplicate": False, "pid": 101},
                {"ok": True, "symbol": "ETH-USDT", "added": False, "duplicate": True, "pid": None},
            ]
        )
        with patch.object(server, "api_crypto_add", add):
            result = asyncio.run(server.api_crypto_bulk_add({"csv_text": "symbol\nBTC-USDT,ETH-USDT"}))

        self.assertEqual(result["added"], ["BTC-USDT"])
        self.assertEqual(result["duplicates"], ["ETH-USDT"])
        self.assertEqual(result["invalid"], [])
        self.assertEqual(result["pids"], [101])

    def test_crypto_manual_order_endpoint_is_simulation_only(self) -> None:
        result = asyncio.run(
            server.api_crypto_order_simulate(
                {
                    "symbol": "BTC-USDT",
                    "signal": "BUY",
                    "entry": 100.0,
                    "stop": 95.0,
                    "target": 115.0,
                    "quantity": 1.0,
                    "available_cash": 1000.0,
                }
            )
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["paper"])
        self.assertTrue(result["dry_run"])
        self.assertIn("No order was submitted", result["message"])

    def test_crypto_manual_place_endpoint_returns_demo_exchange_receipt(self) -> None:
        submitted = {
            "ok": True,
            "id": "local-order-1",
            "status": "submitted",
            "message": "OKX Demo LIMIT BUY submitted.",
            "okx_order_id": "okx-demo-1",
            "plan": {"ok": True},
        }
        with patch.object(server, "execute_manual_demo_order", return_value=submitted) as execute:
            result = asyncio.run(
                server.api_crypto_order_place(
                    {
                        "symbol": "BTC-USDT",
                        "signal": "BUY",
                        "entry": 100.0,
                        "stop": 95.0,
                        "target": 115.0,
                        "quantity": 1.0,
                        "available_cash": 1000.0,
                        "max_open_risk_pct": 5.0,
                        "reward_risk": 2.5,
                        "entry_order_type": "LIMIT",
                    }
                )
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["paper"])
        self.assertTrue(result["demo"])
        self.assertFalse(result["dry_run"])
        self.assertFalse(result["live"])
        self.assertEqual(result["okx_order_id"], "okx-demo-1")
        self.assertEqual(execute.call_args.kwargs["order_type"], "LIMIT")
        self.assertEqual(execute.call_args.args[0].max_risk_pct, 0.05)
        self.assertEqual(execute.call_args.args[0].min_reward_risk_ratio, 2.5)

    def test_crypto_order_rejects_invalid_ui_max_risk(self) -> None:
        with self.assertRaises(server.HTTPException) as raised:
            asyncio.run(
                server.api_crypto_order_place(
                    {
                        "symbol": "BTC-USDT",
                        "signal": "BUY",
                        "entry": 100.0,
                        "stop": 95.0,
                        "target": 115.0,
                        "quantity": 1.0,
                        "available_cash": 1000.0,
                        "max_open_risk_pct": 101.0,
                    }
                )
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Max risk %", raised.exception.detail)

    def test_crypto_order_rejects_invalid_ui_reward_risk(self) -> None:
        with self.assertRaises(server.HTTPException) as raised:
            asyncio.run(
                server.api_crypto_order_place(
                    {
                        "symbol": "BTC-USDT",
                        "signal": "BUY",
                        "entry": 100.0,
                        "stop": 95.0,
                        "target": 115.0,
                        "quantity": 1.0,
                        "available_cash": 1000.0,
                        "max_open_risk_pct": 5.0,
                        "reward_risk": 0.0,
                    }
                )
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Reward:risk", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
