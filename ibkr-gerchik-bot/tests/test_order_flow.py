from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

import pandas as pd

from src.alerts.slack import SlackAlerter
from src.brokers.ibkr import OrderResult
from src.config import SETTINGS
from src.execution.order_manager import OrderManager
from src.jobs.session_utils import calculate_open_risk_amount, run_entry_scan
from src.strategy.signal_models import TradeSignal


def _sanm_signal() -> TradeSignal:
    return TradeSignal(
        symbol="SANM",
        strategy="premarket_watch_validation",
        signal="BUY",
        direction="long",
        entry=241.97,
        stop=237.09,
        target=255.22,
        level_price=237.09,
        level_type="gap",
        nearest_upper_level=255.22,
        nearest_lower_level=237.09,
        reward_risk=2.72,
        partial_targets=[{"qty_pct": 0.5, "price": 255.22}],
        notes=[],
    )


def _intraday_bars() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"open": 240.8, "high": 241.2, "low": 240.4, "close": 240.9},
            {"open": 240.9, "high": 241.7, "low": 240.6, "close": 241.3},
            {"open": 241.3, "high": 242.1, "low": 240.9, "close": 241.6},
        ]
    )


class _BrokerStub:
    def __init__(self) -> None:
        self.is_connected = True
        self.orders: List[Dict[str, object]] = []
        self.next_order_id = 1000

    def _record(self, order_type: str, symbol: str, action: str, quantity: int) -> OrderResult:
        self.next_order_id += 1
        payload = {
            "order_id": self.next_order_id,
            "symbol": symbol,
            "action": action,
            "quantity": quantity,
            "order_type": order_type,
        }
        self.orders.append(payload)
        return OrderResult(
            order_id=self.next_order_id,
            symbol=symbol,
            action=action,
            quantity=quantity,
            order_type=order_type,
            status="Submitted",
        )

    def place_market_order(self, symbol: str, action: str, quantity: int) -> OrderResult:
        return self._record("MKT", symbol, action, quantity)

    def place_market_bracket_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        stop_price: float,
        limit_price: float | None = None,
        *,
        outside_rth: bool = False,
        tif: str | None = None,
    ) -> tuple[OrderResult, OrderResult, OrderResult | None]:
        del outside_rth, tif
        entry_result = self._record("MKT", symbol, action, quantity)
        exit_action = "SELL" if action.upper() == "BUY" else "BUY"
        stop_result = self._record("STP", symbol, exit_action, quantity)
        self.orders[-1]["stop_price"] = stop_price
        limit_result = None
        if limit_price is not None:
            limit_result = self._record("LMT", symbol, exit_action, quantity)
            self.orders[-1]["limit_price"] = limit_price
        return entry_result, stop_result, limit_result

    def place_stop_order(self, symbol: str, action: str, quantity: int, stop_price: float) -> OrderResult:
        result = self._record("STP", symbol, action, quantity)
        self.orders[-1]["stop_price"] = stop_price
        return result

    def place_limit_order(self, symbol: str, action: str, quantity: int, limit_price: float) -> OrderResult:
        result = self._record("LMT", symbol, action, quantity)
        self.orders[-1]["limit_price"] = limit_price
        return result


class _MarketDataStub:
    def __init__(self, *, market_open: bool = True) -> None:
        self._market_open = market_open

    def get_quote(self, symbol: str | None = None) -> Dict[str, float]:
        del symbol
        return {"bid": 241.95, "ask": 241.99, "last": 241.97}

    def market_is_open(self, current_time: object | None = None) -> bool:
        del current_time
        return self._market_open

    def unstable_open_window(self) -> bool:
        return False

    def get_intraday_bars(self, symbol: str, duration: str = "2 D", bar_size: str = "5 mins") -> pd.DataFrame:
        del symbol, duration, bar_size
        return _intraday_bars()


class _NewsFilterStub:
    def has_high_risk_news(self, symbol: str) -> bool:
        del symbol
        return False

    def is_macro_risk(self) -> bool:
        return False

    def get_macro_risk_context(self) -> Dict[str, object]:
        return {"risk_level": "LOW", "provider_hits": [], "matched_headlines": []}

    def get_symbol_risk_context(self, symbol: str) -> Dict[str, object]:
        del symbol
        return {"risk_level": "LOW", "blocked": False, "matched_headlines": [], "provider_hits": [], "source_types": []}


