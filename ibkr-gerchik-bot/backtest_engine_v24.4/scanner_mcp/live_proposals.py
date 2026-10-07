"""Pure READY-only integration from canonical live results to CandidateProposal."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .proposals import CandidateProposal, ProposalSizingInputs
from src.risk.order_planner import OrderPlanRequest, plan_order


class LiveProposalError(ValueError):
    """Structured validation failure; no planner call is made for non-ready input."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _settings_hash(sizing: ProposalSizingInputs) -> str:
    payload = json.dumps(sizing.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "to_dict"):
        converted = value.to_dict()
        if isinstance(converted, Mapping):
            return converted
    raise LiveProposalError("INVALID_LIVE_RESULT", "live_result must be a mapping or to_dict-compatible result")


def build_live_candidate_proposal(live_result: Any, sizing_inputs: ProposalSizingInputs) -> CandidateProposal:
    """Build a deterministic proposal only from a fresh canonical READY result.

    All quantity, constraint, risk and reward arithmetic remains in
    ``src.risk.order_planner.plan_order``.
    """
    result = _mapping(live_result)
    status = str(result.get("status", ""))
    if status != "READY":
        code = "STALE_SESSION" if status == "STALE_DATA" else "NOT_READY_FOR_PROPOSAL"
        raise LiveProposalError(code, f"live result status {status or 'MISSING'} cannot produce a proposal")
    setup = _mapping(result.get("setup"))
    session = _mapping(result.get("session"))
    setup_id = str(setup.get("setup_id") or result.get("setup_id") or "")
    if not setup_id:
        raise LiveProposalError("INVALID_LIVE_RESULT", "READY result is missing setup_id")
    freshness = str(session.get("freshness") or session.get("freshness_state") or "").upper()
    if freshness not in {"FRESH", "DELAYED_USABLE"}:
        raise LiveProposalError("STALE_SESSION", "READY proposal requires FRESH or DELAYED_USABLE current-session data")
    if bool(session.get("complete", False)):
        raise LiveProposalError("INVALID_SESSION", "READY proposal requires an incomplete current session")

    planned = _mapping(setup.get("planned_trade"))
    live_plan = result.get("live_plan")
    live_plan = _mapping(live_plan) if live_plan is not None else {}
    entry = live_plan.get("entry", planned.get("entry"))
    stop = live_plan.get("stop", planned.get("stop"))
    target = live_plan.get("target", planned.get("target"))
    plan_hash = str(live_plan.get("plan_hash") or planned.get("plan_hash") or setup.get("provenance", {}).get("plan_hash") or "legacy")
    symbol = str(result.get("symbol") or setup.get("symbol") or "")
    strategy_id = str(result.get("strategy_id") or setup.get("strategy_id") or "").lower()
    if not symbol or not strategy_id or any(value is None for value in (entry, stop, target)):
        raise LiveProposalError("INVALID_LIVE_RESULT", "READY result is missing symbol, strategy, or complete trade plan")

    request = OrderPlanRequest(
        symbol=symbol,
        side="LONG",
        entry=float(entry),
        stop=float(stop),
        target=float(target),
        capital=sizing_inputs.planner_capital(),
        execution_costs=sizing_inputs.planner_costs(),
    )
    order_plan = plan_order(request)
    settings_hash = _settings_hash(sizing_inputs)
    session_date = str(session.get("session_date") or "unknown")
    proposal_id = f"prop_live_{setup_id}_{session_date}_{plan_hash}_{settings_hash}"
    trigger = _mapping(result.get("trigger"))
    level = _mapping(setup.get("level"))
    provenance = dict(result.get("provenance") or {})
    provenance.update({"source": "canonical_live_engine", "setup_id": setup_id, "settings_hash": settings_hash, "market_data_freshness_status": freshness, "market_data_age_seconds": session.get("freshness_seconds"), "session_as_of": session.get("as_of"), "execution_revalidation_required": True})
    if freshness == "DELAYED_USABLE": provenance["execution_revalidation_reason"] = "DELAYED_MARKET_DATA"
    return CandidateProposal(
        schema_version="1.0",
        proposal_id=proposal_id,
        settings_hash=settings_hash,
        scanner={"source": "canonical_live_engine", "artifact_schema": "1.0"},
        symbol=symbol,
        strategy={"strategy_id": strategy_id, "direction": str(result.get("direction") or setup.get("direction") or "LONG"), "signal_status": "READY"},
        setup={"setup_id": setup_id, "level": level.get("price"), "level_type": level.get("type"), "entry": entry, "stop": stop, "target": target, "plan_hash": plan_hash, "market_data_freshness_status": freshness, "market_data_age_seconds": session.get("freshness_seconds"), "session_as_of": session.get("as_of"), "execution_revalidation_required": True}, 
        pattern_definition={"source": "canonical_live_engine", "strategy_id": strategy_id},
        pattern_bars=dict(setup.get("pattern_bars") or {}),
        trigger_evidence=dict(trigger.get("trigger_evidence") or {}),
        rejection_evidence=list(trigger.get("rejection_evidence") or []),
        chart_annotations=[],
        sizing_inputs=sizing_inputs.to_dict(),
        order_plan=order_plan.to_dict(),
        quantity=order_plan.quantity,
        legacy_scanner_quantity=None,
        proposal_status=order_plan.status,
        live_status="READY",
        setup_id=setup_id,
        session=dict(session),
        live_provenance=provenance,
    )
