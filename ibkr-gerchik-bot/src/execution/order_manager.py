"""Order execution orchestration."""

from __future__ import annotations

from typing import Dict, List, Tuple

from src.alerts.slack import SlackAlerter
from src.brokers.ibkr import IBKRClient, OrderResult
from src.config import LOGGER, SETTINGS, append_markdown_log
from src.data.market_data import MarketDataService
from src.data.news_filter import NewsRiskFilter
from src.risk.position_size import calculate_position_size, position_value_ok
from src.strategy.signal_models import TradeSignal
from src.strategy.validator import validate_trade


class OrderManager:
    """Execute validated trades and record the resulting actions."""

    def __init__(
        self,
        broker: IBKRClient,
        market_data: MarketDataService,
        alerter: SlackAlerter,
        news_filter: NewsRiskFilter,
        dry_run: bool = False,
    ) -> None:
        self.broker = broker
        self.market_data = market_data
        self.alerter = alerter
        self.news_filter = news_filter
        self.dry_run = dry_run

    def execute_trade(
        self,
        signal: TradeSignal,
        account_equity: float,
        cash_available: float,
        current_positions: List[Dict[str, object]],
        open_risk_amount: float,
        *,
        allow_after_hours: bool = False,
        allow_extended_hours_order: bool = False,
        time_in_force: str | None = None,
    ) -> Tuple[bool, Dict[str, object]]:
        quote = self.market_data.get_quote(signal.symbol)
        spread_pct = self._spread_pct(quote)
        quantity = calculate_position_size(account_equity, SETTINGS.risk.risk_per_trade, signal.entry, signal.stop)
        order_size_valid = position_value_ok(quantity, signal.entry, SETTINGS.risk.max_position_value, cash_available)
        enriched_signal = signal.to_dict()
        enriched_signal["quantity"] = quantity
        enriched_signal["risk_amount"] = abs(signal.entry - signal.stop) * quantity
        enriched_signal["next_major_level"] = signal.nearest_upper_level if signal.direction == "long" else signal.nearest_lower_level

        is_valid, reasons = validate_trade(
            enriched_signal,
            account_equity=account_equity,
            current_positions=current_positions,
            open_risk_amount=open_risk_amount,
            spread_pct=spread_pct,
            paper_trading=SETTINGS.paper_trading,
            tws_connected=self.broker.is_connected,
            account_synced=True,
            market_open=self.market_data.market_is_open() or allow_after_hours,
            first_unstable_minutes=self.market_data.unstable_open_window(),
            allow_first_unstable_minutes=False,
            has_symbol_news_risk=self.news_filter.has_high_risk_news(signal.symbol),
            has_macro_risk=self.news_filter.is_macro_risk(),
            atr_has_room=True,
            order_size_valid=order_size_valid,
            cash_available=cash_available,
        )
        if not is_valid or quantity <= 0:
            payload = {"status": "rejected", "reasons": reasons or ["position_size_zero"], "signal": enriched_signal}
            LOGGER.warning("Trade rejected: %s", payload)
            return False, payload

        if self.dry_run:
            trade_payload = self._build_payload(signal, quantity, 0, 0, status="simulated", dry_run=True)
            append_markdown_log(SETTINGS.paths.trade_log, f"Simulated Trade {signal.symbol}", trade_payload)
            return True, trade_payload

        limit_price = None
        if signal.partial_targets:
            first_target = signal.partial_targets[0]
            limit_price = float(first_target["price"])
        entry_order, stop_order, limit_order = self.broker.place_market_bracket_order(
            signal.symbol,
            signal.signal,
            quantity,
            signal.stop,
            limit_price=limit_price,
            outside_rth=allow_extended_hours_order,
            tif=time_in_force,
        )
        broker_statuses = {
            "market_order": entry_order.status,
            "stop_order": stop_order.status,
            "limit_order": None if limit_order is None else limit_order.status,
        }
        failure_statuses = {"cancelled", "inactive", "apicancelled"}
        if any(
            isinstance(status, str) and status.strip().lower() in failure_statuses
            for status in broker_statuses.values()
            if status is not None
        ):
            payload = {
                "status": "broker_rejected",
                "reasons": ["broker_cancelled_order"],
                "signal": enriched_signal,
                "broker_statuses": broker_statuses,
                "market_order_id": entry_order.order_id,
                "stop_order_id": stop_order.order_id,
                "limit_order_id": 0 if limit_order is None else limit_order.order_id,
            }
            LOGGER.warning("Broker cancelled bracket order: %s", payload)
            return False, payload
        trade_payload = self._build_payload(
            signal,
            quantity,
            entry_order.order_id,
            stop_order.order_id,
            limit_order_id=0 if limit_order is None else limit_order.order_id,
            status="executed",
        )
        trade_payload["broker_statuses"] = broker_statuses
        append_markdown_log(SETTINGS.paths.trade_log, f"Trade {signal.symbol}", trade_payload)
        self.alerter.send_trade_executed(trade_payload)
        return True, trade_payload

    @staticmethod
    def _spread_pct(quote: Dict[str, float]) -> float:
        bid = float(quote.get("bid", 0.0))
        ask = float(quote.get("ask", 0.0))
        mid = ((bid + ask) / 2) or float(quote.get("last", 0.0))
        if mid <= 0:
            return 1.0
        return abs(ask - bid) / mid

    @staticmethod
    def _build_payload(
        signal: TradeSignal,
        quantity: int,
        market_order_id: int,
        stop_order_id: int,
        *,
        limit_order_id: int = 0,
        status: str,
        dry_run: bool = False,
    ) -> Dict[str, object]:
        return {
            "status": status,
            "dry_run": dry_run,
            "symbol": signal.symbol,
            "strategy": signal.strategy,
            "level_type": signal.level_type,
            "direction": signal.direction,
            "signal": signal.signal,
            "entry": signal.entry,
            "stop_loss": signal.stop,
            "target": signal.target,
            "reward_risk": signal.reward_risk,
            "partial_targets": signal.partial_targets,
            "quantity": quantity,
            "market_order_id": market_order_id,
            "stop_order_id": stop_order_id,
            "limit_order_id": limit_order_id,
        }
