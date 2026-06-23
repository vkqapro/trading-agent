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


def _manual_candidate_signal() -> TradeSignal:
    return TradeSignal(
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


class _MultiSessionMarketDataStub(_MarketDataStub):
    def get_intraday_bars(self, symbol: str, duration: str = "2 D", bar_size: str = "5 mins") -> pd.DataFrame:
        del symbol, duration, bar_size
        return pd.DataFrame(
            [
                {"datetime": "2026-05-21T15:55:00-04:00", "open": 205.0, "high": 242.1, "low": 200.0, "close": 241.0},
                {"datetime": "2026-05-22T09:35:00-04:00", "open": 241.7, "high": 242.0, "low": 241.6, "close": 241.8},
                {"datetime": "2026-05-22T09:40:00-04:00", "open": 241.8, "high": 242.1, "low": 241.7, "close": 241.97},
            ]
        )


class _SubscriptionBlockedMarketDataStub(_MarketDataStub):
    def get_quote(self, symbol: str | None = None) -> Dict[str, float]:
        del symbol
        return {"bid": 0.0, "ask": 0.0, "last": 80.0, "close": 79.5, "quote_status": "subscription_blocked"}


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

    def test_subscription_blocked_quote_becomes_manual_candidate(self) -> None:
        broker = _BrokerStub()
        order_manager = OrderManager(
            broker=broker,
            market_data=_SubscriptionBlockedMarketDataStub(),
            alerter=SlackAlerter(),
            news_filter=_NewsFilterStub(),
            dry_run=False,
        )

        success, payload = order_manager.execute_trade(
            _manual_candidate_signal(),
            account_equity=100_000.0,
            cash_available=100_000.0,
            current_positions=[],
            open_risk_amount=0.0,
        )

        self.assertFalse(success)
        self.assertEqual(payload["status"], "manual_candidate")
        self.assertEqual(payload["reasons"], ["quote_subscription_required"])
        self.assertEqual(payload["symbol"], "KO")
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
        self.assertEqual(broker.orders[2]["limit_price"], signal.target)

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
        self.assertEqual(result["report_rows"][0]["stock_symbol"], "SANM")
        self.assertEqual(result["report_rows"][0]["reason_not_entered"], "entered")
        self.assertEqual(result["signal_details"][0]["symbol"], "SANM")
        self.assertEqual(result["signal_details"][0]["strategy"], "premarket_watch_validation")
        self.assertEqual(result["signal_details"][0]["status"], "executed")
        self.assertEqual(result["signal_details"][0]["reason"], "entered")
        self.assertEqual(result["signal_details"][0]["signal_level"], 237.09)
        self.assertEqual([order["order_type"] for order in broker.orders], ["MKT", "STP", "LMT"])
        self.assertEqual(broker.orders[0]["action"], "BUY")
        self.assertEqual(broker.orders[1]["action"], "SELL")
        self.assertEqual(broker.orders[2]["action"], "SELL")
        self.assertEqual(broker.orders[2]["limit_price"], result["executed"][0]["target"])
        self.assertEqual(broker.orders[0]["quantity"], 102)

    def test_run_entry_scan_reports_specific_technical_atr_filter_reason(self) -> None:
        original_min_technical_atr_pct = SETTINGS.strategy.minimum_technical_atr_pct
        try:
            object.__setattr__(SETTINGS.strategy, "minimum_technical_atr_pct", 0.01)
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
                    "technical_atr": 1.0,
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
                    stage_name="Intraday",
                    market_data=market_data,
                    order_manager=order_manager,
                    news_filter=_NewsFilterStub(),
                    watchlist=watchlist,
                    account_equity=50_000.0,
                    cash_available=50_000.0,
                    current_positions=[],
                    open_risk_amount=0.0,
                )
        finally:
            object.__setattr__(SETTINGS.strategy, "minimum_technical_atr_pct", original_min_technical_atr_pct)

        self.assertEqual(result["executed"], [])
        self.assertEqual(result["skipped"][0]["reason"], "technical_atr_too_low")
        self.assertIn("technical_atr_too_low", result["signal_details"][0]["reason"])
        self.assertIn("actual=1", result["signal_details"][0]["reason"])
        self.assertIn("required=2.4197", result["signal_details"][0]["reason"])
        self.assertIn("technical_atr_too_low", result["report_rows"][0]["reason_not_entered"])
        self.assertEqual(broker.orders, [])

    def test_run_entry_scan_keeps_subscription_blocked_setup_as_manual_candidate(self) -> None:
        broker = _BrokerStub()
        market_data = _SubscriptionBlockedMarketDataStub()
        order_manager = OrderManager(
            broker=broker,
            market_data=market_data,
            alerter=SlackAlerter(),
            news_filter=_NewsFilterStub(),
            dry_run=False,
        )
        watchlist = {
            "KO": {
                "daily_atr": 40.0,
                "technical_atr": 10.0,
                "levels": [],
            }
        }

        with (
            patch("src.jobs.session_utils.route_strategies", return_value=[_manual_candidate_signal()]),
            patch("src.jobs.session_utils.technical_atr_has_room", return_value=True),
            patch("src.jobs.session_utils.atr_travel_filter", return_value=True),
        ):
            result = run_entry_scan(
                stage_name="Intraday",
                market_data=market_data,
                order_manager=order_manager,
                news_filter=_NewsFilterStub(),
                watchlist=watchlist,
                account_equity=100_000.0,
                cash_available=100_000.0,
                current_positions=[],
                open_risk_amount=0.0,
            )

        self.assertEqual(result["executed"], [])
        self.assertEqual(len(result["manual_candidates"]), 1)
        self.assertEqual(result["manual_candidates"][0]["symbol"], "KO")
        self.assertEqual(result["skipped"][0]["reason"], "quote_subscription_required")
        self.assertEqual(result["report_rows"][0]["stock_symbol"], "KO")
        self.assertEqual(result["report_rows"][0]["reason_not_entered"], "quote_subscription_required")
        self.assertEqual(result["signal_details"][0]["symbol"], "KO")
        self.assertEqual(result["signal_details"][0]["status"], "manual_candidate")
        self.assertEqual(result["signal_details"][0]["reason"], "quote_subscription_required")

    def test_run_entry_scan_uses_current_session_range_for_atr_travel(self) -> None:
        original_trade_log = SETTINGS.paths.trade_log
        original_min_rr = SETTINGS.risk.min_reward_risk_ratio
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_trade_log = Path(temp_dir) / "TRADE_LOG.md"
            temp_trade_log.write_text("# Trade Log\n", encoding="utf-8")
            object.__setattr__(SETTINGS.paths, "trade_log", temp_trade_log)
            object.__setattr__(SETTINGS.risk, "min_reward_risk_ratio", 2.5)

            broker = _BrokerStub()
            market_data = _MultiSessionMarketDataStub()
            order_manager = OrderManager(
                broker=broker,
                market_data=market_data,
                alerter=SlackAlerter(),
                news_filter=_NewsFilterStub(),
                dry_run=False,
            )
            watchlist = {
                "SANM": {
                    "daily_atr": 5.0,
                    "technical_atr": 10.0,
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
                    stage_name="Intraday",
                    market_data=market_data,
                    order_manager=order_manager,
                    news_filter=_NewsFilterStub(),
                    watchlist=watchlist,
                    account_equity=50_000.0,
                    cash_available=50_000.0,
                    current_positions=[],
                    open_risk_amount=0.0,
                    scan_time=pd.Timestamp("2026-05-22T09:40:00-04:00").to_pydatetime(),
                )

        object.__setattr__(SETTINGS.paths, "trade_log", original_trade_log)
        object.__setattr__(SETTINGS.risk, "min_reward_risk_ratio", original_min_rr)

        self.assertEqual(result["skipped"], [])
        self.assertEqual(len(result["executed"]), 1)
        self.assertEqual(result["executed"][0]["symbol"], "SANM")
        self.assertEqual(result["signal_details"][0]["reason"], "entered")
        self.assertEqual([order["order_type"] for order in broker.orders], ["MKT", "STP", "LMT"])


if __name__ == "__main__":
    unittest.main()
