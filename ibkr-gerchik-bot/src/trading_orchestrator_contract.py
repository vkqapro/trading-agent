"""Pure validation and reconciliation for Trading Orchestrator A2A replies."""

from __future__ import annotations

import re
from typing import Any


UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


class ContractError(ValueError):
    """A specialist response cannot be used for readiness reconciliation."""


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{name} must be an object")
    return value


def _nonempty(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ContractError(f"{name} is required")
    return text


def _count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{name} must be a non-negative integer")
    return value


def _scan_id(value: Any) -> str:
    scan_id = _nonempty(value, "scan_id")
    if not UUID_RE.fullmatch(scan_id):
        raise ContractError("scan_id must be a full UUID")
    return scan_id


def validate_ops(payload: Any) -> dict[str, Any]:
    data = _mapping(payload, "Ops response")
    status = _nonempty(data.get("status"), "Ops status").upper()
    if status not in {"HEALTHY", "READY", "OK"}:
        raise ContractError(f"Ops preflight is not healthy: {status}")
    _mapping(data.get("component_health"), "component_health")
    _mapping(data.get("broker_readiness"), "broker_readiness")
    _nonempty(data.get("timestamp"), "timestamp")
    if not isinstance(data.get("warnings", []), list):
        raise ContractError("warnings must be a list")
    return data


def _candidate_identity(row: Any, *, section: str, index: int) -> tuple[str, str, str, int]:
    data = _mapping(row, f"{section}[{index}]")
    identity = data.get("candidate_identity") if isinstance(data.get("candidate_identity"), dict) else data
    expected_section = "active" if section in {"active", "active_reviews"} else "terminal"
    actual_section = str(identity.get("section") or expected_section).strip().lower()
    if actual_section != expected_section:
        raise ContractError(f"{section}[{index}].section does not match its response section")
    symbol = _nonempty(identity.get("symbol"), f"{section}[{index}].symbol").upper()
    ordinal = identity.get("signal_ordinal")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal <= 0:
        raise ContractError(f"{section}[{index}].signal_ordinal must be positive")
    return expected_section, symbol, str(ordinal), ordinal


def _validate_candidates(rows: Any, section: str) -> list[tuple[str, str, str, int]]:
    if not isinstance(rows, list):
        raise ContractError(f"{section} must be a list")
    identities = [_candidate_identity(row, section=section, index=index) for index, row in enumerate(rows)]
    if len(set(identities)) != len(identities):
        raise ContractError(f"{section} contains duplicate candidate identities")
    return identities


def validate_daily(payload: Any) -> dict[str, Any]:
    data = _mapping(payload, "Daily Trader response")
    if str(data.get("scan_status") or "").upper() != "COMPLETED":
        raise ContractError("Daily Trader scan is not completed")
    _scan_id(data.get("scan_id"))
    _nonempty(data.get("radar_as_of"), "radar_as_of")
    _nonempty(data.get("scan_timestamp"), "scan_timestamp")
    scanner = _mapping(data.get("scanner"), "scanner")
    _count(scanner.get("requested"), "scanner.requested")
    _count(scanner.get("completed"), "scanner.completed")
    _count(scanner.get("failed"), "scanner.failed")
    _validate_candidates(data.get("active"), "active")
    _validate_candidates(data.get("terminal"), "terminal")
    if not isinstance(data.get("warnings", []), list):
        raise ContractError("warnings must be a list")
    return data


def validate_reviewer(payload: Any, *, scan_id: str) -> dict[str, Any]:
    data = _mapping(payload, "Reviewer response")
    expected = _scan_id(scan_id)
    if _scan_id(data.get("scan_id")) != expected:
        raise ContractError("Reviewer scan_id does not match Daily Trader scan_id")
    active = _validate_candidates(data.get("active_reviews"), "active_reviews")
    _validate_candidates(data.get("terminal_reviews"), "terminal_reviews")
    for field in ("orders_submitted", "approvals_created", "execution_intents_created"):
        if _count(data.get(field), field) != 0:
            raise ContractError(f"Reviewer side-effect counter is non-zero: {field}")
    if not isinstance(data.get("warnings", []), list):
        raise ContractError("warnings must be a list")
    return {**data, "_active_identities": active}


def reconcile_workflow(ops: Any, daily: Any, reviewer: Any) -> dict[str, Any]:
    """Return a deterministic readiness summary or an explicit incomplete result."""
    try:
        ops_data = validate_ops(ops)
        daily_data = validate_daily(daily)
        scan_id = _scan_id(daily_data["scan_id"])
        reviewer_data = validate_reviewer(reviewer, scan_id=scan_id)
        active = _validate_candidates(daily_data["active"], "active")
        terminal = _validate_candidates(daily_data["terminal"], "terminal")
        reviewer_active = reviewer_data["_active_identities"]
        reviewer_terminal = _validate_candidates(reviewer_data["terminal_reviews"], "terminal_reviews")
        if len(active) != len(reviewer_active):
            raise ContractError("Reviewer active candidate count does not match Daily Trader")
        if set(active) != set(reviewer_active):
            raise ContractError("Reviewer active candidate identities do not match Daily Trader")
        if len(terminal) != len(reviewer_terminal):
            raise ContractError("Reviewer terminal candidate count does not match Daily Trader")
        if set(terminal) != set(reviewer_terminal):
            raise ContractError("Reviewer terminal candidate identities do not match Daily Trader")
        return {
            "status": "READY",
            "scan_id": scan_id,
            "ops_status": str(ops_data["status"]).upper(),
            "active_candidates": len(active),
            "terminal_candidates": len(terminal),
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
            "warnings": [*ops_data.get("warnings", []), *daily_data.get("warnings", []), *reviewer_data.get("warnings", [])],
        }
    except ContractError as exc:
        return {
            "status": "INCOMPLETE",
            "reason": str(exc),
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
        }
