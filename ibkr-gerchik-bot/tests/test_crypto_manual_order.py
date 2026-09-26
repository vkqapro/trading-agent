from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from src.crypto import manual_order, okx_client
from src.crypto.manual_order import execute_manual_demo_order, load_manual_order_state
from src.crypto.okx_client import OKXClient, OKXInstrument
from src.crypto.order_manager import CryptoOrderManager, CryptoOrderRequest


def _request(*, side: str = "BUY", quantity: float = 0.123456) -> CryptoOrderRequest:
    return CryptoOrderRequest(
        symbol="BTC-USDT",
        side=side,
        entry=100.0,
        stop=95.0 if side == "BUY" else 105.0,
        target=115.0 if side == "BUY" else 85.0,
        quantity=quantity,
        account_equity=1000.0,
        cash_available=1000.0,
    )


class FakeOKXClient:
    def __init__(self) -> None:
        self.calls = []

    def instruments(self, inst_type: str):
        self.calls.append(("instruments", inst_type))
        return [
            OKXInstrument(
                inst_id="BTC-USDT",
                inst_type="SPOT",
                base_ccy="BTC",
                quote_ccy="USDT",
                state="live",
                lot_sz="0.0001",
                min_sz="0.0001",
                tick_sz="0.1",
            )
        ]

    def place_spot_order(self, **kwargs):
        self.calls.append(("place_spot_order", kwargs))
        return {"code": "0", "data": [{"ordId": "demo-order-123", "sCode": "0"}]}


class CryptoManualOrderTests(unittest.TestCase):
    def test_demo_limit_order_submits_attached_stop_target_and_persists_receipt(self) -> None:
        fake = FakeOKXClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "manual_okx_orders.json"
            with patch.object(manual_order, "STATE_PATH", state_path):
                result = execute_manual_demo_order(_request(), order_type="LIMIT", client=fake)
                state = load_manual_order_state()

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["okx_order_id"], "demo-order-123")
        self.assertTrue(result["protection_attached"])
        call = next(payload for name, payload in fake.calls if name == "place_spot_order")
        self.assertEqual(call["inst_id"], "BTC-USDT")
        self.assertEqual(call["order_type"], "LIMIT")
        self.assertEqual(call["size"], "0.1234")
        self.assertEqual(call["price"], "100")
        self.assertEqual(call["stop"], "95")
        self.assertEqual(call["target"], "115")
        self.assertEqual(state["orders"][0]["okx_order_id"], "demo-order-123")

    def test_demo_execution_fails_closed_when_simulated_trading_is_disabled(self) -> None:
        fake = FakeOKXClient()
        unsafe_settings = SimpleNamespace(okx_simulated_trading=False, okx_account_mode="spot")
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(manual_order, "STATE_PATH", Path(temp_dir) / "manual_okx_orders.json"),
                patch.object(manual_order, "CRYPTO_SETTINGS", unsafe_settings),
            ):
                result = execute_manual_demo_order(_request(), order_type="MARKET", client=fake)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "error")
        self.assertIn("must stay true", result["message"])
        self.assertFalse(any(name == "place_spot_order" for name, _payload in fake.calls))

    def test_spot_short_is_rejected_before_exchange_submission(self) -> None:
        fake = FakeOKXClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(manual_order, "STATE_PATH", Path(temp_dir) / "manual_okx_orders.json"):
                result = execute_manual_demo_order(_request(side="SELL"), order_type="MARKET", client=fake)

        self.assertFalse(result["ok"])
        self.assertIn("not supported in OKX SPOT mode", result["message"])
        self.assertFalse(any(name == "place_spot_order" for name, _payload in fake.calls))

    def test_cash_cap_quantity_is_floored_not_rounded_above_cash(self) -> None:
        request = CryptoOrderRequest(
            symbol="BTC-USDT",
            side="BUY",
            entry=77407.40,
            stop=76401.66,
            target=88716.73,
            account_equity=100.0,
            cash_available=100.0,
        )

        plan = CryptoOrderManager().plan_order(request)

        self.assertTrue(plan["ok"])
        self.assertLessEqual(plan["quantity"] * plan["entry"], 100.0)

    def test_ui_max_risk_override_controls_order_ceiling(self) -> None:
        request = CryptoOrderRequest(
            symbol="DOGE-USDT",
            side="BUY",
            entry=0.09,
            stop=0.06946,
            target=0.15162,
            quantity=973.70983446,
            account_equity=400.0,
            cash_available=400.0,
            max_risk_pct=0.05,
        )

        plan = CryptoOrderManager().plan_order(request)

        self.assertTrue(plan["ok"])
        self.assertEqual(plan["max_open_risk"], 20.0)
        self.assertEqual(plan["risk_budget"], 20.0)
        self.assertEqual(plan["effective_max_risk_pct"], 0.05)
        self.assertEqual(plan["risk_source"], "ui")

    def test_environment_max_open_risk_remains_the_default(self) -> None:
        request = CryptoOrderRequest(
            symbol="DOGE-USDT",
            side="BUY",
            entry=0.09,
            stop=0.06946,
            target=0.15162,
            quantity=973.70983446,
            account_equity=400.0,
            cash_available=400.0,
        )

        plan = CryptoOrderManager().plan_order(request)

        self.assertFalse(plan["ok"])
        self.assertIn("open_risk_limit_exceeded", plan["reasons"])
        self.assertEqual(plan["max_open_risk"], 12.0)
        self.assertEqual(plan["risk_source"], "environment")

    def test_ui_reward_risk_override_controls_manual_order_minimum(self) -> None:
        request = CryptoOrderRequest(
            symbol="WLD-USDT",
            side="BUY",
            entry=0.4193,
            stop=0.365,
            target=0.55505,
            quantity=184.16206261,
            account_equity=200.0,
            cash_available=200.0,
            max_risk_pct=0.05,
            min_reward_risk_ratio=2.5,
        )

        plan = CryptoOrderManager().plan_order(request)

        self.assertTrue(plan["ok"])
        self.assertEqual(plan["reward_risk"], 2.5)
        self.assertEqual(plan["effective_min_reward_risk_ratio"], 2.5)
        self.assertEqual(plan["reward_risk_source"], "ui")

    def test_environment_reward_risk_remains_default_without_ui_override(self) -> None:
        request = CryptoOrderRequest(
            symbol="WLD-USDT",
            side="BUY",
            entry=0.4193,
            stop=0.365,
            target=0.55505,
            quantity=184.16206261,
            account_equity=200.0,
            cash_available=200.0,
            max_risk_pct=0.05,
        )

        plan = CryptoOrderManager().plan_order(request)

        self.assertFalse(plan["ok"])
        self.assertIn("reward_risk_too_low", plan["reasons"])
        self.assertEqual(plan["effective_min_reward_risk_ratio"], 3.0)
        self.assertEqual(plan["reward_risk_source"], "environment")


