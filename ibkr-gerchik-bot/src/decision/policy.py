"""Deterministic gates around model choices.

The model can choose an action, but this module owns all quantities, prices,
protective-stop rules and hard vetoes.  It deliberately imports existing risk
functions instead of recreating their formulas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from src.config import SETTINGS
from src.risk.position_size import calculate_position_size, position_value_ok
from src.strategy.validator import validate_trade

from .models import DecisionAction, DecisionCandidate, PositionAction


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...] = tuple()
    quantity: int = 0
    risk_amount: float = 0.0
    action: str = DecisionAction.WAIT.value
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionActionDecision:
    approved: bool
    action: str
    reasons: tuple[str, ...] = tuple()
    quantity: int = 0
    new_stop: float | None = None


class AutonomousRiskGate:
    """Mandatory deterministic entry gate for LLM-owned decisions."""

    def __init__(self, config: Any = None) -> None:
        self.config = config or SETTINGS.decision_agent

    def evaluate_entry(
        self,
        candidate: DecisionCandidate,
        *,
        account_equity: float,
        cash_available: float,
        current_positions: Sequence[Mapping[str, object]],
        open_risk_amount: float,
        spread_pct: float,
        broker_connected: bool,
        account_synced: bool,
        market_open: bool,
        first_unstable_minutes: bool,
        allow_first_unstable_minutes: bool = False,
        symbol_news_risk: bool = False,
        macro_risk: bool = False,
        atr_has_room: bool = True,
        daily_realized_pnl: float = 0.0,
        data_age_seconds: float | None = None,
        current_price: float | None = None,
        setup_still_valid: bool = True,
        isolated_paper: bool = False,
        max_open_positions: int | None = None,
        risk_per_trade_pct: float | None = None,
    ) -> RiskDecision:
        reasons: list[str] = []
        if not setup_still_valid:
            reasons.append("candidate_invalidated")
        if data_age_seconds is None or not math.isfinite(float(data_age_seconds)):
            reasons.append("data_age_unknown")
        elif float(data_age_seconds) > float(self.config.max_data_age_seconds):
            reasons.append("data_stale")
        if current_price is not None:
            try:
                price = float(current_price)
            except (TypeError, ValueError):
                price = 0.0
            if price <= 0 or not math.isfinite(price):
                reasons.append("quote_invalid")
            elif candidate.entry > 0 and abs(price - candidate.entry) / candidate.entry > float(self.config.max_entry_chase_pct):
                reasons.append("price_chase_too_far")

        symbol = candidate.symbol.upper()
        for position in current_positions:
            if str(position.get("symbol", "")).upper() != symbol:
                continue
            owner = str(position.get("owner") or position.get("source") or "GERCHIK_LEGACY").upper()
            reasons.append("symbol_owned_by_legacy" if owner != "LLM_AGENT" else "duplicate_agent_position")

        effective_max_positions = int(
            self.config.max_open_positions if max_open_positions is None else max_open_positions
        )
        if len(current_positions) >= effective_max_positions:
            reasons.append("agent_max_positions_reached")
        if account_equity <= 0:
            reasons.append("invalid_account_equity")
        if daily_loss_exceeded(account_equity, daily_realized_pnl, self.config):
            reasons.append("daily_loss_limit")

        configured_risk_pct = self.config.risk_per_trade_pct if risk_per_trade_pct is None else risk_per_trade_pct
        risk_pct = min(float(SETTINGS.risk.risk_per_trade), float(configured_risk_pct) / 100.0)
        quantity = calculate_position_size(account_equity, risk_pct, candidate.entry, candidate.stop)
        risk_amount = abs(candidate.entry - candidate.stop) * quantity
        order_size_valid = position_value_ok(
            quantity,
            candidate.entry,
            SETTINGS.risk.max_position_value,
            cash_available,
        )
        if risk_amount > account_equity * risk_pct:
            reasons.append("agent_risk_per_trade_exceeded")
        if open_risk_amount + risk_amount > account_equity * SETTINGS.risk.max_open_risk_pct:
            reasons.append("open_risk_limit_exceeded")

        signal = candidate.to_dict()
        signal.update(
            {
                "signal": "BUY" if candidate.direction == "long" else "SELL",
                "quantity": quantity,
                "risk_amount": risk_amount,
                "next_major_level": (
                    candidate.market_context.get("next_major_level")
                    if isinstance(candidate.market_context, Mapping)
                    else None
                ),
            }
        )
        deterministic_valid, validator_reasons = validate_trade(
            signal,
            account_equity=account_equity,
            current_positions=[dict(item) for item in current_positions],
            open_risk_amount=open_risk_amount,
            spread_pct=spread_pct,
            paper_trading=True if isolated_paper else SETTINGS.paper_trading,
            tws_connected=True if isolated_paper else broker_connected,
            account_synced=True if isolated_paper else account_synced,
            market_open=market_open,
            first_unstable_minutes=first_unstable_minutes,
            allow_first_unstable_minutes=allow_first_unstable_minutes,
            has_symbol_news_risk=symbol_news_risk,
            has_macro_risk=macro_risk,
            atr_has_room=atr_has_room,
            order_size_valid=order_size_valid,
            cash_available=cash_available,
        )
        reasons.extend(validator_reasons)
        if not deterministic_valid:
            reasons.append("deterministic_validator_rejected")
        # Stable order and no duplicate explanation codes make audit rows easy to compare.
        unique_reasons = tuple(dict.fromkeys(reasons))
        return RiskDecision(
            approved=not unique_reasons,
            reasons=unique_reasons,
            quantity=quantity,
            risk_amount=risk_amount,
            action=DecisionAction.ENTER.value if not unique_reasons else DecisionAction.REJECT.value,
            metadata={"risk_pct": risk_pct, "symbol": symbol},
        )


def daily_loss_exceeded(account_equity: float, daily_realized_pnl: float, config: Any) -> bool:
    if account_equity <= 0 or float(config.max_daily_r) <= 0:
        return False
    return daily_realized_pnl <= -(account_equity * float(config.max_daily_r) / 100.0)


def evaluate_position_action(
    action: PositionAction | str,
    position: Mapping[str, object],
    *,
    current_price: float,
) -> PositionActionDecision:
    """Validate the small LLM position menu without touching a broker."""
    try:
        resolved = action if isinstance(action, PositionAction) else PositionAction(str(action).upper())
    except ValueError:
        return PositionActionDecision(False, str(action), ("position_action_not_allowed",))
    quantity = max(0, int(float(position.get("quantity", 0) or 0)))
    if quantity <= 0:
        return PositionActionDecision(False, resolved.value, ("position_quantity_invalid",))
    if resolved is PositionAction.HOLD:
        return PositionActionDecision(True, resolved.value, quantity=quantity)
    if resolved is PositionAction.CLOSE:
        return PositionActionDecision(True, resolved.value, quantity=quantity)
    if resolved is PositionAction.TRIM_50:
        trim_quantity = max(1, quantity // 2)
        return PositionActionDecision(True, resolved.value, quantity=trim_quantity)
    if resolved is PositionAction.MOVE_STOP_TO_BREAKEVEN:
        try:
            entry = float(position["entry"])
            current_stop = float(position["stop_loss"])
            price = float(current_price)
        except (KeyError, TypeError, ValueError):
            return PositionActionDecision(False, resolved.value, ("position_prices_invalid",))
        direction = str(position.get("direction") or "long").lower()
        if direction == "long":
            safe = price > entry and entry > current_stop
        else:
            safe = price < entry and entry < current_stop
        if not safe:
            return PositionActionDecision(False, resolved.value, ("breakeven_not_safe",))
        return PositionActionDecision(True, resolved.value, quantity=quantity, new_stop=entry)
    return PositionActionDecision(False, resolved.value, ("position_action_not_allowed",))
