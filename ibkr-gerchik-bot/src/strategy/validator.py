"""Trade validation rules."""

from __future__ import annotations

from typing import Dict, List, Tuple

from src.config import SETTINGS


def _reward_risk_ratio(entry: float, stop_loss: float, target: float) -> float:
    risk = abs(entry - stop_loss)
    reward = abs(target - entry)
    return reward / risk if risk else 0.0


def validate_trade(
    signal: Dict[str, object],
    account_equity: float,
    current_positions: List[Dict[str, object]],
    open_risk_amount: float,
    spread_pct: float,
) -> Tuple[bool, List[str]]:
    """Validate a trade candidate against hard portfolio rules."""
    reasons: List[str] = []
    symbol = str(signal.get("symbol", ""))
    entry = float(signal.get("entry", 0.0))
    stop_loss = float(signal.get("stop_loss", 0.0))
    target = float(signal.get("target", 0.0))
    risk_amount = float(signal.get("risk_amount", 0.0))

    if len(current_positions) >= SETTINGS.risk.max_positions:
        reasons.append("max_positions_reached")
    if risk_amount > account_equity * SETTINGS.risk.risk_per_trade:
        reasons.append("risk_per_trade_exceeded")
    if not stop_loss:
        reasons.append("missing_stop_loss")
    if _reward_risk_ratio(entry, stop_loss, target) < SETTINGS.risk.min_reward_risk_ratio:
        reasons.append("reward_risk_too_low")
    if spread_pct > SETTINGS.risk.max_spread_pct:
        reasons.append("spread_too_wide")
    if any(position.get("symbol") == symbol for position in current_positions):
        reasons.append("duplicate_position")
    if open_risk_amount + risk_amount > account_equity * SETTINGS.risk.max_open_risk_pct:
        reasons.append("open_risk_limit_exceeded")

    return len(reasons) == 0, reasons