class OrderFlowTests(unittest.TestCase):
    def test_open_risk_ignores_positions_without_stop_loss(self) -> None:
        self.assertEqual(
            calculate_open_risk_amount(
                [
                    {"symbol": "SEI", "quantity": 100, "entry": 78.18, "direction": "short"},
                    {"symbol": "BBBY", "quantity": 100, "entry": 4.65, "direction": "long", "stop_loss": 0},
                ]
            ),
            0.0,
        )

    def test_sanm_setup_is_rejected_under_current_default_limits(self) -> None:
        broker = _BrokerStub()
        order_manager = OrderManager(
            broker=broker,
            market_data=_MarketDataStub(),
            alerter=SlackAlerter(),
            news_filter=_NewsFilterStub(),
            dry_run=False,
        )

        success, payload = order_manager.execute_trade(
            _sanm_signal(),
            account_equity=100_000.0,
            cash_available=100_000.0,
            current_positions=[],
            open_risk_amount=0.0,
        )

        self.assertFalse(success)
        self.assertIn("reward_risk_too_low", payload["reasons"])
        self.assertIn("order_size_invalid", payload["reasons"])
        self.assertIn("position_value_invalid", payload["reasons"])
        self.assertEqual(broker.orders, [])

    def test_allow_after_hours_submits_valid_trade_when_other_rules_pass(self) -> None:
        original_trade_log = SETTINGS.paths.trade_log
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_trade_log = Path(temp_dir) / "TRADE_LOG.md"
            temp_trade_log.write_text("# Trade Log\n", encoding="utf-8")
            object.__setattr__(SETTINGS.paths, "trade_log", temp_trade_log)

            broker = _BrokerStub()
            order_manager = OrderManager(
                broker=broker,
                market_data=_MarketDataStub(market_open=False),
                alerter=SlackAlerter(),
                news_filter=_NewsFilterStub(),
                dry_run=False,
            )
            signal = TradeSignal(
                symbol="KO",
                strategy="manual_watch",
                signal="BUY",
                direction="long",
                entry=80.0,
                stop=76.0,
                target=92.0,
                level_price=76.0,
                level_type="manual_watch",
                nearest_upper_level=92.0,
                nearest_lower_level=None,
                reward_risk=3.0,
                partial_targets=[{"qty_pct": 1.0, "price": 92.0}],
                notes=[],
            )

            success, payload = order_manager.execute_trade(
                signal,
                account_equity=100_000.0,
                cash_available=80_000.0,
                current_positions=[],
                open_risk_amount=0.0,
                allow_after_hours=True,
            )

        object.__setattr__(SETTINGS.paths, "trade_log", original_trade_log)

        self.assertTrue(success)
        self.assertEqual(payload["status"], "executed")
        self.assertEqual([order["order_type"] for order in broker.orders], ["MKT", "STP", "LMT"])

    def test_run_entry_scan_places_sanm_orders_when_signal_and_limits_allow(self) -> None:
        original_trade_log = SETTINGS.paths.trade_log
        original_min_rr = SETTINGS.risk.min_reward_risk_ratio
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_trade_log = Path(temp_dir) / "TRADE_LOG.md"
            temp_trade_log.write_text("# Trade Log\n", encoding="utf-8")
            object.__setattr__(SETTINGS.paths, "trade_log", temp_trade_log)
            object.__setattr__(SETTINGS.risk, "min_reward_risk_ratio", 2.5)

            broker = _BrokerStub()
            market_data = _MarketDataStub()
            order_manager = OrderManager(
                broker=broker,
                market_data=market_data,
                alerter=SlackAlerter(),
                news_filter=_NewsFilterStub(),
                dry_run=False,
            )
            watchlist = {
                "SANM": {
                    "daily_atr": 30.0,
                    "technical_atr": 3.0,
                    "levels": [
                        {
                            "symbol": "SANM",
                            "price": 237.09,
                            "type": "gap",
                            "timeframe": "daily",
                            "touches": 4,
                            "false_breakouts": 1,
                            "strength_score": 12.0,
                            "created_by": "gap_lower",
                            "nearest_upper_level": 255.22,
                            "nearest_lower_level": 209.08,
                            "zone_low": 234.28,
                            "zone_high": 240.31,
                            "center": 236.99,
                            "strength": 12.0,
                            "atr_value": 12.978,
                        }
                    ],
                }
            }

            with patch("src.jobs.session_utils.route_strategies", return_value=[_sanm_signal()]):
                result = run_entry_scan(
                    stage_name="Open",
                    market_data=market_data,
                    order_manager=order_manager,
                    news_filter=_NewsFilterStub(),
                    watchlist=watchlist,
                    account_equity=50_000.0,
                    cash_available=50_000.0,
                    current_positions=[],
                    open_risk_amount=0.0,
                )

        object.__setattr__(SETTINGS.paths, "trade_log", original_trade_log)
        object.__setattr__(SETTINGS.risk, "min_reward_risk_ratio", original_min_rr)

        self.assertEqual(len(result["executed"]), 1)
        self.assertEqual(result["executed"][0]["symbol"], "SANM")
        self.assertEqual([order["order_type"] for order in broker.orders], ["MKT", "STP", "LMT"])
        self.assertEqual(broker.orders[0]["action"], "BUY")
        self.assertEqual(broker.orders[1]["action"], "SELL")
        self.assertEqual(broker.orders[2]["action"], "SELL")
        self.assertEqual(broker.orders[0]["quantity"], 102)


if __name__ == "__main__":
    unittest.main()
