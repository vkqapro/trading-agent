"""Intraday trade monitoring."""

from __future__ import annotations

from typing import Dict, List

from src.alerts.slack import SlackAlerter
from src.brokers.ibkr import IBKRClient
from src.config import SETTINGS, append_markdown_log
from src.data.news_filter import NewsRiskFilter
from src.risk.kill_switch import should_trigger_kill_switch
from src.workflow_log import append_workflow_snapshot


def run_intraday(
    broker: IBKRClient,
    alerter: SlackAlerter,
    news_filter: NewsRiskFilter,
    tracked_positions: List[Dict[str, object]],
    account_equity: float,
    daily_realized_pnl: float = 0.0,
    dry_run: bool = False,
) -> List[Dict[str, object]]:
    """Monitor open positions, verify stops, and react to invalidation."""
    actions: List[Dict[str, object]] = []
    open_orders = broker.get_open_orders()
    stop_symbols = {order.get("symbol") for order in open_orders if order.get("type") == "STP"}
    macro_risk = news_filter.is_macro_risk()
    kill_switch, reasons = should_trigger_kill_switch(
        account_equity=account_equity,
        daily_realized_pnl=daily_realized_pnl,
        connection_healthy=broker.is_connected,
        broker_positions=broker.get_positions(),
        internal_positions=tracked_positions,
        macro_risk=macro_risk,
        stop_integrity_ok=all(position.get("symbol") in stop_symbols for position in tracked_positions),
    )
    if kill_switch:
        for position in tracked_positions:
            quantity = int(position.get("quantity", 0))
            if quantity <= 0:
                continue
            action = "SELL" if position.get("direction") == "long" else "BUY"
            if not dry_run:
                broker.place_market_order(str(position["symbol"]), action, quantity)
        payload = {"event": "kill_switch", "reasons": reasons}
        actions.append(payload)
        append_workflow_snapshot(SETTINGS.paths.research_log, "Intraday", {"actions": actions})
        alerter.send_intraday_heartbeat(
            {
                "tracked_symbols": [position.get("symbol") for position in tracked_positions],
                "actions": actions,
                "macro_risk": macro_risk,
            }
        )
        return actions

    for position in tracked_positions:
        symbol = str(position["symbol"])
        quote = broker.get_market_price(symbol)
        current_price = float(quote.get("last", 0.0))
        entry = float(position.get("entry", 0.0))
        stop_loss = float(position.get("stop_loss", 0.0))
        quantity = int(position.get("quantity", 0))
        direction = str(position.get("direction", "long"))
        pnl_pct = (((current_price - entry) / entry) * 100.0) if direction == "long" and entry else (((entry - current_price) / entry) * 100.0 if entry else 0.0)
        if pnl_pct <= -7.0 and quantity > 0:
            if not dry_run:
                broker.place_market_order(symbol, "SELL" if direction == "long" else "BUY", quantity)
            actions.append({"symbol": symbol, "event": "loss_cut", "pnl_pct": round(pnl_pct, 2)})
            continue
        if news_filter.has_high_risk_news(symbol) and quantity > 0:
            if not dry_run:
                broker.place_market_order(symbol, "SELL" if direction == "long" else "BUY", quantity)
            actions.append({"symbol": symbol, "event": "thesis_break_news"})
            continue
        if pnl_pct >= 20.0 or pnl_pct >= 15.0:
            trail_percent = 5.0 if pnl_pct >= 20.0 else 7.0
            proposed_stop = current_price * (1 - max(trail_percent, 3.0) / 100.0) if direction == "long" else current_price * (1 + max(trail_percent, 3.0) / 100.0)
            if not dry_run:
                broker.replace_stop_order(symbol, "SELL" if direction == "long" else "BUY", quantity, proposed_stop, int(position.get("stop_order_id", 0)) or None)
            position["stop_loss"] = round(proposed_stop, 2)
            actions.append({"symbol": symbol, "event": "stop_adjusted", "new_stop": round(proposed_stop, 2)})

    append_markdown_log(SETTINGS.paths.trade_log, "Intraday Actions", {"actions": actions or "none"})
    append_workflow_snapshot(SETTINGS.paths.research_log, "Intraday", {"actions": actions, "tracked_positions": tracked_positions})
    alerter.send_intraday_heartbeat(
        {
            "tracked_symbols": [position.get("symbol") for position in tracked_positions],
            "actions": actions,
            "macro_risk": macro_risk,
        }
    )
    return actions
