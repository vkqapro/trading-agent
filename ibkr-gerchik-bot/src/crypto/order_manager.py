"""OKX crypto order planning and safe simulated execution.

Crypto orders intentionally reuse the same risk limits as the stock bot:
``SETTINGS.risk`` is loaded from the existing .env variables such as
RISK_PER_TRADE, MAX_DAILY_LOSS, MAX_OPEN_POSITIONS, MAX_OPEN_RISK,
MIN_REWARD_RISK, and MAX_POSITION_VALUE.

Live OKX submission is still disabled here until the bracket-order path is
explicitly tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from src.config import SETTINGS


@dataclass(frozen=True)
class CryptoOrderRequest:
    symbol: str
    side: str
    entry: float
    stop: float
    target: float | None = None
    quantity: float | None = None
    account_equity: float = 0.0
    cash_available: float | None = None
    open_risk_amount: float = 0.0
    current_positions_count: int = 0
    daily_realized_pnl: float = 0.0


def stock_risk_settings() -> Dict[str, float | int]:
    """Expose the shared stock risk settings used for crypto sizing."""
    risk = SETTINGS.risk
    return {
        "risk_per_trade": risk.risk_per_trade,
        "max_daily_loss_pct": risk.max_daily_loss_pct,
        "max_positions": risk.max_positions,
        "max_open_risk_pct": risk.max_open_risk_pct,
        "min_reward_risk_ratio": risk.min_reward_risk_ratio,
        "max_spread_pct": risk.max_spread_pct,
        "max_position_value": risk.max_position_value,
    }


def _reward_risk(side: str, entry: float, stop: float, target: float | None) -> float:
    if target is None:
        return 0.0
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    side = side.upper()
    reward = (target - entry) if side == "BUY" else (entry - target)
    return max(0.0, reward / risk)


def _round_crypto_quantity(quantity: float) -> float:
    """Use a conservative generic precision until per-instrument lot sizes are wired."""
    if quantity <= 0:
        return 0.0
    return float(f"{quantity:.8f}")


class CryptoOrderManager:
    def plan_order(self, request: CryptoOrderRequest) -> dict:
        side = request.side.upper().strip()
        entry = float(request.entry or 0.0)
        stop = float(request.stop or 0.0)
        target = None if request.target is None else float(request.target)
        equity = float(request.account_equity or 0.0)
        cash_available = None if request.cash_available is None else float(request.cash_available)
        open_risk_amount = float(request.open_risk_amount or 0.0)
        daily_realized_pnl = float(request.daily_realized_pnl or 0.0)
        risk_settings = stock_risk_settings()
        reasons: List[str] = []

        if side not in {"BUY", "SELL"} or entry <= 0 or stop <= 0:
            reasons.append("invalid_setup")
        if side == "BUY" and stop >= entry:
            reasons.append("buy_stop_must_be_below_entry")
        if side == "SELL" and stop <= entry:
            reasons.append("sell_stop_must_be_above_entry")
        if equity <= 0:
            reasons.append("missing_account_equity")

        risk_per_unit = abs(entry - stop)
        reward_risk = _reward_risk(side, entry, stop, target)
        if target is not None and reward_risk < float(risk_settings["min_reward_risk_ratio"]):
            reasons.append("reward_risk_too_low")

        max_daily_loss = equity * float(risk_settings["max_daily_loss_pct"])
        if equity > 0 and daily_realized_pnl < -max_daily_loss:
            reasons.append("daily_loss_limit_exceeded")

        if request.current_positions_count >= int(risk_settings["max_positions"]):
            reasons.append("max_positions_reached")

        risk_budget = equity * float(risk_settings["risk_per_trade"])
        max_position_value = float(risk_settings["max_position_value"])
        max_open_risk = equity * float(risk_settings["max_open_risk_pct"])

        if request.quantity is not None and float(request.quantity) > 0:
            quantity = float(request.quantity)
        elif risk_per_unit > 0 and risk_budget > 0:
            quantity = risk_budget / risk_per_unit
        else:
            quantity = 0.0

        caps = [quantity]
        if entry > 0 and max_position_value > 0:
            caps.append(max_position_value / entry)
        if cash_available is not None and cash_available > 0 and entry > 0:
            caps.append(cash_available / entry)
        quantity = _round_crypto_quantity(min(caps) if caps else 0.0)

        notional = quantity * entry
        risk_amount = quantity * risk_per_unit
        if quantity <= 0:
            reasons.append("quantity_zero")
        if max_position_value > 0 and notional > max_position_value:
            reasons.append("max_position_value_exceeded")
        if cash_available is not None and cash_available > 0 and notional > cash_available:
            reasons.append("insufficient_cash")
        if equity > 0 and open_risk_amount + risk_amount > max_open_risk:
            reasons.append("open_risk_limit_exceeded")

        ok = not reasons
        return {
            "ok": ok,
            "status": "planned" if ok else "rejected",
            "reasons": reasons,
            "symbol": request.symbol.upper().strip(),
            "side": side,
            "entry": entry,
            "stop": stop,
            "target": target,
            "quantity": quantity,
            "position_value": round(notional, 2),
            "risk_amount": round(risk_amount, 2),
            "reward_risk": round(reward_risk, 2),
            "risk_budget": round(risk_budget, 2),
            "max_open_risk": round(max_open_risk, 2),
            "max_position_value": round(max_position_value, 2),
            "uses_stock_risk_settings": True,
            "risk_settings": risk_settings,
        }

    def place_order(self, request: CryptoOrderRequest, *, live: bool = False) -> dict:
        plan = self.plan_order(request)
        if not plan["ok"]:
            return plan
        if live:
            raise RuntimeError("Live OKX orders are not enabled yet. Verify simulated trading first.")
        return {"ok": True, "status": "simulated", "paper": True, "plan": plan}
