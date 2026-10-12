"""Read-only deterministic pre-trade review over Daily Trader authority.

This module never scans, calculates risk, creates approvals, or calls a broker.
It consumes the already assembled Daily Trader symbol payload and classifies it
for human review only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException

router = APIRouter()


REVIEW_ELIGIBLE = "ELIGIBLE_FOR_APPROVAL"
REVIEW_CAUTION = "REVIEW_WITH_CAUTION"
REVIEW_NOT_ELIGIBLE = "NOT_ELIGIBLE_FOR_APPROVAL"

TERMINAL_LIFECYCLES = {"INVALIDATED", "EXPIRED", "AMBIGUOUS_INTRABAR_ORDER", "NO_SETUP"}
REVIEWABLE_LIFECYCLES = {"PENDING_ENTRY", "ENTRY_TRIGGERED", "READY"}


@dataclass(frozen=True)
class ReviewResult:
    symbol: str
    strategy: str | None
    side: str | None
    lifecycle_status: str
    lifecycle_reason: str | None
    review_status: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    candidate: dict[str, Any] | None
    read_only: bool
    orders_submitted: int
    approval_created: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "side": self.side,
            "lifecycle_status": self.lifecycle_status,
            "lifecycle_reason": self.lifecycle_reason,
            "review_status": self.review_status,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "candidate": self.candidate,
            "read_only": self.read_only,
            "orders_submitted": self.orders_submitted,
            "approval_created": self.approval_created,
        }


def _missing(candidate: Mapping[str, Any]) -> list[str]:
    required = ("symbol", "strategy", "side", "lifecycle_status", "planned_entry", "stop", "target")
    return [name for name in required if candidate.get(name) in (None, "")]


def review_symbol_payload(payload: Mapping[str, Any]) -> list[ReviewResult]:
    """Classify candidates from one Daily Trader symbol response.

    The scanner/lifecycle payload is authoritative. No values are recomputed.
    """
    symbol = str(payload.get("symbol") or "").strip().upper()
    read_only = payload.get("read_only") is True
    orders_submitted = int(payload.get("orders_submitted") or 0)
    common_safe = {"read_only": read_only, "orders_submitted": orders_submitted}
    results: list[ReviewResult] = []

    for item in payload.get("active") or []:
        scanner = dict(item.get("scanner") or {})
        radar = item.get("radar")
        candidate = {
            **scanner,
            "symbol": symbol,
            "radar": radar,
            "market_status": payload.get("market_status"),
            **common_safe,
        }
        lifecycle = str(scanner.get("lifecycle_status") or "UNKNOWN").upper()
        results.append(_classify(candidate, lifecycle))

    for item in payload.get("terminal") or []:
        scanner = dict(item.get("scanner") or {})
        candidate = {
            **scanner,
            "symbol": symbol,
            "radar": item.get("radar"),
            "market_status": payload.get("market_status"),
            **common_safe,
        }
        lifecycle = str(scanner.get("lifecycle_status") or "UNKNOWN").upper()
        results.append(_classify(candidate, lifecycle))

    if payload.get("no_setup") or (not results and str(payload.get("scanner_status") or "").upper() == "NO_SETUP"):
        results.append(
            ReviewResult(
                symbol=symbol,
                strategy=None,
                side=None,
                lifecycle_status="NO_SETUP",
                lifecycle_reason="no_production_setup",
                review_status=REVIEW_NOT_ELIGIBLE,
                reasons=("No production setup exists.",),
                warnings=(),
                candidate=None,
                **common_safe,
            )
        )
    return results


def _classify(candidate: dict[str, Any], lifecycle: str) -> ReviewResult:
    symbol = str(candidate.get("symbol") or "").upper()
    strategy = candidate.get("strategy")
    side = candidate.get("side")
    warnings: list[str] = []
    reasons: list[str] = []
    hard_reasons: list[str] = []

    if lifecycle in TERMINAL_LIFECYCLES:
        return ReviewResult(symbol, strategy, side, lifecycle, candidate.get("lifecycle_reason"), REVIEW_NOT_ELIGIBLE,
                            ("This setup is terminal and no longer active.",), (), candidate,
                            candidate["read_only"], candidate["orders_submitted"])
    if candidate["read_only"] is not True:
        hard_reasons.append("Safety contract read_only=true is not satisfied.")
    if candidate["orders_submitted"] != 0:
        hard_reasons.append("Safety contract orders_submitted=0 is not satisfied.")
    missing = _missing(candidate)
    if missing:
        hard_reasons.append("Missing authoritative setup fields: " + ", ".join(missing) + ".")
    if lifecycle not in REVIEWABLE_LIFECYCLES:
        hard_reasons.append(f"Lifecycle {lifecycle or 'UNKNOWN'} is not a reviewable production lifecycle.")

    if candidate.get("direction_conflict") is True:
        warnings.append("Counter-trend setup: scanner direction conflicts with Radar context.")
    if candidate.get("data_quality_complete") is False:
        missing_quality = candidate.get("missing_quality_data") or []
        suffix = ": " + ", ".join(map(str, missing_quality)) if missing_quality else ""
        warnings.append("DATA QUALITY PARTIAL" + suffix)
    if candidate.get("market_status") != "OPEN":
        warnings.append(f"Market status is {candidate.get('market_status') or 'UNKNOWN'}.")
    if lifecycle == "PENDING_ENTRY":
        reasons.append("Entry has not triggered yet.")

    status = REVIEW_NOT_ELIGIBLE if hard_reasons else (REVIEW_CAUTION if warnings else REVIEW_ELIGIBLE)
    reasons = hard_reasons or ["Active production setup supplied by the authoritative scanner."]
    if lifecycle == "PENDING_ENTRY":
        reasons.append("Entry has not triggered yet.")
    return ReviewResult(symbol, strategy, side, lifecycle, candidate.get("lifecycle_reason"), status,
                        tuple(reasons), tuple(warnings), candidate, candidate["read_only"], candidate["orders_submitted"])


def review_batch_payload(payloads: list[Mapping[str, Any]]) -> list[ReviewResult]:
    results: list[ReviewResult] = []
    for payload in payloads:
        results.extend(review_symbol_payload(payload))
    return results


@router.get("/api/pre-trade-review/{symbol}")
def pre_trade_review_route(symbol: str):
    from dashboard_react.server import _daily_trader_service
    try:
        source = _daily_trader_service().symbol(symbol)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400 if isinstance(exc, ValueError) else 503, str(exc)) from exc
    return {"symbol": str(symbol).strip().upper(), "reviews": [item.to_dict() for item in review_symbol_payload(source)], "read_only": source.get("read_only") is True, "orders_submitted": int(source.get("orders_submitted") or 0), "approval_created": False}


@router.post("/api/pre-trade-review/batch")
def pre_trade_review_batch_route(body: dict):
    from dashboard_react.server import _daily_trader_service
    if not isinstance(body, dict) or not isinstance(body.get("symbols"), list):
        raise HTTPException(400, "Batch Pre-Trade Review requires a symbols list")
    symbols = [str(item).strip().upper() for item in body["symbols"] if str(item).strip()]
    if not symbols or len(symbols) > 100:
        raise HTTPException(400, "Batch Pre-Trade Review requires 1 to 100 symbols")
    sources = []
    for symbol in dict.fromkeys(symbols):
        try:
            sources.append(_daily_trader_service().symbol(symbol))
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
    return {"requested": len(symbols), "reviews": [item.to_dict() for item in review_batch_payload(sources)], "read_only": all(item.get("read_only") is True for item in sources), "orders_submitted": sum(int(item.get("orders_submitted") or 0) for item in sources), "approval_created": False}


@router.get("/api/pre-trade-review/health")
def pre_trade_review_health_route():
    from dashboard_react.server import _daily_trader_service
    health = _daily_trader_service().health()
    return {"status": health.get("status"), "read_only": health.get("read_only") is True, "orders_submitted": int(health.get("orders_submitted") or 0), "approval_created": False, "reviewer": "pre-trade-reviewer"}
