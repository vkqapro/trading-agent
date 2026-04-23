"""End-of-day reporting."""

from __future__ import annotations

from typing import Dict, List

from src.alerts.slack import SlackAlerter
from src.config import SETTINGS, append_markdown_log
from src.risk.risk_manager import RiskManager


def run_eod(
    risk_manager: RiskManager,
    open_positions: List[Dict[str, object]],
    daily_pnl: float,
    alerter: SlackAlerter,
) -> Dict[str, object]:
    """Persist a daily summary and notify Slack."""
    risk_manager.update_daily_pnl(daily_pnl)
    summary = {
        "daily_pnl": round(daily_pnl, 2),
        "open_positions": len(open_positions),
        "blocked": risk_manager.get_state()["blocked"],
    }
    append_markdown_log(SETTINGS.paths.research_log, "End Of Day", summary)
    alerter.send_daily_summary(summary)
    return summary
