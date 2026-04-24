"""Order execution orchestration."""

from __future__ import annotations

from typing import Dict, List, Tuple

from src.alerts.slack import SlackAlerter
from src.brokers.ibkr import IBKRClient, OrderResult
from src.config import LOGGER, SETTINGS, append_markdown_log
from src.data.news_filter import NewsRiskFilter
from src.risk.position_size import calculate_position_size
from src.strategy.validator import validate_trade


class OrderManager:
    """Execute validated trades and record the resulting actions."""

    def __init__(
        self,
        broker: IBKRClient,
        alerter: SlackAlerter,
        news_filter: NewsRiskFilter,
        dry_run: bool = False,
    ) -> None:
        self.broker = broker
        self.alerter = alerter
        self.news_filter = news_filter
        self.dry_run = dry_run

    def execute_trade(
        self,
        signal: Dict[str, object],
        account_equity: float,
        current_positions: List[Dict[str, object]],
        open_risk_amount: float,
        spread_pct: float,
    ) -> Tuple[bool, Dict[str, object]]:
        entry = float(signal["entry"])
        stop_price = float(signal.get("stop", signal.get("stop_loss", 0.0)))
        quantity = calculate_position_size(account_equity, SETTINGS.risk.risk_per_trade, entry, stop_price)
        enriched_signal = dict(signal)
        enriched_signal["risk_amount"] = abs(entry - stop_price) * quantity
        enriched_signal["quantity"] = quantity

        symbol = str(signal["symbol"])
        symbol_news_risk = self.news_filter.has_high_risk_news(symbol)
        macro_risk = self.news_filter.is_macro_risk()
        is_valid, reasons = validate_trade(
            enriched_signal,
            account_equity=account_equity,
            current_positions=current_positions,
            open_risk_amount=open_risk_amount,
            spread_pct=spread_pct,
            has_symbol_news_risk=symbol_news_risk,
            has_macro_risk=macro_risk,
        )
        if not is_valid or quantity <= 0:
            payload = {"status": "rejected", "reasons": reasons or ["position_size_zero"], "signal": enriched_signal}
            LOGGER.warning("Trade rejected: %s", payload)
            return False, payload

        action = str(signal["signal"]).upper()
        stop_action = "SELL" if action == "BUY" else "BUY"

        if self.dry_run:
            trade_payload = {
                "status": "simulated",
                "symbol": symbol,
                "strategy": signal["strategy"],
                "direction": signal.get("direction", "long" if action == "BUY" else "short"),
                "signal": action,
                "entry": entry,
                "stop_loss": stop_price,
                "target": signal["target"],
                "quantity": quantity,
                "market_order_id": 0,
                "stop_order_id": 0,
                "dry_run": True,
            }
            append_markdown_log(SETTINGS.paths.trade_log, f"Simulated Trade {symbol}", trade_payload)
            LOGGER.info("Dry-run trade simulated: %s", trade_payload)
            return True, trade_payload

        try:
            market_order: OrderResult = self.broker.place_market_order(symbol, action, quantity)
            stop_order: OrderResult = self.broker.place_stop_order(symbol, stop_action, quantity, stop_price)
        except Exception as exc:
            self.alerter.send_error(f"Order placement failed for {symbol}: {exc}")
            raise

        trade_payload = {
            "status": "executed",
            "symbol": symbol,
            "strategy": signal["strategy"],
            "direction": signal.get("direction", "long" if action == "BUY" else "short"),
            "signal": action,
            "entry": entry,
            "stop_loss": stop_price,
            "target": signal["target"],
            "quantity": quantity,
            "market_order_id": market_order.order_id,
            "stop_order_id": stop_order.order_id,
        }
        append_markdown_log(SETTINGS.paths.trade_log, f"Trade {symbol}", trade_payload)
        self.alerter.send_trade_executed(trade_payload)
        return True, trade_payload
