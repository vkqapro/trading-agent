"""Intraday trade monitoring."""

from __future__ import annotations

from typing import Dict, List

from src.alerts.slack import SlackAlerter
from src.brokers.ibkr import IBKRClient
from src.config import LOGGER, SETTINGS, append_markdown_log
from src.data.news_filter import NewsRiskFilter


def run_intraday(
    broker: IBKRClient,
    alerter: SlackAlerter,
    news_filter: NewsRiskFilter,
    tracked_positions: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    """Monitor open trades, exit on breaking news, and tighten stops when possible."""
    actions: List[Dict[str, object]] = []
    for position in tracked_positions:
        symbol = str(position["symbol"])
        quote = broker.get_market_price(symbol)
        last_price = float(quote.get("last", 0.0))
        stop_loss = float(position.get("stop_loss", 0.0))
        entry = float(position.get("entry", 0.0))
        quantity = int(position.get("quantity", 0))
        direction = str(position.get("direction", "long"))

        if news_filter.has_high_risk_news(symbol) and quantity > 0:
            exit_action = "SELL" if direction == "long" else "BUY"
            broker.place_market_order(symbol, exit_action, quantity)
            payload = {"symbol": symbol, "event": "exit_on_news_risk", "last_price": last_price}
            actions.append(payload)
            alerter.send_error(f"Exited {symbol} due to high-risk news.")
            LOGGER.warning("News exit triggered: %s", payload)
            continue

        invalidated = (direction == "long" and last_price <= stop_loss) or (direction == "short" and last_price >= stop_loss)
        if invalidated:
            payload = {"symbol": symbol, "event": "stop_triggered", "last_price": last_price, "stop_loss": stop_loss}
            actions.append(payload)
            alerter.send_stop_triggered(symbol, stop_loss)
            LOGGER.warning("Position invalidated: %s", payload)
            continue

        one_r_move = abs(entry - stop_loss)
        if one_r_move <= 0:
            continue

        reached_break_even_trail = (
            direction == "long" and last_price >= entry + one_r_move
        ) or (
            direction == "short" and last_price <= entry - one_r_move
        )
        if reached_break_even_trail and stop_loss != entry and quantity > 0:
            stop_action = "SELL" if direction == "long" else "BUY"
            replacement = broker.replace_stop_order(
                symbol=symbol,
                action=stop_action,
                quantity=quantity,
                stop_price=entry,
                existing_order_id=int(position.get("stop_order_id", 0)) or None,
            )
            position["stop_loss"] = entry
            position["stop_order_id"] = replacement.order_id
            payload = {"symbol": symbol, "event": "stop_moved_to_break_even", "new_stop": entry}
            actions.append(payload)
            LOGGER.info("Stop adjusted: %s", payload)

    append_markdown_log(SETTINGS.paths.research_log, "Intraday Monitor", {"events": actions or "none"})
    return actions
