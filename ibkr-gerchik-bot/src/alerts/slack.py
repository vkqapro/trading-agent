"""Slack webhook notifications."""

from __future__ import annotations

from typing import Dict

import requests

from src.config import LOGGER, SETTINGS


class SlackAlerter:
    """Send alerts to Slack with graceful degradation when disabled."""

    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url or SETTINGS.slack_webhook

    def send(self, message: str) -> bool:
        if not self.webhook_url:
            LOGGER.info("Slack webhook not configured. Message skipped: %s", message)
            return False
        try:
            response = requests.post(self.webhook_url, json={"text": message}, timeout=10)
            response.raise_for_status()
            return True
        except requests.RequestException:
            LOGGER.exception("Failed to send Slack alert.")
            return False

    def send_trade_executed(self, trade: Dict[str, object]) -> bool:
        return self.send(
            f"Trade executed: {trade['symbol']} {trade['direction']} qty={trade['quantity']} "
            f"entry={trade['entry']} stop={trade['stop_loss']} target={trade['target']}"
        )

    def send_stop_triggered(self, symbol: str, stop_price: float) -> bool:
        return self.send(f"Stop triggered: {symbol} at {stop_price}")

    def send_error(self, message: str) -> bool:
        return self.send(f"Error: {message}")

    def send_daily_summary(self, summary: Dict[str, object]) -> bool:
        return self.send(
            f"Daily summary: pnl={summary.get('daily_pnl')} open_positions={summary.get('open_positions')} "
            f"blocked={summary.get('blocked')}"
        )