class OKXSpotOrderPayloadTests(unittest.TestCase):
    def test_private_auth_header_is_forced_to_okx_demo_mode(self) -> None:
        client = OKXClient(base_url="https://example.invalid")
        settings = SimpleNamespace(
            okx_api_key="demo-key",
            okx_api_secret="demo-secret",
            okx_api_passphrase="demo-passphrase",
            okx_simulated_trading=True,
        )

        with patch.object(okx_client, "CRYPTO_SETTINGS", settings):
            headers = client._auth_headers("POST", "/api/v5/trade/order", "{}")

        self.assertEqual(headers["x-simulated-trading"], "1")
        self.assertEqual(headers["OK-ACCESS-KEY"], "demo-key")

    def test_limit_order_payload_contains_demo_ready_attached_protection(self) -> None:
        client = OKXClient(base_url="https://example.invalid")
        captured = {}

        def fake_request(method, path, **kwargs):
            captured.update({"method": method, "path": path, **kwargs})
            return {"code": "0", "data": [{"ordId": "1"}]}

        with patch.object(client, "_request", side_effect=fake_request):
            client.place_spot_order(
                inst_id="BTC-USDT",
                side="buy",
                size="0.01",
                order_type="LIMIT",
                price="100",
                client_order_id="manual1",
                stop="95",
                target="115",
                attached_algo_client_order_id="protect1",
            )

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/api/v5/trade/order")
        self.assertTrue(captured["private"])
        body = captured["body"]
        self.assertEqual(body["ordType"], "limit")
        self.assertEqual(body["px"], "100")
        self.assertNotIn("tgtCcy", body)
        self.assertEqual(body["attachAlgoOrds"][0]["tpOrdPx"], "-1")
        self.assertEqual(body["attachAlgoOrds"][0]["slOrdPx"], "-1")


if __name__ == "__main__":
    unittest.main()
