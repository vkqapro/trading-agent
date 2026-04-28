"""Slack webhook notifications."""

from __future__ import annotations

from typing import Dict

import requests

from src.config import LOGGER, SETTINGS

try:
    from slack_sdk.webhook import WebhookClient
except ImportError:  # pragma: no cover - optional dependency path.
    WebhookClient = None  # type: ignore[assignment]


class SlackAlerter:
    """Send alerts to Slack with graceful degradation when disabled."""

    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url or SETTINGS.slack_webhook
        self.client = WebhookClient(self.webhook_url) if self.webhook_url and WebhookClient else None

    def send(self, message: str) -> bool:
        if not self.webhook_url:
            LOGGER.info("Slack webhook not configured. Message skipped: %s", message)
            return False
        try:
            if self.client is not None:
                response = self.client.send(text=message)
                return response.status_code == 200
            response = requests.post(self.webhook_url, json={"text": message}, timeout=10)
            response.raise_for_status()
            return True
        except Exception:
            LOGGER.exception("Failed to send Slack alert.")
            return False

    def send_trade_executed(self, trade: Dict[str, object]) -> bool:
        return self.send(
            f"Trade executed: {trade['symbol']} {trade['signal']} qty={trade['quantity']} "
            f"entry={trade['entry']} stop={trade['stop_loss']} target={trade['target']}"
        )

    def send_stop_triggered(self, symbol: str, stop_price: float) -> bool:
        return self.send(f"Stop triggered: {symbol} at {stop_price}")

    def send_error(self, message: str) -> bool:
        return self.send(f"Error: {message}")

    def send_intraday_heartbeat(self, summary: Dict[str, object]) -> bool:
        tracked_symbols = summary.get("tracked_symbols")
        tracked_count = len(tracked_symbols) if isinstance(tracked_symbols, list) else 0
        actions = summary.get("actions")
        action_count = len(actions) if isinstance(actions, list) else 0
        macro_risk = bool(summary.get("macro_risk", False))
        action_label = "actions taken" if action_count else "no action"
        lines = [
            "Intraday heartbeat",
            f"Tracked: {tracked_count}",
            f"Macro risk: {'ON' if macro_risk else 'OFF'}",
            f"Status: {action_label}",
        ]
        if isinstance(tracked_symbols, list) and tracked_symbols:
            preview = ", ".join(str(symbol) for symbol in tracked_symbols[:8])
            if len(tracked_symbols) > 8:
                preview = f"{preview}, ..."
            lines.append(f"Universe: {preview}")
        if action_count and isinstance(actions, list):
            lines.append(f"Latest: {actions[0]}")
        return self.send("\n".join(lines))

    def send_premarket_summary(self, summary: Dict[str, object]) -> bool:
        macro_risk = summary.get("macro_risk", {})
        blocked = bool(macro_risk.get("blocked")) if isinstance(macro_risk, dict) else False
        ideas = summary.get("ideas")
        research_symbols = summary.get("research_symbols")
        lines = [
            "Premarket summary",
            f"Macro risk: {'ON' if blocked else 'OFF'}",
        ]

        if isinstance(research_symbols, list) and research_symbols:
            universe_preview = ", ".join(str(symbol) for symbol in research_symbols[:8])
            if len(research_symbols) > 8:
                universe_preview = f"{universe_preview}, ..."
            lines.append(f"Universe: {universe_preview}")

        if isinstance(ideas, list) and ideas:
            for idea in ideas[:3]:
                symbol = idea.get("symbol", "?")
                decision = idea.get("decision", "WATCH")
                entry = idea.get("entry", "-")
                stop = idea.get("stop", "-")
                target = idea.get("target", "-")
                lines.append(
                    f"{symbol}: {decision} entry {entry} stop {stop} target {target}"
                )
        else:
            lines.append("Ideas: none")

        if blocked and isinstance(macro_risk, dict):
            matched = macro_risk.get("matched_headlines")
            if isinstance(matched, list) and matched:
                lines.append(f"Risk: {matched[0]}")

        return self.send("\n".join(lines))

    def send_daily_summary(self, summary: Dict[str, object]) -> bool:
        positions = summary.get("positions")
        open_position_count = len(positions) if isinstance(positions, list) else 0
        return self.send(
            f"Daily summary: pnl={summary.get('daily_pnl')} open_positions={open_position_count} "
            f"blocked={summary.get('blocked')} reasons={summary.get('reasons')}"
        )
