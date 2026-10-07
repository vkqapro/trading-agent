"""Deterministic, read-only CandidateProposal integration for scanner artifacts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from src.risk.order_planner import (
    CapitalConstraints,
    ExecutionCosts,
    OrderPlanRequest,
    plan_order,
)

ELIGIBLE_STRATEGIES = frozenset({"lp1", "lp2", "prb1", "prb2"})


@dataclass(frozen=True)
class ProposalSizingInputs:
    account_capital: float | None = None
    max_capital_allocation_pct: float | None = None
    max_loss_risk_pct: float | None = None
    available_funds: float | None = None
    max_position_value: float | None = None
    slippage_per_share: float = 0.0
    fees_per_share: float = 0.0
    include_execution_costs: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def planner_capital(self) -> CapitalConstraints:
        return CapitalConstraints(
            account_capital=self.account_capital,
            max_capital_allocation_pct=self.max_capital_allocation_pct,
            max_loss_risk_pct=self.max_loss_risk_pct,
            available_funds=self.available_funds,
            max_position_value=self.max_position_value,
        )

    def planner_costs(self) -> ExecutionCosts:
        return ExecutionCosts(
            slippage_per_share=self.slippage_per_share,
            fees_per_share=self.fees_per_share,
            include_in_sizing=self.include_execution_costs,
        )


@dataclass(frozen=True)
class CandidateProposal:
    schema_version: str
    proposal_id: str
    settings_hash: str
    scanner: dict[str, Any]
    symbol: str
    strategy: dict[str, Any]
    setup: dict[str, Any]
    pattern_definition: dict[str, Any]
    pattern_bars: dict[str, Any]
    trigger_evidence: dict[str, Any]
    rejection_evidence: list[Any]
    chart_annotations: list[dict[str, Any]]
    sizing_inputs: dict[str, Any]
    order_plan: dict[str, Any]
    quantity: int
    legacy_scanner_quantity: Any
    proposal_status: str
    # Optional canonical-live fields; historical proposal payloads remain unchanged
    # semantically while sharing the same CandidateProposal contract.
    live_status: str | None = None
    setup_id: str | None = None
    session: dict[str, Any] = field(default_factory=dict)
    live_provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _settings_hash(sizing: ProposalSizingInputs) -> str:
    encoded = json.dumps(sizing.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def build_candidate_proposal(
    scanner_candidate: Mapping[str, Any],
    sizing_inputs: ProposalSizingInputs,
) -> CandidateProposal:
    """Build a proposal using scanner prices verbatim and the canonical planner only."""
    strategy_id = str(scanner_candidate.get("strategy_id", "")).lower()
    status = str(scanner_candidate.get("status", ""))
    direction = str(scanner_candidate.get("direction", "")).upper()
    if status != "ENTRY_SIGNAL" or direction != "LONG" or strategy_id not in ELIGIBLE_STRATEGIES:
        raise ValueError("scanner candidate is not eligible for a LONG ENTRY_SIGNAL proposal")

    trade_plan = scanner_candidate.get("trade_plan") or {}
    level = scanner_candidate.get("level") or {}
    quality = scanner_candidate.get("quality") or {}
    signal = scanner_candidate.get("signal") or {}
    entry = trade_plan.get("entry")
    stop = trade_plan.get("stop")
    target = trade_plan.get("target")
    request = OrderPlanRequest(
        symbol=str(scanner_candidate.get("symbol", "")),
        side="LONG",
        entry=entry,
        stop=stop,
        target=target,
        capital=sizing_inputs.planner_capital(),
        execution_costs=sizing_inputs.planner_costs(),
    )
    order_plan = plan_order(request)
    settings_hash = _settings_hash(sizing_inputs)
    run_id = str(scanner_candidate.get("run_id", ""))
    symbol = str(scanner_candidate.get("symbol", ""))
    proposal_id = f"prop_{run_id}_{symbol}_{strategy_id}_{settings_hash}"
    return CandidateProposal(
        schema_version="1.0",
        proposal_id=proposal_id,
        settings_hash=settings_hash,
        scanner={
            "run_id": run_id,
            "scanner_version": scanner_candidate.get("scanner_version", "0.2.0"),
            "artifact_schema": scanner_candidate.get("schema_version", "1.1"),
        },
        symbol=symbol,
        strategy={
            "strategy_id": strategy_id,
            "direction": direction,
            "signal_status": status,
            "signal_bar": signal.get("signal_bar", signal.get("signal_bar_date")),
            "score": quality.get("score"),
        },
        setup={
            "level": level.get("price"),
            "level_type": level.get("level_type"),
            "entry": entry,
            "stop": stop,
            "target": target,
            "reward_risk": trade_plan.get("reward_risk", trade_plan.get("rr")),
        },
        pattern_definition=dict(scanner_candidate.get("pattern_definition") or {}),
        pattern_bars=dict(scanner_candidate.get("pattern_bars") or {}),
        trigger_evidence=dict(scanner_candidate.get("trigger_evidence") or {}),
        rejection_evidence=list(scanner_candidate.get("rejection_evidence") or []),
        chart_annotations=[dict(item) for item in (scanner_candidate.get("chart_annotations") or [])],
        sizing_inputs=sizing_inputs.to_dict(),
        order_plan=order_plan.to_dict(),
        quantity=order_plan.quantity,
        legacy_scanner_quantity=scanner_candidate.get("quantity"),
        proposal_status=order_plan.status,
    )
