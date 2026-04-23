"""Trading kill switch conditions."""

from __future__ import annotations

from typing import Dict, List, Tuple

from src.config import SETTINGS


def should_trigger_kill_switch(
    account_equity: float,
    daily_realized_pnl: float,
    connection_healthy: bool,
    broker_positions: List[Dict[str, object]],
    internal_positions: List[Dict[str, object]],
) -> Tuple[bool, List[str]]:
    """Block all trading when safety conditions are breached."""
    reasons: List[str] = []
    if daily_realized_pnl < -(account_equity * SETTINGS.risk.max_daily_loss_pct):
        reasons.append("daily_loss_limit_exceeded")
    if not connection_healthy:
        reasons.append("connection_error")
    broker_symbols = sorted(position.get("symbol") for position in broker_positions)
    internal_symbols = sorted(position.get("symbol") for position in internal_positions)
    if broker_symbols != internal_symbols:
        reasons.append("inconsistent_positions")
    return len(reasons) > 0, reasons
