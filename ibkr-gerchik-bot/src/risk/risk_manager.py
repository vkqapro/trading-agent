"""Portfolio risk tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from src.config import SETTINGS


@dataclass
class RiskSnapshot:
    daily_realized_pnl: float = 0.0
    open_risk_amount: float = 0.0
    blocked: bool = False
    reasons: List[str] = field(default_factory=list)


class RiskManager:
    """Track realized loss and open portfolio risk."""

    def __init__(self, account_equity: float) -> None:
        self.account_equity = account_equity
        self.snapshot = RiskSnapshot()

    def update_daily_pnl(self, realized_pnl: float) -> None:
        self.snapshot.daily_realized_pnl = realized_pnl
        self._evaluate_limits()

    def update_open_risk(self, positions: List[Dict[str, float]]) -> None:
        open_risk = 0.0
        for position in positions:
            quantity = abs(float(position.get("quantity", 0.0)))
            entry = float(position.get("entry", 0.0))
            stop = float(position.get("stop_loss", 0.0))
            open_risk += abs(entry - stop) * quantity
        self.snapshot.open_risk_amount = open_risk
        self._evaluate_limits()

    def can_take_new_trade(self) -> bool:
        self._evaluate_limits()
        return not self.snapshot.blocked

    def get_state(self) -> Dict[str, object]:
        self._evaluate_limits()
        return {
            "daily_realized_pnl": self.snapshot.daily_realized_pnl,
            "open_risk_amount": self.snapshot.open_risk_amount,
            "blocked": self.snapshot.blocked,
            "reasons": list(self.snapshot.reasons),
        }

    def _evaluate_limits(self) -> None:
        reasons: List[str] = []
        if self.snapshot.daily_realized_pnl < -(self.account_equity * SETTINGS.risk.max_daily_loss_pct):
            reasons.append("daily_loss_limit_exceeded")
        if self.snapshot.open_risk_amount > self.account_equity * SETTINGS.risk.max_open_risk_pct:
            reasons.append("open_risk_limit_exceeded")
        self.snapshot.reasons = reasons
        self.snapshot.blocked = bool(reasons)
