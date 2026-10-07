"""Durable SQLite audit trail for autonomous decisions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import DecisionCandidate, DecisionRequest, DecisionResponse, sanitize_mapping, sanitize_text


SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    status TEXT NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    direction TEXT NOT NULL,
    candidate_json TEXT NOT NULL,
    source_signal_id TEXT
);
CREATE TABLE IF NOT EXISTS candidate_revisions (
    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    candidate_json TEXT NOT NULL,
    scanner_run_id TEXT,
    recorded_at TEXT NOT NULL,
    UNIQUE(candidate_id, plan_hash),
    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
);
CREATE TABLE IF NOT EXISTS decision_snapshots (
    decision_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
);
CREATE TABLE IF NOT EXISTS model_decisions (
    decision_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    run_id TEXT,
    agent_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    chosen_action TEXT,
    confidence REAL,
    ranked_actions_json TEXT,
    reason_codes_json TEXT,
    summary TEXT,
    latency_ms REAL,
    token_usage_json TEXT,
    estimated_cost REAL,
    error TEXT,
    prompt_version TEXT,
    strategy_source TEXT,
    definition_version TEXT,
    definition_hash TEXT,
    parameter_snapshot_json TEXT,
    prompt_id TEXT,
    prompt_hash TEXT,
    applicability_json TEXT,
    compiled_prompt_json TEXT,
    allowed_actions_json TEXT,
    prompt_payload_json TEXT,
    provider_status TEXT,
    provider_error_category TEXT,
    provider_finish_reason TEXT,
    raw_model_text_sanitized TEXT,
    parsed_response_json TEXT,
    model_action TEXT,
    effective_action TEXT,
    decision_origin TEXT,
    system_result_json TEXT,
    requested_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
);
CREATE TABLE IF NOT EXISTS risk_decisions (
    risk_id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    approved INTEGER NOT NULL,
    reasons_json TEXT NOT NULL,
    quantity INTEGER,
    risk_amount REAL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_links (
    execution_id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_key TEXT,
    reservation_id TEXT,
    mode TEXT NOT NULL DEFAULT 'unknown',
    agent_id TEXT NOT NULL DEFAULT 'unknown',
    decision_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    status TEXT NOT NULL,
    order_ids_json TEXT,
    position_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_reservations (
    execution_key TEXT PRIMARY KEY,
    reservation_id TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    account_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry REAL NOT NULL,
    stop REAL NOT NULL,
    target REAL NOT NULL,
    quantity INTEGER,
    order_fingerprint TEXT,
    decision_id TEXT,
    order_ref TEXT,
    order_ids_json TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
);
CREATE TABLE IF NOT EXISTS position_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT NOT NULL,
    candidate_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    position_id TEXT,
    final_status TEXT NOT NULL,
    final_r REAL,
    final_pnl REAL,
    closed_at TEXT,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_health (
    health_id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms REAL,
    error TEXT,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_decisions_candidate ON model_decisions(candidate_id);
CREATE INDEX IF NOT EXISTS idx_model_decisions_completed ON model_decisions(completed_at);
CREATE INDEX IF NOT EXISTS idx_risk_decisions_decision ON risk_decisions(decision_id);
CREATE INDEX IF NOT EXISTS idx_execution_links_candidate ON execution_links(candidate_id);
CREATE INDEX IF NOT EXISTS idx_execution_reservations_candidate ON execution_reservations(candidate_id);
CREATE INDEX IF NOT EXISTS idx_execution_reservations_state ON execution_reservations(state);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class ReservationResult:
    acquired: bool
    execution_key: str
    reservation_id: str
    state: str
    reason: str | None = None


class DecisionAudit:
    """Small SQLite repository; all writes are explicit and queryable."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            existing = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(execution_links)").fetchall()
            }
            for name, definition in (
                ("execution_key", "TEXT"),
                ("reservation_id", "TEXT"),
                ("mode", "TEXT NOT NULL DEFAULT 'unknown'"),
                ("agent_id", "TEXT NOT NULL DEFAULT 'unknown'"),
            ):
                if name not in existing:
                    connection.execute(f"ALTER TABLE execution_links ADD COLUMN {name} {definition}")
            decision_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(model_decisions)").fetchall()
            }
            for name, definition in (
                ("run_id", "TEXT"),
                ("prompt_version", "TEXT"),
                ("strategy_source", "TEXT"),
                ("definition_version", "TEXT"),
                ("definition_hash", "TEXT"),
                ("parameter_snapshot_json", "TEXT"),
                ("prompt_id", "TEXT"),
                ("prompt_hash", "TEXT"),
                ("applicability_json", "TEXT"),
                ("compiled_prompt_json", "TEXT"),
                ("allowed_actions_json", "TEXT"),
                ("prompt_payload_json", "TEXT"),
                ("provider_status", "TEXT"),
                ("provider_error_category", "TEXT"),
                ("provider_finish_reason", "TEXT"),
                ("raw_model_text_sanitized", "TEXT"),
                ("parsed_response_json", "TEXT"),
                ("model_action", "TEXT"),
                ("effective_action", "TEXT"),
                ("decision_origin", "TEXT"),
                ("system_result_json", "TEXT"),
            ):
                if name not in decision_columns:
                    connection.execute(f"ALTER TABLE model_decisions ADD COLUMN {name} {definition}")

    def record_candidate_revision(
        self,
        candidate: DecisionCandidate,
        plan_hash: str,
        *,
        scanner_run_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Persist one immutable canonical setup revision in DecisionAudit."""
        owns = connection is None
        connection = connection or self.connection()
        try:
            connection.execute(
                """INSERT INTO candidate_revisions
                (candidate_id, plan_hash, candidate_json, scanner_run_id, recorded_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id, plan_hash) DO NOTHING""",
                (
                    candidate.candidate_id,
                    str(plan_hash),
                    _json(candidate.to_dict()),
                    scanner_run_id,
                    _now(),
                ),
            )
            if owns:
                connection.commit()
        finally:
            if owns:
                connection.close()

    def record_candidate(self, candidate: DecisionCandidate, *, connection: sqlite3.Connection | None = None) -> None:
        owns = connection is None
        connection = connection or self.connection()
        try:
            connection.execute(
                """INSERT INTO candidates
                (candidate_id, created_at, asset_class, symbol, strategy, direction,
                 candidate_json, source_signal_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET candidate_json=excluded.candidate_json""",
                (
                    candidate.candidate_id,
                    candidate.created_at.isoformat(),
                    candidate.asset_class,
                    candidate.symbol,
                    candidate.strategy,
                    candidate.direction,
                    _json(candidate.to_dict()),
                    candidate.source_signal_id,
                ),
            )
            if owns:
                connection.commit()
        finally:
            if owns:
                connection.close()

    def record_snapshot(self, request: DecisionRequest, *, connection: sqlite3.Connection | None = None) -> None:
        owns = connection is None
        connection = connection or self.connection()
        try:
            self.record_candidate(request.snapshot.candidate, connection=connection)
            payload = request.snapshot.to_dict()
            encoded = _json(payload)
            connection.execute(
                """INSERT INTO decision_snapshots
                (decision_id, candidate_id, snapshot_json, snapshot_hash, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET snapshot_json=excluded.snapshot_json,
                snapshot_hash=excluded.snapshot_hash""",
                (
                    request.decision_id,
                    request.snapshot.candidate.candidate_id,
                    encoded,
                    hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                    _now(),
                ),
            )
            if owns:
                connection.commit()
        finally:
            if owns:
                connection.close()

    def record_decision(
        self,
        request: DecisionRequest,
        response: DecisionResponse | None = None,
        *,
        latency_ms: float | None = None,
        token_usage: Mapping[str, object] | None = None,
        estimated_cost: float | None = None,
        error: str | None = None,
        status: str | None = None,
        provider_observation: Mapping[str, object] | None = None,
        model_action: str | None = None,
        effective_action: str | None = None,
        decision_origin: str | None = None,
        system_result: Mapping[str, object] | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        owns = connection is None
        connection = connection or self.connection()
        try:
            self.record_snapshot(request, connection=connection)
            resolved_status = status or ("completed" if response is not None else "failed")
            observation = dict(provider_observation or {})
            resolved_model_action = model_action or (None if response is None else response.action.value)
            resolved_effective_action = effective_action or resolved_model_action or ("NO_ACTION" if response is None else None)
            resolved_origin = decision_origin or ("MODEL" if response is not None else "PROVIDER_FAILURE")
            prompt_payload = observation.get("request_payload")
            parsed_response = observation.get("parsed_response_json")
            connection.execute(
                """INSERT INTO model_decisions
                (decision_id, candidate_id, run_id, agent_id, provider, model, mode, status,
                 chosen_action, confidence, ranked_actions_json, reason_codes_json,
                 summary, latency_ms, token_usage_json, estimated_cost, error,
                 prompt_version, strategy_source, definition_version, definition_hash,
                 parameter_snapshot_json, prompt_id, prompt_hash, applicability_json,
                 compiled_prompt_json, allowed_actions_json, prompt_payload_json,
                 provider_status, provider_error_category, provider_finish_reason, raw_model_text_sanitized,
                 parsed_response_json, model_action, effective_action, decision_origin,
                 system_result_json, requested_at, completed_at)
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(decision_id) DO UPDATE SET status=excluded.status,
                chosen_action=excluded.chosen_action, confidence=excluded.confidence,
                ranked_actions_json=excluded.ranked_actions_json,
                reason_codes_json=excluded.reason_codes_json, summary=excluded.summary,
                latency_ms=excluded.latency_ms, token_usage_json=excluded.token_usage_json,
                estimated_cost=excluded.estimated_cost, error=excluded.error,
                prompt_version=COALESCE(excluded.prompt_version, model_decisions.prompt_version),
                strategy_source=COALESCE(excluded.strategy_source, model_decisions.strategy_source),
                definition_version=COALESCE(excluded.definition_version, model_decisions.definition_version),
                definition_hash=COALESCE(excluded.definition_hash, model_decisions.definition_hash),
                parameter_snapshot_json=COALESCE(excluded.parameter_snapshot_json, model_decisions.parameter_snapshot_json),
                prompt_id=COALESCE(excluded.prompt_id, model_decisions.prompt_id),
                prompt_hash=COALESCE(excluded.prompt_hash, model_decisions.prompt_hash),
                applicability_json=COALESCE(excluded.applicability_json, model_decisions.applicability_json),
                compiled_prompt_json=COALESCE(excluded.compiled_prompt_json, model_decisions.compiled_prompt_json),
                allowed_actions_json=COALESCE(excluded.allowed_actions_json, model_decisions.allowed_actions_json),
                prompt_payload_json=COALESCE(excluded.prompt_payload_json, model_decisions.prompt_payload_json),
                provider_status=COALESCE(excluded.provider_status, model_decisions.provider_status),
                provider_error_category=COALESCE(excluded.provider_error_category, model_decisions.provider_error_category),
                provider_finish_reason=COALESCE(excluded.provider_finish_reason, model_decisions.provider_finish_reason),
                raw_model_text_sanitized=COALESCE(excluded.raw_model_text_sanitized, model_decisions.raw_model_text_sanitized),
                parsed_response_json=COALESCE(excluded.parsed_response_json, model_decisions.parsed_response_json),
                model_action=COALESCE(excluded.model_action, model_decisions.model_action),
                effective_action=COALESCE(excluded.effective_action, model_decisions.effective_action),
                decision_origin=COALESCE(excluded.decision_origin, model_decisions.decision_origin),
                system_result_json=COALESCE(excluded.system_result_json, model_decisions.system_result_json),
                completed_at=excluded.completed_at""",
                (
                    request.decision_id,
                    request.snapshot.candidate.candidate_id,
                    request.run_id,
                    request.agent_id,
                    request.provider,
                    request.model,
                    request.mode.value,
                    resolved_status,
                    None if response is None else response.action.value,
                    None if response is None else response.confidence,
                    None if response is None else _json(response.ranked_actions),
                    None if response is None else _json(response.reason_codes),
                    None if response is None else response.summary,
                    latency_ms,
                    _json(token_usage or {}),
                    estimated_cost,
                    error,
                    request.prompt_version,
                    request.strategy_source,
                    request.definition_version,
                    request.definition_hash,
                    _json(sanitize_mapping(request.parameter_snapshot or {})),
                    request.prompt_id,
                    request.prompt_hash,
                    _json(sanitize_mapping(request.applicability or {})),
                    _json(sanitize_mapping(request.compiled_prompt or {})),
                    _json(list(request.allowed_actions)),
                    _json(sanitize_mapping(prompt_payload)) if prompt_payload is not None else None,
                    str(observation.get("provider_status") or ("SUCCESS" if response is not None else "FAILED")),
                    str(observation.get("provider_error_category") or "") or None,
                    str(observation.get("finish_reason") or "") or None,
                    sanitize_text(observation.get("raw_model_text_sanitized")) if observation.get("raw_model_text_sanitized") is not None else None,
                    _json(sanitize_mapping(parsed_response)) if parsed_response is not None else None,
                    resolved_model_action,
                    resolved_effective_action,
                    resolved_origin,
                    _json(sanitize_mapping(system_result or {})),
                    request.requested_at.isoformat(),
                    _now(),
                ),
            )
            if owns:
                connection.commit()
        finally:
            if owns:
                connection.close()

    def update_decision_outcome(
        self,
        *,
        decision_id: str,
        effective_action: str,
        decision_origin: str,
        system_result: Mapping[str, object] | None = None,
    ) -> None:
        """Persist downstream policy outcome without overwriting model output."""
        with self.connection() as connection:
            connection.execute(
                """UPDATE model_decisions
                   SET effective_action = ?, decision_origin = ?, system_result_json = ?
                   WHERE decision_id = ?""",
                (
                    str(effective_action),
                    str(decision_origin),
                    _json(sanitize_mapping(system_result or {})),
                    str(decision_id),
                ),
            )

    def record_risk(
        self,
        *,
        decision_id: str,
        candidate_id: str,
        approved: bool,
        reasons: Iterable[str],
        quantity: int | None = None,
        risk_amount: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        owns = connection is None
        connection = connection or self.connection()
        try:
            connection.execute(
                "INSERT INTO risk_decisions (decision_id, candidate_id, approved, reasons_json, quantity, risk_amount, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (decision_id, candidate_id, int(approved), _json(list(reasons)), quantity, risk_amount, _now()),
            )
            if owns:
                connection.commit()
        finally:
            if owns:
                connection.close()

    def record_execution(
        self,
        *,
        decision_id: str,
        candidate_id: str,
        status: str,
        order_ids: Mapping[str, object] | None = None,
        position_id: str | None = None,
        mode: str = "unknown",
        agent_id: str = "unknown",
        reservation_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        owns = connection is None
        connection = connection or self.connection()
        try:
            connection.execute(
                "INSERT INTO execution_links (execution_key, reservation_id, mode, agent_id, decision_id, candidate_id, status, order_ids_json, position_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (None, reservation_id, mode, agent_id, decision_id, candidate_id, status, _json(order_ids or {}), position_id, _now()),
            )
            if reservation_id:
                reservation_state = {
                    "paper_simulated": "PAPER_SIMULATED",
                    "live_executed": "SUBMITTED",
                    "ibkr_paper_executed": "SUBMITTED",
                    "live_rejected": "FAILED_PRE_SUBMIT",
                    "ibkr_paper_rejected": "FAILED_PRE_SUBMIT",
                    "paper_rejected": "FAILED_PRE_SUBMIT",
                }.get(status, status.upper())
                connection.execute(
                    "UPDATE execution_reservations SET state = ?, order_ids_json = ?, updated_at = ? WHERE reservation_id = ?",
                    (reservation_state, _json(order_ids or {}), _now(), reservation_id),
                )
            if owns:
                connection.commit()
        finally:
            if owns:
                connection.close()

    def record_position_event(
        self,
        *,
        position_id: str,
        event_type: str,
        payload: Mapping[str, object],
        candidate_id: str | None = None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO position_events (position_id, candidate_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (position_id, candidate_id, event_type, _json(payload), _now()),
            )

    def record_outcome(
        self,
        *,
        candidate_id: str,
        final_status: str,
        final_r: float | None = None,
        final_pnl: float | None = None,
        position_id: str | None = None,
        payload: Mapping[str, object] | None = None,
        closed_at: str | None = None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO outcomes (candidate_id, position_id, final_status, final_r, final_pnl, closed_at, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (candidate_id, position_id, final_status, final_r, final_pnl, closed_at or _now(), _json(payload or {})),
            )

    def record_provider_health(
        self,
        *,
        provider: str,
        model: str,
        status: str,
        latency_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO provider_health (provider, model, status, latency_ms, error, observed_at) VALUES (?, ?, ?, ?, ?, ?)",
                (provider, model, status, latency_ms, error, _now()),
            )

    def list_decisions(
        self,
        *,
        symbol: str | None = None,
        action: str | None = None,
        provider: str | None = None,
        mode: str | None = None,
        run_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, object]]:
        clauses: list[str] = []
        values: list[object] = []
        if symbol:
            clauses.append("c.symbol = ?")
            values.append(symbol.upper())
        if action:
            clauses.append("m.chosen_action = ?")
            values.append(action.upper())
        if provider:
            clauses.append("m.provider = ?")
            values.append(provider.lower())
        if mode:
            clauses.append("m.mode = ?")
            values.append(mode.lower())
        if run_id:
            clauses.append("m.run_id = ?")
            values.append(str(run_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 1000)))
        query = f"""
            SELECT m.decision_id, m.candidate_id, m.run_id, m.agent_id, m.provider, m.model, m.mode,
                   m.status, m.chosen_action AS action, m.confidence, m.reason_codes_json,
                   m.summary, m.latency_ms, m.error, m.requested_at, m.completed_at,
                   m.model_action, m.effective_action, m.decision_origin,
                   m.provider_status, m.provider_error_category,
                   m.provider_finish_reason,
                   c.symbol, c.strategy, c.asset_class, c.direction, c.candidate_json,
                   r.approved AS risk_approved, r.reasons_json AS risk_reasons,
                   e.status AS execution_status, e.mode AS execution_mode,
                   e.agent_id AS execution_agent_id, e.order_ids_json, e.position_id
            FROM model_decisions m
            JOIN candidates c ON c.candidate_id = m.candidate_id
            LEFT JOIN risk_decisions r ON r.risk_id = (
                SELECT MAX(r2.risk_id) FROM risk_decisions r2 WHERE r2.decision_id = m.decision_id
            )
            LEFT JOIN execution_links e ON e.execution_id = (
                SELECT MAX(e2.execution_id) FROM execution_links e2 WHERE e2.decision_id = m.decision_id
            )
            {where}
            ORDER BY m.completed_at DESC LIMIT ?
        """
        with self.connection() as connection:
            rows = connection.execute(query, values).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _decode_json(value: object, default: object) -> object:
        if value in (None, ""):
            return default
        try:
            return sanitize_mapping(json.loads(str(value)))
        except (TypeError, ValueError, json.JSONDecodeError):
            return default

    @staticmethod
    def _sanitize_inspector(value: object) -> object:
        """Sanitize both sensitive keys and account-like strings for the UI."""
        if isinstance(value, Mapping):
            return {
                str(key): DecisionAudit._sanitize_inspector(item)
                for key, item in value.items()
                if (
                    str(key).strip().lower() in {"token_usage_json", "parameter_snapshot_json"}
                    or str(key).strip().lower().endswith("_tokens")
                    or not any(part in str(key).strip().lower() for part in ("key", "secret", "token", "password", "credential", "account_number", "account_id", "passphrase"))
                )
            }
        if isinstance(value, (list, tuple)):
            return [DecisionAudit._sanitize_inspector(item) for item in value]
        if isinstance(value, str):
            return sanitize_text(value)
        return value

    def get_decision(self, decision_id: str) -> dict[str, object] | None:
        """Return one sanitized, read-only inspector record."""
        with self.connection() as connection:
            row = connection.execute(
                """SELECT m.*, c.symbol, c.strategy, c.asset_class, c.direction,
                          c.candidate_json, s.snapshot_json,
                          r.approved AS risk_approved, r.reasons_json AS risk_reasons,
                          r.quantity AS risk_quantity, r.risk_amount,
                          e.status AS execution_status, e.mode AS execution_mode,
                          e.agent_id AS execution_agent_id, e.order_ids_json, e.position_id
                   FROM model_decisions m
                   JOIN candidates c ON c.candidate_id = m.candidate_id
                   LEFT JOIN decision_snapshots s ON s.decision_id = m.decision_id
                   LEFT JOIN risk_decisions r ON r.risk_id = (
                       SELECT MAX(r2.risk_id) FROM risk_decisions r2 WHERE r2.decision_id = m.decision_id
                   )
                   LEFT JOIN execution_links e ON e.execution_id = (
                       SELECT MAX(e2.execution_id) FROM execution_links e2 WHERE e2.decision_id = m.decision_id
                   )
                   WHERE m.decision_id = ?""",
                (str(decision_id),),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in (
            "candidate_json",
            "snapshot_json",
            "token_usage_json",
            "parameter_snapshot_json",
            "applicability_json",
            "compiled_prompt_json",
            "allowed_actions_json",
            "prompt_payload_json",
            "parsed_response_json",
            "ranked_actions_json",
            "reason_codes_json",
            "risk_reasons",
            "order_ids_json",
            "system_result_json",
        ):
            result[key] = self._decode_json(result.get(key), {} if key not in {"allowed_actions_json", "ranked_actions_json", "reason_codes_json", "risk_reasons"} else [])
        result["raw_model_text_sanitized"] = sanitize_text(result.get("raw_model_text_sanitized") or "") or None
        result.pop("account_id", None)
        return self._sanitize_inspector(result)

    def list_decisions_for_run(self, run_id: str, *, limit: int = 1000) -> list[dict[str, object]]:
        return self.list_decisions(run_id=run_id, limit=limit)

    def status(self) -> dict[str, object]:
        tables = (
            "candidates",
            "model_decisions",
            "risk_decisions",
            "execution_links",
            "execution_reservations",
            "position_events",
            "outcomes",
        )
        with self.connection() as connection:
            counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}
            mode_rows = connection.execute(
                "SELECT mode, COUNT(*) AS count FROM execution_links GROUP BY mode"
            ).fetchall()
            decision_rows = connection.execute(
                "SELECT mode, COUNT(*) AS count FROM model_decisions GROUP BY mode"
            ).fetchall()
        executions_by_mode = {str(row["mode"]): int(row["count"]) for row in mode_rows}
        decisions_by_mode = {str(row["mode"]): int(row["count"]) for row in decision_rows}
        return {
            "database": str(self.path),
            **counts,
            "decisions": counts["model_decisions"],
            "shadow_decisions": decisions_by_mode.get("shadow", 0),
            "internal_paper_executions": executions_by_mode.get("paper_autonomous", 0),
            "ibkr_paper_executions": executions_by_mode.get("ibkr_paper_autonomous", 0),
            "live_executions": executions_by_mode.get("live_autonomous", 0),
            "executions_by_mode": executions_by_mode,
        }

    def count_executions_since(
        self,
        *,
        mode: str,
        agent_id: str,
        since: str,
    ) -> int:
        """Count successful broker/simulated execution links after an ISO boundary."""
        with self.connection() as connection:
            row = connection.execute(
                """SELECT COUNT(*) FROM execution_links
                   WHERE mode = ? AND agent_id = ? AND created_at >= ?
                     AND status IN ('paper_simulated', 'live_executed',
                                    'ibkr_paper_executed', 'submitted', 'filled', 'open')""",
                (str(mode).lower(), agent_id, since),
            ).fetchone()
        return int(row[0] or 0)

    def reserve_execution(
        self,
        *,
        mode: str,
        agent_id: str,
        candidate_id: str,
        account_id: str | None,
        symbol: str,
        side: str,
        entry: float,
        stop: float,
        target: float,
        decision_id: str | None = None,
        order_fingerprint: str | None = None,
        order_ref: str | None = None,
    ) -> ReservationResult:
        """Atomically claim one executable identity using SQLite locking."""
        from uuid import uuid4

        key = "|".join(
            [str(mode).lower(), str(agent_id), str(candidate_id), str(account_id or "")]
        )
        reservation_id = f"reservation-{uuid4().hex}"
        now = _now()
        connection = self.connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO execution_reservations
                (execution_key, reservation_id, mode, agent_id, candidate_id, account_id,
                 symbol, side, entry, stop, target, quantity, order_fingerprint,
                 decision_id, order_ref, order_ids_json, state, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, '{}', 'RESERVED', NULL, ?, ?)""",
                (
                    key,
                    reservation_id,
                    str(mode).lower(),
                    agent_id,
                    candidate_id,
                    account_id,
                    symbol.upper(),
                    side.upper(),
                    float(entry),
                    float(stop),
                    float(target),
                    order_fingerprint,
                    decision_id,
                    order_ref,
                    now,
                    now,
                ),
            )
            connection.commit()
            return ReservationResult(True, key, reservation_id, "RESERVED")
        except sqlite3.IntegrityError:
            connection.rollback()
            row = connection.execute(
                "SELECT reservation_id, state FROM execution_reservations WHERE execution_key = ?",
                (key,),
            ).fetchone()
            return ReservationResult(
                False,
                key,
                str(row[0]) if row else "",
                str(row[1]) if row else "UNKNOWN",
                "execution_identity_already_reserved",
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update_reservation(
        self,
        reservation_id: str,
        *,
        state: str,
        decision_id: str | None = None,
        quantity: int | None = None,
        order_fingerprint: str | None = None,
        order_ref: str | None = None,
        order_ids: Mapping[str, object] | None = None,
        error: str | None = None,
    ) -> None:
        fields = ["state = ?", "updated_at = ?"]
        values: list[object] = [state.upper(), _now()]
        for column, value in (
            ("decision_id", decision_id),
            ("quantity", quantity),
            ("order_fingerprint", order_fingerprint),
            ("order_ref", order_ref),
            ("error", error),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)
        if order_ids is not None:
            fields.append("order_ids_json = ?")
            values.append(_json(order_ids))
        values.append(reservation_id)
        with self.connection() as connection:
            connection.execute(
                f"UPDATE execution_reservations SET {', '.join(fields)} WHERE reservation_id = ?",
                values,
            )

    def reservation(self, reservation_id: str) -> dict[str, object] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM execution_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
        return dict(row) if row else None

    def incomplete_reservations(self, *, mode: str) -> list[dict[str, object]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM execution_reservations WHERE mode = ? AND state IN ('RESERVED', 'READY_TO_SUBMIT', 'SUBMITTING', 'PAPER_MUTATING', 'RECONCILIATION_REQUIRED') ORDER BY created_at",
                (str(mode).lower(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def provider_health_is_recent(self, *, provider: str, model: str, max_age_seconds: float) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT observed_at FROM provider_health WHERE provider = ? AND model = ? AND status = 'ok' ORDER BY health_id DESC LIMIT 1",
                (provider, model),
            ).fetchone()
        if not row:
            return False
        try:
            observed = datetime.fromisoformat(str(row[0]))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds() <= max_age_seconds
        except (TypeError, ValueError):
            return False

    def has_active_execution(
        self,
        candidate_id: str,
        *,
        mode: str | None = None,
        agent_id: str | None = None,
        account_id: str | None = None,
    ) -> bool:
        active_states = (
            "RESERVED",
            "READY_TO_SUBMIT",
            "SUBMITTING",
            "SUBMITTED",
            "FILLED",
            "OPEN",
            "PAPER_SIMULATED",
        )
        placeholders = ",".join("?" for _ in active_states)
        values: list[object] = [candidate_id, *active_states]
        query = f"SELECT 1 FROM execution_reservations WHERE candidate_id = ? AND state IN ({placeholders})"
        if mode is not None:
            query += " AND mode = ?"
            values.append(str(mode).lower())
        if agent_id is not None:
            query += " AND agent_id = ?"
            values.append(agent_id)
        if account_id is not None:
            query += " AND COALESCE(account_id, '') = ?"
            values.append(account_id)
        query += " LIMIT 1"
        with self.connection() as connection:
            row = connection.execute(query, values).fetchone()
            if row:
                return True
            if mode is None:
                legacy = connection.execute(
                    "SELECT 1 FROM execution_links WHERE candidate_id = ? AND status IN ('paper_simulated', 'submitted', 'executed', 'filled', 'open') LIMIT 1",
                    (candidate_id,),
                ).fetchone()
                return legacy is not None
        return False

    def performance(self) -> dict[str, object]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total, SUM(CASE WHEN final_status = 'CLOSED' THEN 1 ELSE 0 END) AS closed, SUM(final_pnl) AS pnl, SUM(final_r) AS r FROM outcomes"
            ).fetchone()
        return {
            "total_outcomes": int(row["total"] or 0),
            "closed_outcomes": int(row["closed"] or 0),
            "pnl": float(row["pnl"] or 0.0),
            "r": float(row["r"] or 0.0),
        }

    def provider_health(self, *, limit: int = 100) -> list[dict[str, object]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT provider, model, status, latency_ms, error, observed_at FROM provider_health ORDER BY health_id DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [dict(row) for row in rows]
