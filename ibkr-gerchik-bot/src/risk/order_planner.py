"""Pure canonical order planning for preview and proposal generation.

This module deliberately has no broker, filesystem, network, queue, or runtime
configuration dependencies.  Callers provide all capital and execution-cost
assumptions explicitly.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class PlanStatus(str, Enum):
    ACTIONABLE = "ACTIONABLE"
    NON_ACTIONABLE = "NON_ACTIONABLE"
    INVALID = "INVALID"


@dataclass(frozen=True)
class CapitalConstraints:
    """Independent capital and loss-budget inputs, expressed as percentages."""

    account_capital: float | None = None
    max_capital_allocation_pct: float | None = None
    max_loss_risk_pct: float | None = None
    available_funds: float | None = None
    max_position_value: float | None = None


@dataclass(frozen=True)
class ExecutionCosts:
    """Per-share costs; disabled costs are represented explicitly."""

    slippage_per_share: float = 0.0
    fees_per_share: float = 0.0
    include_in_sizing: bool = False

    @property
    def total_per_share(self) -> float:
        return self.slippage_per_share + self.fees_per_share


@dataclass(frozen=True)
class OrderPlanRequest:
    symbol: str
    side: str
    entry: float
    stop: float
    target: float
    capital: CapitalConstraints
    execution_costs: ExecutionCosts = field(default_factory=ExecutionCosts)
    asset_class: str = "STOCK"
    quantity_precision: str = "WHOLE_SHARES"

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OrderPlanRequest":
        """Build a request while rejecting unknown or missing top-level fields."""
        required = {"symbol", "side", "entry", "stop", "target", "capital"}
        allowed = required | {"execution_costs", "asset_class", "quantity_precision"}
        unknown = sorted(set(payload) - allowed)
        missing = sorted(required - set(payload))
        if unknown or missing:
            details = []
            if missing:
                details.append(f"missing fields: {', '.join(missing)}")
            if unknown:
                details.append(f"unknown fields: {', '.join(unknown)}")
            raise ValueError("; ".join(details))
        capital_raw = payload["capital"]
        if not isinstance(capital_raw, Mapping):
            raise ValueError("capital must be an object")
        capital_allowed = {f.name for f in CapitalConstraints.__dataclass_fields__.values()}
        capital_unknown = sorted(set(capital_raw) - capital_allowed)
        if capital_unknown:
            raise ValueError(f"unknown capital fields: {', '.join(capital_unknown)}")
        costs_raw = payload.get("execution_costs", {})
        if not isinstance(costs_raw, Mapping):
            raise ValueError("execution_costs must be an object")
        costs_allowed = {f.name for f in ExecutionCosts.__dataclass_fields__.values()}
        costs_unknown = sorted(set(costs_raw) - costs_allowed)
        if costs_unknown:
            raise ValueError(f"unknown execution_costs fields: {', '.join(costs_unknown)}")
        return cls(
            symbol=payload["symbol"], side=payload["side"], entry=payload["entry"],
            stop=payload["stop"], target=payload["target"],
            capital=CapitalConstraints(**capital_raw),
            execution_costs=ExecutionCosts(**costs_raw),
            asset_class=payload.get("asset_class", "STOCK"),
            quantity_precision=payload.get("quantity_precision", "WHOLE_SHARES"),
        )


@dataclass(frozen=True)
class CanonicalOrderPlan:
    schema_version: str
    status: str
    symbol: str
    side: str
    entry: float
    stop: float
    target: float
    quantity: int
    position_value: float
    risk: dict[str, Any]
    reward: dict[str, Any]
    rr: float | None
    allocation: dict[str, Any]
    quantity_constraints: dict[str, int | None]
    binding_constraint: str | None
    warnings: list[str]
    reasons: list[dict[str, str]]
    validation_errors: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _error(code: str, message: str, field: str) -> dict[str, str]:
    return {"code": code, "message": message, "field": field}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _floor_ratio(numerator: float, denominator: float) -> int:
    return max(0, math.floor(numerator / denominator))


def plan_order(request: OrderPlanRequest) -> CanonicalOrderPlan:
    """Return a deterministic, side-effect-free order plan."""
    errors: list[dict[str, str]] = []
    if not isinstance(request.symbol, str) or not request.symbol.strip():
        errors.append(_error("INVALID_SYMBOL", "symbol must be a non-empty string", "symbol"))
    side = str(request.side).upper() if isinstance(request.side, str) else ""
    if side not in {"BUY", "LONG"}:
        errors.append(_error("UNSUPPORTED_SIDE", "v1 supports LONG/BUY only", "side"))
    if request.quantity_precision != "WHOLE_SHARES":
        errors.append(_error("UNSUPPORTED_QUANTITY_PRECISION", "v1 supports WHOLE_SHARES only", "quantity_precision"))
    if str(request.asset_class).upper() != "STOCK":
        errors.append(_error("UNSUPPORTED_ASSET_CLASS", "v1 supports STOCK only", "asset_class"))
    for name, value in (("entry", request.entry), ("stop", request.stop), ("target", request.target)):
        if not _finite(value) or float(value) <= 0:
            errors.append(_error("INVALID_PRICE", f"{name} must be finite and positive", name))
    if _finite(request.entry) and _finite(request.stop) and request.stop >= request.entry:
        errors.append(_error("INVALID_STOP", "LONG stop must be below entry", "stop"))
    if _finite(request.entry) and _finite(request.target) and request.target <= request.entry:
        errors.append(_error("INVALID_TARGET", "LONG target must be above entry", "target"))

    capital = request.capital
    capital_values = {
        "account_capital": capital.account_capital,
        "max_capital_allocation_pct": capital.max_capital_allocation_pct,
        "max_loss_risk_pct": capital.max_loss_risk_pct,
        "available_funds": capital.available_funds,
        "max_position_value": capital.max_position_value,
    }
    for name, value in capital_values.items():
        if value is not None and not _finite(value):
            errors.append(_error("NON_FINITE_VALUE", f"{name} must be finite", f"capital.{name}"))
    if capital.account_capital is not None and _finite(capital.account_capital) and capital.account_capital <= 0:
        errors.append(_error("INVALID_CAPITAL", "account_capital must be positive", "capital.account_capital"))
    for name in ("max_capital_allocation_pct", "max_loss_risk_pct"):
        value = getattr(capital, name)
        if value is not None and _finite(value) and (value < 0 or value > 100):
            errors.append(_error("INVALID_PERCENTAGE", f"{name} must be between 0 and 100", f"capital.{name}"))
    for name in ("available_funds", "max_position_value"):
        value = getattr(capital, name)
        if value is not None and _finite(value) and value < 0:
            errors.append(_error("NEGATIVE_LIMIT", f"{name} cannot be negative", f"capital.{name}"))
    if capital.max_capital_allocation_pct is not None and capital.account_capital is None:
        errors.append(_error("MISSING_CAPITAL", "account_capital is required for allocation sizing", "capital.account_capital"))
    if capital.max_loss_risk_pct is not None and capital.account_capital is None:
        errors.append(_error("MISSING_CAPITAL", "account_capital is required for loss-risk sizing", "capital.account_capital"))

    costs = request.execution_costs
    for name, value in (("slippage_per_share", costs.slippage_per_share), ("fees_per_share", costs.fees_per_share)):
        if not _finite(value) or value < 0:
            errors.append(_error("INVALID_EXECUTION_COST", f"{name} must be finite and non-negative", f"execution_costs.{name}"))
    if errors:
        return _invalid_plan(request, side, errors)

    entry, stop, target = float(request.entry), float(request.stop), float(request.target)
    risk_per_share = entry - stop
    reward_per_share = target - entry
    execution_cost = costs.total_per_share
    sizing_risk = risk_per_share + execution_cost if costs.include_in_sizing else risk_per_share
    constraints: dict[str, int | None] = {
        "capital_allocation": None,
        "loss_risk": None,
        "available_funds": None,
        "max_position_value": None,
    }
    allocation_budget = None
    max_allocated = None
    if capital.max_capital_allocation_pct is not None:
        allocation_budget = capital.account_capital * capital.max_capital_allocation_pct / 100
        max_allocated = allocation_budget
        constraints["capital_allocation"] = _floor_ratio(allocation_budget, entry)
    if capital.max_loss_risk_pct is not None:
        loss_budget = capital.account_capital * capital.max_loss_risk_pct / 100
        constraints["loss_risk"] = _floor_ratio(loss_budget, sizing_risk)
    else:
        loss_budget = None
    if capital.available_funds is not None:
        constraints["available_funds"] = _floor_ratio(capital.available_funds, entry)
    if capital.max_position_value is not None:
        constraints["max_position_value"] = _floor_ratio(capital.max_position_value, entry)
    enabled = [(name, value) for name, value in constraints.items() if value is not None]
    if not enabled:
        return _invalid_plan(request, side, [_error("MISSING_CONSTRAINT", "at least one sizing constraint must be provided", "capital")])
    quantity = min(value for _, value in enabled)
    binding = next(name for name, value in enabled if value == quantity)
    status = PlanStatus.ACTIONABLE.value if quantity > 0 else PlanStatus.NON_ACTIONABLE.value
    reasons = [] if quantity > 0 else [{"code": "ZERO_QUANTITY", "message": "all enabled constraints allow zero shares"}]
    position_value = quantity * entry
    structural_max_loss = quantity * risk_per_share
    modeled_max_loss = quantity * sizing_risk
    return CanonicalOrderPlan(
        schema_version="1.0", status=status, symbol=request.symbol, side=side,
        entry=request.entry, stop=request.stop, target=request.target, quantity=quantity,
        position_value=position_value,
        risk={"risk_per_share": risk_per_share, "execution_cost_per_share": execution_cost,
              "slippage_per_share": costs.slippage_per_share, "fees_per_share": costs.fees_per_share,
              "execution_costs_included": costs.include_in_sizing, "sizing_risk_per_share": sizing_risk,
              "max_loss_before_costs": structural_max_loss, "modeled_max_loss": modeled_max_loss,
              "loss_risk_budget": loss_budget},
        reward={"reward_per_share": reward_per_share, "potential_profit_before_costs": quantity * reward_per_share},
        rr=reward_per_share / risk_per_share,
        allocation={"account_capital": capital.account_capital, "max_capital_allocation_pct": capital.max_capital_allocation_pct,
                    "max_allocated_capital": max_allocated, "actual_position_pct": (position_value / capital.account_capital * 100 if capital.account_capital else None)},
        quantity_constraints=constraints, binding_constraint=binding, warnings=[], reasons=reasons, validation_errors=[])


def _invalid_plan(request: OrderPlanRequest, side: str, errors: list[dict[str, str]]) -> CanonicalOrderPlan:
    return CanonicalOrderPlan(
        schema_version="1.0", status=PlanStatus.INVALID.value, symbol=request.symbol, side=side,
        entry=request.entry, stop=request.stop, target=request.target, quantity=0, position_value=0.0,
        risk={}, reward={}, rr=None, allocation={}, quantity_constraints={"capital_allocation": None, "loss_risk": None, "available_funds": None, "max_position_value": None},
        binding_constraint=None, warnings=[], reasons=[], validation_errors=errors)


# Friendly aliases for callers that prefer a verb matching other domain modules.
calculate_order_plan = plan_order
CanonicalPlannerRequest = OrderPlanRequest
