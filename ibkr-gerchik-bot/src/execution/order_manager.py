"""Order execution orchestration."""

from __future__ import annotations

from typing import Dict, List, Tuple

from src.alerts.slack import SlackAlerter
from src.brokers.ibkr import IBKRClient, OrderResult
from src.config import LOGGER, SETTINGS, append_markdown_log
from src.risk.position_size import calculate_position_size
from src.strategy.validator import validate_trade


class OrderManager:
    """Execute validated trades and record the resulting actions."""

    def __init__(self, broker: IBKRClient, alerter: SlackAlerter) -> None:
        self.broker = broker
        self.alerter = alerter

    def execute_trade(
        self,
        signal: Dict[str, object],
        account_equity: float,
        current_positions: List[Dict[str, object]],
        open_risk_amount: float,
        spread_pct: float,
    ) -> Tuple[bool, Dict[str, object]]:
        entry = float(signal["entry"])
        stop_loss = float(signal["stop_loss"])
        quantity = calculate_position_size(account_equity, SETTINGS.risk.risk_per_trade, entry, stop_loss)
        enriched_signal = dict(signal)
        enriched_signal["risk_amount"] = abs(entry - stop_loss) * quantity
        enriched_signal["quantity"] = quantity

        is_valid, reasons = validate_trade(
            enriched_signal,
            account_equity=account_equity,
            current_positions=current_positions,
            open_risk_amount=open_risk_amount,
            spread_pct=spread_pct,
        )
        if not is_valid or quantity <= 0:
            payload = {"status": "rejected", "reasons": reasons or ["position_size_zero"], "signal": enriched_signal}
            LOGGER.warning("Trade rejected: %s", payload)
            return False, payload

        action = "BUY" if str(signal["direction"]) == "long" else "SELL"
        stop_action = "SELL" if action == "BUY" else "BUY"
        market_order: OrderResult = self.broker.place_market_order(str(signal["symbol"]), action, quantity)
        stop_order: OrderResult = self.broker.place_stop_order(str(signal["symbol"]), stop_action, quantity, stop_loss)

        trade_payload = {
            "status": "executed",
            "symbol": signal["symbol"],
            "strategy": signal["strategy"],
            "direction": signal["direction"],
            "entry": entry,
            "stop_loss": stop_loss,
            "target": signal["target"],
            "quantity": quantity,
            "market_order_id": market_order.order_id,
            "stop_order_id": stop_order.order_id,
        }
        append_markdown_log(SETTINGS.paths.trade_log, f"Trade {signal['symbol']}", trade_payload)
        self.alerter.send_trade_executed(trade_payload)
        return True, trade_payload
