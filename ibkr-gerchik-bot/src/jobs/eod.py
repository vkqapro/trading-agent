"""End-of-day reporting."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from src.alerts.slack import SlackAlerter
from src.config import SETTINGS, append_markdown_log
from src.risk.risk_manager import RiskManager
from src.workflow_log import append_workflow_snapshot


def run_eod(
    risk_manager: RiskManager,
    account_snapshot: Dict[str, object],
    open_positions: List[Dict[str, object]],
    daily_pnl: float,
    alerter: SlackAlerter,
) -> Dict[str, object]:
    """Persist end-of-day account and position summary."""
    risk_manager.update_daily_pnl(daily_pnl)
    summary = {
        "date": datetime.now().date().isoformat(),
        "daily_pnl": round(daily_pnl, 2),
        "daily_pnl_pct": round((daily_pnl / max(risk_manager.account_equity, 1.0)) * 100.0, 2),
        "positions": open_positions,
        "account_snapshot": account_snapshot,
        "blocked": risk_manager.get_state()["blocked"],
        "reasons": risk_manager.get_state()["reasons"],
    }
    append_markdown_log(SETTINGS.paths.trade_log, "End Of Day", summary)
    append_workflow_snapshot(SETTINGS.paths.trade_log, "EOD", {"summary": summary})
    alerter.send_daily_summary(summary)
    return summary
