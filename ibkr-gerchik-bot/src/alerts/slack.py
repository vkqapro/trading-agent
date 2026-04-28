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

    def send_daily_summary(self, summary: Dict[str, object]) -> bool:
        positions = summary.get("positions")
        open_position_count = len(positions) if isinstance(positions, list) else 0
        return self.send(
            f"Daily summary: pnl={summary.get('daily_pnl')} open_positions={open_position_count} "
            f"blocked={summary.get('blocked')} reasons={summary.get('reasons')}"
        )
