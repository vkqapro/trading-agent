"""Trading kill switch conditions."""

from __future__ import annotations

from typing import Dict, List, Tuple

from src.config import SETTINGS


def _equity_symbols(positions: List[Dict[str, object]]) -> List[str]:
    symbols: List[str] = []
    for position in positions:
        sec_type = str(position.get("sec_type", "STK")).upper()
        if sec_type != "STK":
            continue
        symbol = str(position.get("symbol", ""))
        if symbol:
            symbols.append(symbol)
    return sorted(symbols)


def should_trigger_kill_switch(
    account_equity: float,
    daily_realized_pnl: float,
    connection_healthy: bool,
    broker_positions: List[Dict[str, object]],
    internal_positions: List[Dict[str, object]],
    macro_risk: bool = False,
    stop_integrity_ok: bool = True,
) -> Tuple[bool, List[str]]:
    """Block all trading when safety conditions are breached."""
    reasons: List[str] = []
    if daily_realized_pnl < -(account_equity * SETTINGS.risk.max_daily_loss_pct):
        reasons.append("daily_loss_limit_exceeded")
    if not connection_healthy:
        reasons.append("ibkr_disconnected")
    broker_symbols = _equity_symbols(broker_positions)
    internal_symbols = _equity_symbols(internal_positions)
    if broker_symbols != internal_symbols:
        reasons.append("account_not_synced")
    if macro_risk:
        reasons.append("macro_risk_event_detected")
    if not stop_integrity_ok:
        reasons.append("missing_protective_stop")
    return len(reasons) > 0, reasons
