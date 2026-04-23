"""Intraday trade monitoring."""

from __future__ import annotations

from typing import Dict, List

from src.alerts.slack import SlackAlerter
from src.brokers.ibkr import IBKRClient
from src.config import LOGGER, SETTINGS, append_markdown_log


def run_intraday(
    broker: IBKRClient,
    alerter: SlackAlerter,
    tracked_positions: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    """Monitor open trades and notify on stop or invalidation events."""
    actions: List[Dict[str, object]] = []
    for position in tracked_positions:
        symbol = str(position["symbol"])
        quote = broker.get_market_price(symbol)
        last_price = quote.get("last", 0.0)
        stop_loss = float(position.get("stop_loss", 0.0))
        direction = str(position.get("direction", "long"))
        invalidated = (direction == "long" and last_price <= stop_loss) or (direction == "short" and last_price >= stop_loss)
        if invalidated:
            payload = {"symbol": symbol, "event": "stop_triggered", "last_price": last_price, "stop_loss": stop_loss}
            actions.append(payload)
            alerter.send_stop_triggered(symbol, stop_loss)
            LOGGER.warning("Position invalidated: %s", payload)

    append_markdown_log(SETTINGS.paths.research_log, "Intraday Monitor", {"events": actions or "none"})
    return actions
