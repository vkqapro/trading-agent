"""Persistent, read-only orchestration for the AI Trade Desk full scan."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
import builtins
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import HTTPException, Request


STAGES = ("RADAR_REFRESH", "BREAKOUTS", "FRESH_FLIPS", "RADAR_SETUPS", "DISCOVERY_UNIVERSE", "PRODUCTION_SCANNER", "PROPOSALS")
DISCOVERY_BATCH_SIZE = 100
DISCOVERY_MAX_SYMBOLS = 400
DISCOVERY_DEFAULT_MAX_SYMBOLS = 25
DISCOVERY_BATCH_TIMEOUT_SECONDS = 300.0


def _server_pipeline(scan_id: str, stage: Callable[[str, Mapping[str, Any]], None], cancelled: Callable[[], bool]) -> Mapping[str, Any]:
    """Fallback name for the live dashboard process during hot-reload handoff."""
    from dashboard_react import server
    return server._run_ai_trade_desk_pipeline(scan_id, stage, cancelled)


setattr(builtins, "_run_ai_trade_desk_pipeline", _server_pipeline)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AITradeDeskScanManager:
    """Run a bounded pipeline once at a time and persist every stage result."""

    def __init__(self, database_path: str | Path, pipeline: Callable[[str, Callable[[str, Mapping[str, Any]], None], Callable[[], bool]], Mapping[str, Any]]):
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.pipeline = pipeline
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-trade-desk-scan")
        self.lock = threading.Lock()
        self._ensure_schema()

    def _connection(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def _ensure_schema(self) -> None:
        with self._connection() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS ai_trade_desk_scans (
                scan_id TEXT PRIMARY KEY, job_type TEXT NOT NULL DEFAULT 'FULL_MARKET_SCAN', parent_scan_id TEXT,
                status TEXT NOT NULL, stage TEXT NOT NULL,
                stage_results_json TEXT NOT NULL, result_json TEXT, error_json TEXT,
                parameters_json TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0, started_at TEXT,
                completed_at TEXT, updated_at TEXT NOT NULL
            )""")
            columns = {row[1] for row in con.execute("PRAGMA table_info(ai_trade_desk_scans)").fetchall()}
            if "job_type" not in columns:
                con.execute("ALTER TABLE ai_trade_desk_scans ADD COLUMN job_type TEXT NOT NULL DEFAULT 'FULL_MARKET_SCAN'")
            if "parent_scan_id" not in columns:
                con.execute("ALTER TABLE ai_trade_desk_scans ADD COLUMN parent_scan_id TEXT")
            if "parameters_json" not in columns:
                con.execute("ALTER TABLE ai_trade_desk_scans ADD COLUMN parameters_json TEXT")
            con.execute("CREATE INDEX IF NOT EXISTS idx_ai_trade_desk_scans_updated ON ai_trade_desk_scans(updated_at)")

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        for key in ("stage_results_json", "result_json", "error_json", "parameters_json"):
            raw = item.pop(key, None)
            try:
                item[key.removesuffix("_json")] = json.loads(raw) if raw else None
            except (TypeError, ValueError):
                item[key.removesuffix("_json")] = None
        item["cancel_requested"] = bool(item["cancel_requested"])
        return item

    def _get(self, scan_id: str) -> dict[str, Any] | None:
        with self._connection() as con:
            return self._decode(con.execute("SELECT * FROM ai_trade_desk_scans WHERE scan_id=?", (scan_id,)).fetchone())

    def latest(self) -> dict[str, Any] | None:
        with self._connection() as con:
            return self._decode(con.execute("SELECT * FROM ai_trade_desk_scans ORDER BY updated_at DESC LIMIT 1").fetchone())

    def submit(self, *, job_type: str = "FULL_MARKET_SCAN", parent_scan_id: str | None = None, parameters: Mapping[str, Any] | None = None) -> dict[str, Any]:
        normalized_type = str(job_type or "FULL_MARKET_SCAN").strip().upper()
        if normalized_type not in {"FULL_MARKET_SCAN", "FULL_MARKET_DISCOVERY", "GPT_REVIEW", "FULL_AI_SCAN"}:
            raise ValueError("unsupported AI Trade Desk job type")
        normalized_parameters = dict(parameters or {})
        if normalized_type == "FULL_MARKET_DISCOVERY":
            raw_max_symbols = normalized_parameters.get("max_symbols", DISCOVERY_DEFAULT_MAX_SYMBOLS)
            if isinstance(raw_max_symbols, bool):
                raise ValueError("max_symbols must be a positive integer")
            try:
                max_symbols = int(raw_max_symbols)
            except (TypeError, ValueError) as exc:
                raise ValueError("max_symbols must be a positive integer") from exc
            if max_symbols < 1 or max_symbols > DISCOVERY_MAX_SYMBOLS:
                raise ValueError(f"max_symbols must be between 1 and {DISCOVERY_MAX_SYMBOLS}")
            normalized_parameters = {"max_symbols": max_symbols}
        else:
            normalized_parameters = {}
        with self.lock:
            current = self.latest()
            if current and current["status"] in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}:
                return current
            scan_id = "aitd-scan-" + uuid.uuid4().hex
            now = _now()
            with self._connection() as con:
                con.execute("INSERT INTO ai_trade_desk_scans(scan_id,job_type,parent_scan_id,status,stage,stage_results_json,parameters_json,updated_at) VALUES(?,?,?,?,?,?,?,?)", (scan_id, normalized_type, parent_scan_id, "QUEUED", "QUEUED", "{}", json.dumps(normalized_parameters, sort_keys=True), now))
            self.executor.submit(self._run, scan_id)
            return self._get(scan_id) or {"scan_id": scan_id, "status": "QUEUED", "stage": "QUEUED"}

    def cancel(self, scan_id: str) -> dict[str, Any] | None:
        now = _now()
        with self._connection() as con:
            con.execute("UPDATE ai_trade_desk_scans SET cancel_requested=1,status=CASE WHEN status IN ('QUEUED','RUNNING') THEN 'CANCEL_REQUESTED' ELSE status END,updated_at=? WHERE scan_id=?", (now, scan_id))
        return self._get(scan_id)

    def status(self, scan_id: str) -> dict[str, Any] | None:
        return self._get(scan_id)

    def reconcile_review_job(self, scan_id: str, *, proposal_store: Any) -> dict[str, Any]:
        """Recompute a GPT review aggregate from persisted, source-backed evidence."""
        job = self._get(scan_id)
        if job is None:
            raise KeyError("review_job_not_found")
        if str(job.get("job_type") or "").upper() != "GPT_REVIEW":
            raise ValueError("review_job_required")
        parent_id = str(job.get("parent_scan_id") or "").strip()
        parent = self._get(parent_id) if parent_id else None
        if parent is None:
            raise ValueError("parent_market_scan_not_found")
        proposal_stage = (parent.get("stage_results") or {}).get("PROPOSALS") or {}
        expected_ids = [str(item) for item in proposal_stage.get("proposal_ids") or [] if str(item).strip()]
        checkpoint = _review_checkpoint(scan_id, job, parent, include_completed=True, proposal_store=proposal_store)
        by_proposal_id = {str(item.get("proposal_id") or ""): item for item in checkpoint if item.get("proposal_id")}
        evidence: list[dict[str, Any]] = []
        for proposal_id in expected_ids:
            proposal = proposal_store.get(proposal_id)
            candidate = by_proposal_id.get(proposal_id)
            reasons: list[str] = []
            if proposal is None:
                reasons.append("PROPOSAL_NOT_FOUND")
            elif candidate is None:
                reasons.append("SOURCE_CANDIDATE_IDENTITY_MISMATCH")
            else:
                candidate_direction = str(candidate.get("side") or candidate.get("direction") or "").upper()
                for field, left, right in (
                    ("symbol", proposal.get("symbol"), candidate.get("symbol")),
                    ("strategy", proposal.get("strategy"), candidate.get("strategy")),
                    ("direction", proposal.get("direction"), candidate_direction),
                ):
                    if str(left or "").upper() != str(right or "").upper():
                        reasons.append(f"{field.upper()}_MISMATCH")
            events = proposal_store.events(proposal_id) if proposal else []
            published_events = [event for event in events if event.get("event_type") == "AI_REVIEW_PUBLISHED"]
            review = proposal.get("ai_review") if proposal else None
            if proposal and proposal.get("ai_review_status") != "COMPLETED":
                reasons.append("AI_REVIEW_NOT_COMPLETED")
            if len(published_events) != 1:
                reasons.append("AI_REVIEW_PUBLISHED_AUDIT_MISMATCH")
            if not isinstance(review, Mapping):
                reasons.append("AI_REVIEW_PAYLOAD_MISSING")
            elif candidate is not None:
                for field in ("proposal_id", "opportunity_id", "proposal_origin_source_scan_id", "reviewed_observation_id", "reviewed_source_candidate_id"):
                    if str(review.get(field) or "") != str(candidate.get(field) or ""):
                        reasons.append(f"REVIEW_{field.upper()}_MISMATCH")
                if str(review.get("reviewed_source_scan_id") or "") != str(candidate.get("reviewed_source_scan_id") or ""):
                    reasons.append("REVIEW_REVIEWED_SOURCE_SCAN_ID_MISMATCH")
            evidence.append({"proposal_id": proposal_id, "published": not reasons, "reasons": reasons})

        expected_count = len(expected_ids)
        reviewed_count = sum(1 for item in evidence if item["published"])
        complete = expected_count == reviewed_count
        if expected_count == 0 and not checkpoint:
            status = "SKIPPED_NO_CANDIDATES"
        elif complete:
            status = "COMPLETED"
        else:
            status = "REVIEW_PARTIAL"
        now = _now()
        result = dict(job.get("result") or {})
        result.update({
            "status": status,
            "parent_scan_id": parent_id,
            "candidate_count": expected_count,
            "expected_proposal_ids": expected_ids,
            "reviewed_count": reviewed_count,
            "published_count": reviewed_count,
            "review_required": not complete,
            "reconciliation": {"verified_at": now, "evidence": evidence},
        })
        stages = dict(job.get("stage_results") or {})
        stages["GPT_ANALYSIS"] = {
            **(stages.get("GPT_ANALYSIS") or {}), "status": status,
            "candidate_count": expected_count, "reviewed_count": reviewed_count,
            "expected_proposal_ids": expected_ids, "reconciliation": "authoritative_proposal_audit",
            "completed_at": now,
        }
        stages["KANBAN_PUBLICATION"] = {
            **(stages.get("KANBAN_PUBLICATION") or {}), "status": status,
            "published_count": reviewed_count, "expected_proposal_ids": expected_ids,
            "reconciliation": "authoritative_proposal_audit", "completed_at": now,
        }
        with self._connection() as con:
            con.execute(
                "UPDATE ai_trade_desk_scans SET status=?,stage=?,stage_results_json=?,result_json=?,completed_at=?,updated_at=? WHERE scan_id=?",
                (status, "KANBAN_PUBLICATION", json.dumps(stages, sort_keys=True, default=str), json.dumps(result, sort_keys=True, default=str), now if complete or status == "SKIPPED_NO_CANDIDATES" else None, now, scan_id),
            )
        return self._get(scan_id) or result

    def _cancelled(self, scan_id: str) -> bool:
        with self._connection() as con:
            row = con.execute("SELECT cancel_requested FROM ai_trade_desk_scans WHERE scan_id=?", (scan_id,)).fetchone()
            return bool(row and row[0])

    def _stage(self, scan_id: str, stage: str, result: Mapping[str, Any]) -> None:
        with self._connection() as con:
            row = con.execute("SELECT stage_results_json FROM ai_trade_desk_scans WHERE scan_id=?", (scan_id,)).fetchone()
            stages = json.loads(row[0] or "{}") if row else {}
            stages[stage] = {**dict(result), "completed_at": _now()}
            con.execute("UPDATE ai_trade_desk_scans SET status='RUNNING',stage=?,stage_results_json=?,started_at=COALESCE(started_at,?),updated_at=? WHERE scan_id=?", (stage, json.dumps(stages, sort_keys=True, default=str), _now(), _now(), scan_id))

    def _run(self, scan_id: str) -> None:
        try:
            with self._connection() as con:
                con.execute("UPDATE ai_trade_desk_scans SET status='RUNNING',started_at=?,updated_at=? WHERE scan_id=?", (_now(), _now(), scan_id))
            result = self.pipeline(scan_id, lambda stage_name, stage_result: self._stage(scan_id, stage_name, stage_result), lambda: self._cancelled(scan_id))
            status = "CANCELLED" if self._cancelled(scan_id) else str(result.get("status") or "COMPLETED").upper()
            with self._connection() as con:
                con.execute("UPDATE ai_trade_desk_scans SET status=?,result_json=?,completed_at=?,updated_at=? WHERE scan_id=?", (status, json.dumps(dict(result), sort_keys=True, default=str), _now(), _now(), scan_id))
        except Exception as exc:
            with self._connection() as con:
                con.execute("UPDATE ai_trade_desk_scans SET status='FAILED',error_json=?,completed_at=?,updated_at=? WHERE scan_id=?", (json.dumps({"type": type(exc).__name__, "message": str(exc) or type(exc).__name__}), _now(), _now(), scan_id))


def _candidate_rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in result.get("active") or []:
        if not isinstance(item, Mapping):
            continue
        scanner = dict(item.get("scanner") or {})
        candidate = {"symbol": item.get("symbol"), **scanner}
        candidate["radar"] = dict(item.get("radar") or {})
        rows.append(candidate)
    return rows


def _reviewable_candidates(candidates: list[dict[str, Any]], *, proposal_store: Any | None = None) -> list[dict[str, Any]]:
    """Attach only exact, persisted proposal identities to review candidates."""
    from dashboard_react import server
    from src.decision.ai_trade_desk import candidate_identity, opportunity_identity

    proposal_store = proposal_store or server._ai_trade_desk_store()
    proposals = proposal_store.list()
    by_candidate = {str(proposal.get("source_candidate_id") or ""): proposal for proposal in proposals}
    by_opportunity = {str(proposal.get("opportunity_id") or ""): proposal for proposal in proposals if proposal.get("opportunity_id")}
    reviewable: list[dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
        item["direction"] = str(candidate.get("side") or candidate.get("direction") or "").upper()
        candidate_id = candidate_identity(candidate)
        try:
            candidate_opportunity_id = opportunity_identity(candidate)
        except ValueError:
            candidate_opportunity_id = ""
        proposal = by_candidate.get(candidate_id) or by_opportunity.get(candidate_opportunity_id)
        if proposal is not None:
            item["proposal_id"] = proposal["proposal_id"]
            item["proposal_status"] = proposal["status"]
            item["ai_review_status"] = proposal["ai_review_status"]
            item["opportunity_id"] = proposal.get("opportunity_id")
            item["proposal_origin_source_scan_id"] = proposal.get("source_scan_id")
            item["proposal_origin_source_candidate_id"] = proposal.get("source_candidate_id")
            item["reviewed_source_candidate_id"] = candidate_id
            observation = next(
                (
                    row for row in proposal_store.observations(str(proposal["proposal_id"]))
                    if str(row.get("opportunity_id") or "") == str(proposal.get("opportunity_id") or "")
                    and str(row.get("source_scan_id") or "") == str(candidate.get("source_scan_id") or "")
                    and str(row.get("source_candidate_id") or "") == candidate_id
                ),
                None,
            )
            item["reviewed_observation_id"] = observation.get("observation_id") if observation else None
            item["reviewed_source_scan_id"] = observation.get("source_scan_id") if observation else None
            source = proposal.get("source") if isinstance(proposal.get("source"), Mapping) else {}
            material_fields = ("symbol", "strategy", "side", "level_type", "level", "planned_entry", "entry", "stop", "target")
            changed = any(str(candidate.get(field, "")) != str(source.get(field, proposal.get(field, ""))) for field in material_fields)
            item["review_classification"] = "UPDATED_OPPORTUNITY" if changed or proposal.get("ai_review_status") != "COMPLETED" else "UNCHANGED_OPPORTUNITY"
        else:
            item["proposal_id"] = None
            item["proposal_status"] = "NOT_PERSISTED"
            item["ai_review_status"] = "NOT_REVIEWABLE"
            item["opportunity_id"] = candidate_opportunity_id or None
            item["review_classification"] = "NEW_OPPORTUNITY" if candidate_opportunity_id else "HISTORICAL_OR_TERMINAL"
        reviewable.append(item)
    return reviewable


def _review_checkpoint(
    scan_id: str,
    review_job: Mapping[str, Any],
    parent: Mapping[str, Any],
    *,
    include_completed: bool = False,
    proposal_store: Any | None = None,
) -> list[dict[str, Any]]:
    """Build the exact persisted parent-scan candidates eligible for this review."""
    parent_result = parent.get("result") if isinstance(parent.get("result"), Mapping) else {}
    source = parent_result.get("result") if isinstance(parent_result.get("result"), Mapping) else parent_result
    candidates = _reviewable_candidates(_candidate_rows(source), proposal_store=proposal_store)
    if not include_completed:
        candidates = [
            item for item in candidates
            if item.get("review_classification") in {"NEW_OPPORTUNITY", "UPDATED_OPPORTUNITY"}
        ]
    for candidate in candidates:
        candidate["gpt_review_job_id"] = scan_id
        candidate["parent_market_scan_id"] = str(review_job.get("parent_scan_id") or "")
    return candidates


def _build_discovery_universe(rows: list[Mapping[str, Any]], *, max_symbols: int = DISCOVERY_DEFAULT_MAX_SYMBOLS) -> dict[str, Any]:
    """Select a deterministic Radar-only universe; scanner output remains authoritative."""
    normalized = [dict(row) for row in rows if isinstance(row, Mapping)]
    snapshot_as_of = max((str(row.get("as_of") or "") for row in normalized), default="")
    current = [row for row in normalized if str(row.get("as_of") or "") == snapshot_as_of]
    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in current:
        symbol = str(row.get("symbol") or "").strip().upper()
        reasons: list[str] = []
        if not symbol:
            reasons.append("MISSING_SYMBOL")
        elif symbol in seen:
            reasons.append("DUPLICATE_SYMBOL")
        trend = str(row.get("trend") or "").strip().upper()
        if trend not in {"GREEN", "RED", "GREY"}:
            reasons.append("INVALID_TREND")
        try:
            price = float(row.get("price") or row.get("close_price") or 0)
            liquidity = float(row.get("avg_dollar_volume") or 0)
            radar_score = float(row.get("radar_score") or 0)
        except (TypeError, ValueError):
            price = liquidity = radar_score = 0.0
            reasons.append("INVALID_RADAR_NUMERIC_DATA")
        if price <= 0:
            reasons.append("INVALID_PRICE")
        if liquidity <= 0:
            reasons.append("MISSING_LIQUIDITY_DATA")
        if not str(row.get("as_of") or "").strip():
            reasons.append("MISSING_SNAPSHOT_DATE")
        if reasons:
            excluded.append({"symbol": symbol or None, "reasons": reasons})
            continue
        seen.add(symbol)
        selection_reasons = []
        if bool(row.get("breakout")):
            selection_reasons.append("BREAKOUT")
        if bool(row.get("trend_flip")):
            selection_reasons.append("FRESH_FLIP")
        if float(row.get("momentum_score") or 0) > 0:
            selection_reasons.append("MOMENTUM")
        selection_reasons.append("LIQUID_RADAR_ROW")
        selected.append({
            "symbol": symbol,
            "as_of": row.get("as_of"),
            "trend": trend,
            "radar_score": radar_score,
            "breakout": bool(row.get("breakout")),
            "trend_flip": bool(row.get("trend_flip")),
            "momentum_score": row.get("momentum_score"),
            "avg_dollar_volume": liquidity,
            "price": price,
            "scanner_status": row.get("scanner_status") or "NOT_SCANNED",
            "selection_reasons": selection_reasons,
        })
    selected.sort(key=lambda item: (-float(item["radar_score"]), str(item["symbol"])))
    cap = max(1, min(int(max_symbols), DISCOVERY_MAX_SYMBOLS))
    if len(selected) > cap:
        for item in selected[cap:]:
            excluded.append({"symbol": item["symbol"], "reasons": ["DISCOVERY_CAPACITY_LIMIT"]})
        selected = selected[:cap]
    return {
        "snapshot_as_of": snapshot_as_of or None,
        "total_snapshot_rows": len(current),
        "selected_symbols": [item["symbol"] for item in selected],
        "selected": selected,
        "excluded": sorted(excluded, key=lambda item: str(item.get("symbol") or "")),
        "selected_count": len(selected),
        "excluded_count": len(excluded),
        "batch_size": DISCOVERY_BATCH_SIZE,
        "max_symbols": cap,
        "requested_max_symbols": int(max_symbols),
        "actual_sample_size": len(selected),
        "valid": bool(selected),
        "reason_code": None if selected else "DISCOVERY_NO_ELIGIBLE_RADAR_ROWS",
    }


def _run_discovery_stages(scan_id: str, stage: Callable[[str, Mapping[str, Any]], None], cancelled: Callable[[], bool], *, max_symbols: int = DISCOVERY_DEFAULT_MAX_SYMBOLS) -> dict[str, Any]:
    from dashboard_react import server

    radar = server._radar_service()
    if cancelled():
        return {"status": "CANCELLED", "discovery_mode": "FULL_MARKET_DISCOVERY", "orders_submitted": 0}
    refreshed = radar.refresh()
    stage("RADAR_REFRESH", {"status": "COMPLETED", "symbols_processed": len(refreshed.get("results") or []), "warnings": refreshed.get("skipped") or []})
    rows = [dict(row) for row in radar.list_results(limit=5000) if isinstance(row, Mapping)]
    universe = _build_discovery_universe(rows, max_symbols=max_symbols)
    snapshot_timestamp = max((str(row.get("created_at") or "") for row in rows if str(row.get("as_of") or "") == universe.get("snapshot_as_of")), default=None)
    checkpoint = {
        "discovery_mode": "FULL_MARKET_DISCOVERY",
        "source": "RADAR_DISCOVERY",
        "snapshot_as_of": universe.get("snapshot_as_of"),
        "snapshot_timestamp": snapshot_timestamp,
        "source_scan_id": f"radar:{universe.get('snapshot_as_of')}:{snapshot_timestamp}" if universe.get("snapshot_as_of") and snapshot_timestamp else None,
        "captured_at": _now(),
        **universe,
    }
    stage("DISCOVERY_UNIVERSE", {"status": "COMPLETED" if universe.get("valid") else "EMPTY", **checkpoint})
    if cancelled():
        return {"status": "CANCELLED", "discovery_mode": "FULL_MARKET_DISCOVERY", "radar_universe_checkpoint": checkpoint, "orders_submitted": 0}
    selected = tuple(universe.get("selected_symbols") or [])
    batches = [selected[index:index + DISCOVERY_BATCH_SIZE] for index in range(0, len(selected), DISCOVERY_BATCH_SIZE)]
    batch_results: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    terminal: list[dict[str, Any]] = []
    unsafe_in_flight = False
    for batch_index, batch in enumerate(batches, start=1):
        if cancelled():
            return {"status": "CANCELLED", "discovery_mode": "FULL_MARKET_DISCOVERY", "radar_universe_checkpoint": checkpoint, "batch_results": batch_results, "orders_submitted": 0}
        request = server.DailyTraderScanRequest(
            mode="scheduled", top_n=10, include_breakouts=True, include_fresh_flips=True,
            max_symbols=len(batch), idempotency_key=f"{scan_id}:batch:{batch_index}", trigger_source="manual",
            symbols_override=tuple(batch), scanner_timeout_seconds=DISCOVERY_BATCH_TIMEOUT_SECONDS,
        )
        try:
            result = server._daily_trader_service().scan(request, scan_id=f"{scan_id}:batch:{batch_index}")
            if str(result.get("status") or "").upper() in {"IN_FLIGHT", "BLOCKED_GLOBAL_IN_FLIGHT"}:
                unsafe_in_flight = True
                batch_results.append({"batch_index": batch_index, "batch_id": f"{scan_id}:batch:{batch_index}", "symbols": list(batch), "status": str(result.get("status") or "IN_FLIGHT").upper(), "requested": len(batch), "processed": 0, "in_flight_owner": result.get("in_flight_owner"), "errors": [{"reason_code": "SCANNER_BATCH_IN_FLIGHT", "owner_batch_id": result.get("owner_batch_id")} ]})
                break
            errors = list(result.get("errors") or [])
            active.extend(result.get("active") or [])
            terminal.extend(result.get("terminal") or [])
            batch_results.append({"batch_index": batch_index, "batch_id": f"{scan_id}:batch:{batch_index}", "symbols": list(batch), "status": "COMPLETED_WITH_ERRORS" if errors else "COMPLETED", "requested": len(batch), "processed": result.get("processed_count", 0), "errors": errors})
        except Exception as exc:
            batch_results.append({"batch_index": batch_index, "batch_id": f"{scan_id}:batch:{batch_index}", "symbols": list(batch), "status": "FAILED", "requested": len(batch), "processed": 0, "errors": [{"type": type(exc).__name__}]})
    stage_status = "BLOCKED_SCANNER_IN_FLIGHT" if unsafe_in_flight else ("COMPLETED" if batch_results and all(item["status"] == "COMPLETED" for item in batch_results) else ("COMPLETED_WITH_ERRORS" if batch_results else "EMPTY"))
    combined = {"active": active, "terminal": terminal, "processed_count": sum(int(item.get("processed") or 0) for item in batch_results), "scanner": {"requested": len(selected), "completed": sum(int(item.get("processed") or 0) for item in batch_results), "failed": sum(len(item.get("errors") or []) for item in batch_results)}}
    candidates = _candidate_rows(combined)
    stage("PRODUCTION_SCANNER", {"status": stage_status, "batch_count": len(batch_results), "batches": batch_results, "selected_symbols": selected, "symbols_processed": combined["processed_count"], "candidate_count": len(candidates), "scanner": combined["scanner"]})
    published: list[str] = []
    if unsafe_in_flight:
        stage("PROPOSALS", {"status": "BLOCKED_SCANNER_IN_FLIGHT", "created_or_reused_count": 0, "proposal_ids": []})
        return {"status": "BLOCKED_SCANNER_IN_FLIGHT", "discovery_mode": "FULL_MARKET_DISCOVERY", "scan_id": scan_id, "candidate_count": 0, "proposal_ids": [], "radar_universe_checkpoint": checkpoint, "batch_results": batch_results, "result": combined, "orders_submitted": 0, "approvals_created": 0, "execution_intents_created": 0, "recovery_required": True}
    for candidate in candidates:
        key = "discovery:" + "|".join(str(candidate.get(field) or "") for field in ("source_scan_id", "symbol", "strategy", "side"))
        try:
            proposal = server._ai_trade_desk_store().ingest(candidate, idempotency_key=key, ai_reasoning="AI review pending; deterministic scanner facts only.", ai_model="pending", ai_review_status="PENDING")
            published.append(str(proposal["proposal_id"]))
        except ValueError:
            continue
    stage("PROPOSALS", {"status": "COMPLETED", "created_or_reused_count": len(published), "proposal_ids": published})
    return {"status": "COMPLETED_WITH_ERRORS" if stage_status != "COMPLETED" else "COMPLETED", "discovery_mode": "FULL_MARKET_DISCOVERY", "scan_id": scan_id, "candidate_count": len(candidates), "proposal_ids": published, "radar_universe_checkpoint": checkpoint, "batch_results": batch_results, "result": combined, "orders_submitted": 0, "approvals_created": 0, "execution_intents_created": 0}


def _run_market_stages(scan_id: str, stage: Callable[[str, Mapping[str, Any]], None], cancelled: Callable[[], bool]) -> dict[str, Any]:
    from dashboard_react import server

    radar = server._radar_service()
    if cancelled():
        return {"status": "CANCELLED", "orders_submitted": 0}
    refreshed = radar.refresh()
    stage("RADAR_REFRESH", {"status": "COMPLETED", "symbols_processed": len(refreshed.get("results") or []), "warnings": refreshed.get("skipped") or []})
    if cancelled():
        return {"status": "CANCELLED", "orders_submitted": 0}
    breakouts = server._daily_trader_service().radar_rows(limit=5000)
    stage("BREAKOUTS", {"status": "COMPLETED", "symbols_processed": breakouts.get("count", 0), "candidate_count": sum(1 for row in breakouts.get("results") or [] if row.get("breakout"))})
    flips = server._daily_trader_service().radar_rows(flips_only=True, limit=5000)
    stage("FRESH_FLIPS", {"status": "COMPLETED", "symbols_processed": flips.get("count", 0), "candidate_count": flips.get("count", 0)})
    universe = server._radar_setup_universe()
    # Persist the complete selection evidence with the scan. The selected tuple below
    # remains the sole input to the production scanner, even if Radar is updated later.
    universe_checkpoint = {
        "source": universe.get("source", "RADAR_SETUPS"),
        "source_scan_id": universe.get("source_scan_id"),
        "snapshot_as_of": universe.get("snapshot_as_of"),
        "snapshot_timestamp": universe.get("snapshot_timestamp"),
        "eligible_symbol_count": universe.get("eligible_symbol_count", 0),
        "selected_symbols": list(universe.get("selected_symbols") or []),
        "selected": list(universe.get("selected") or []),
        "excluded_status_counts": dict(universe.get("excluded_status_counts") or {}),
        "stale_symbol_count": universe.get("stale_symbol_count", 0),
        "total_snapshot_rows": universe.get("total_snapshot_rows", 0),
        "reason_code": universe.get("reason_code"),
        "captured_at": _now(),
    }
    stage("RADAR_SETUPS", {"status": "COMPLETED" if universe.get("valid") else "EMPTY", **universe_checkpoint})
    if cancelled():
        return {"status": "CANCELLED", "orders_submitted": 0}
    selected_symbols = tuple(str(symbol).strip().upper() for symbol in (universe.get("selected_symbols") or []) if str(symbol).strip())
    request = server.DailyTraderScanRequest(
        mode="scheduled", top_n=10, include_breakouts=True, include_fresh_flips=True,
        max_symbols=len(selected_symbols), idempotency_key=scan_id, trigger_source="manual",
        symbols_override=selected_symbols,
    )
    production = server._daily_trader_service().scan(request, scan_id=scan_id)
    candidates = _candidate_rows(production)
    stage("PRODUCTION_SCANNER", {"status": "COMPLETED", "scan_id": production.get("scan_id"), "symbols_processed": production.get("processed_count", 0), "candidate_count": len(candidates), "scanner": production.get("scanner"), "selected_symbols": selected_symbols})
    published: list[str] = []
    for candidate in candidates:
        if cancelled():
            return {"status": "CANCELLED", "orders_submitted": 0, "candidate_count": len(candidates)}
        key = "scan:" + "|".join(str(candidate.get(field) or "") for field in ("source_scan_id", "symbol", "strategy", "side"))
        try:
            proposal = server._ai_trade_desk_store().ingest(candidate, idempotency_key=key, ai_reasoning="AI review pending; deterministic scanner facts only.", ai_model="pending", ai_review_status="PENDING")
            published.append(str(proposal["proposal_id"]))
        except ValueError:
            continue
    stage("PROPOSALS", {"status": "COMPLETED", "created_or_reused_count": len(published), "proposal_ids": published})
    return {
        "status": "COMPLETED", "scan_id": production.get("scan_id"),
        "candidate_count": len(candidates), "proposal_ids": published,
        "radar_universe_checkpoint": universe_checkpoint,
        "result": production, "orders_submitted": 0,
        "approvals_created": 0, "execution_intents_created": 0,
    }


def _run_ai_trade_desk_pipeline(scan_id: str, stage: Callable[[str, Mapping[str, Any]], None], cancelled: Callable[[], bool]) -> Mapping[str, Any]:
    from dashboard_react import server

    job = server._ai_trade_desk_scan_jobs().status(scan_id) or {}
    job_type = str(job.get("job_type") or "FULL_MARKET_SCAN").upper()
    if job_type == "FULL_MARKET_DISCOVERY":
        parameters = job.get("parameters") if isinstance(job.get("parameters"), Mapping) else {}
        return _run_discovery_stages(scan_id, stage, cancelled, max_symbols=int(parameters.get("max_symbols", DISCOVERY_DEFAULT_MAX_SYMBOLS)))
    if job_type in {"FULL_MARKET_SCAN", "FULL_AI_SCAN"}:
        market = _run_market_stages(scan_id, stage, cancelled)
        if job_type == "FULL_MARKET_SCAN" or market.get("status") == "CANCELLED":
            return market
    parent_id = str(job.get("parent_scan_id") or scan_id)
    parent = server._ai_trade_desk_scan_jobs().status(parent_id) or {}
    result = parent.get("result") if isinstance(parent.get("result"), Mapping) else {}
    review_candidates = _review_checkpoint(scan_id, job, parent)
    if not review_candidates:
        stage("GPT_ANALYSIS", {"status": "SKIPPED_NO_CANDIDATES", "candidate_count": 0, "reviewed_count": 0, "reason_code": "NO_ELIGIBLE_CANDIDATES", "review_payload_available": False})
        stage("KANBAN_PUBLICATION", {"status": "SKIPPED_NO_CANDIDATES", "published_count": 0, "reason_code": "NO_ELIGIBLE_CANDIDATES"})
        return {"status": "SKIPPED_NO_CANDIDATES", "parent_scan_id": parent_id, "candidate_count": 0, "reviewed_count": 0, "published_count": 0, "orders_submitted": 0, "approvals_created": 0, "execution_intents_created": 0, "review_required": False}
    stage("GPT_ANALYSIS", {"status": "REVIEW_PENDING", "candidate_count": len(review_candidates), "reviewed_count": 0, "reason_code": "CHATGPT_REVIEW_IMPORT_REQUIRED", "review_payload_available": True})
    stage("KANBAN_PUBLICATION", {"status": "PENDING_AI_REVIEW", "published_count": 0})
    return {"status": "REVIEW_PENDING", "parent_scan_id": parent_id, "candidate_count": len(review_candidates), "reviewed_count": 0, "published_count": 0, "orders_submitted": 0, "approvals_created": 0, "execution_intents_created": 0, "review_required": True}


def _register_dashboard_routes() -> None:
    try:
        from dashboard_react import server
    except Exception:
        return
    server._run_ai_trade_desk_pipeline = _run_ai_trade_desk_pipeline
    if not hasattr(server, "app"):
        return

    def require_human(request: Request) -> str:
        return server._ai_trade_desk_role(request, "human")

    def submit(job_type: str, request: Request, body: dict | None = None):
        require_human(request)
        values = dict(body or {})
        parent = values.get("parent_scan_id")
        try:
            parameters = {}
            if str(job_type).upper() == "FULL_MARKET_DISCOVERY":
                parameters["max_symbols"] = values.get("max_symbols", DISCOVERY_DEFAULT_MAX_SYMBOLS)
            return server._ai_trade_desk_scan_jobs().submit(job_type=job_type, parent_scan_id=str(parent) if parent else None, parameters=parameters)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    def full_market(request: Request):
        return submit("FULL_MARKET_SCAN", request)

    def full_market_discovery(request: Request):
        return submit("FULL_MARKET_DISCOVERY", request)

    def review(request: Request, body: dict):
        return submit("GPT_REVIEW", request, body)

    def full_ai(request: Request):
        return submit("FULL_AI_SCAN", request)

    def preview(request: Request, body: dict):
        require_human(request)
        from src.decision.ai_trade_desk import calculate_trade_metrics
        try:
            values = calculate_trade_metrics(direction=body.get("direction"), entry=body.get("entry"), stop=body.get("stop"), target=body.get("target"), rr=body.get("rr"), cash_allocation=body.get("cash_allocation"), max_risk_pct=body.get("max_risk_pct"), quantity=body.get("quantity"), keep_target=bool(body.get("keep_target", False)))
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"preview": values, "read_only": True, "orders_submitted": 0, "execution_intents_created": 0}

    def status(scan_id: str):
        item = server._ai_trade_desk_scan_jobs().status(scan_id)
        if item is None:
            raise HTTPException(404, "scan not found")
        return item

    def runs(limit: int = 20):
        manager = server._ai_trade_desk_scan_jobs()
        with manager._connection() as con:
            rows = con.execute("SELECT * FROM ai_trade_desk_scans ORDER BY updated_at DESC LIMIT ?", (max(1, min(int(limit), 100)),)).fetchall()
            return {"runs": [manager._decode(row) for row in rows]}

    def cancel(scan_id: str, request: Request):
        require_human(request)
        item = server._ai_trade_desk_scan_jobs().cancel(scan_id)
        if item is None:
            raise HTTPException(404, "scan not found")
        return item

    def review_payload(scan_id: str, request: Request):
        require_human(request)
        item = status(scan_id)
        result = item.get("result") if isinstance(item.get("result"), Mapping) else {}
        parent = server._ai_trade_desk_scan_jobs().status(str(item.get("parent_scan_id") or scan_id)) or item
        candidates = _review_checkpoint(scan_id, item, parent)
        review_status = "SKIPPED_NO_CANDIDATES" if item.get("status") == "SKIPPED_NO_CANDIDATES" else "AI_REVIEW_PENDING"
        return {
            "scan_id": scan_id,
            "gpt_review_job_id": scan_id,
            "parent_market_scan_id": str(item.get("parent_scan_id") or ""),
            "review_status": review_status,
            "candidates": candidates,
            "reviewable_count": sum(1 for candidate in candidates if candidate.get("proposal_id")),
            "unpublished_count": sum(1 for candidate in candidates if not candidate.get("proposal_id")),
            "read_only": True,
            "orders_submitted": 0,
        }

    def review_import(scan_id: str, request: Request, body: dict):
        reviews = body.get("reviews") if isinstance(body, Mapping) else None
        if not isinstance(reviews, list):
            raise HTTPException(400, "reviews must be a list")
        item = status(scan_id)
        if item.get("status") == "SKIPPED_NO_CANDIDATES" and not reviews:
            return {"scan_id": scan_id, "status": "SKIPPED_NO_CANDIDATES", "reviewed_count": 0, "published_count": 0, "count": 0, "orders_submitted": 0, "execution_intents_created": 0}
        actor = server._ai_trade_desk_role(request, "ai")
        parent = server._ai_trade_desk_scan_jobs().status(str(item.get("parent_scan_id") or ""))
        if parent is None:
            raise HTTPException(422, "parent_market_scan_not_found")
        checkpoint = {
            str(candidate.get("proposal_id") or ""): candidate
            for candidate in _review_checkpoint(scan_id, item, parent, include_completed=True)
            if candidate.get("proposal_id")
        }
        published = []
        for review_item in reviews:
            proposal_id = str(review_item.get("proposal_id") or "")
            if not proposal_id:
                raise HTTPException(422, "proposal_id is required")
            expected = checkpoint.get(proposal_id)
            if expected is None:
                raise HTTPException(422, "review proposal is not an exact reviewable parent-scan checkpoint candidate")
            for field in (
                "gpt_review_job_id", "parent_market_scan_id", "opportunity_id",
                "proposal_origin_source_scan_id", "reviewed_observation_id",
                "reviewed_source_candidate_id", "source_scan_id", "symbol", "strategy",
            ):
                if str(review_item.get(field) or "").upper() != str(expected.get(field) or "").upper():
                    raise HTTPException(422, f"review {field} does not match authoritative parent-scan checkpoint")
            if str(review_item.get("direction") or review_item.get("side") or "").upper() != str(expected.get("side") or expected.get("direction") or "").upper():
                raise HTTPException(422, "review direction does not match authoritative parent-scan checkpoint")
            if str(review_item.get("lifecycle_status") or "").upper() != str(expected.get("lifecycle_status") or "").upper():
                raise HTTPException(422, "review lifecycle_status does not match authoritative parent-scan checkpoint")
            try:
                published.append(server._ai_trade_desk_store().publish_ai_review(proposal_id, review_item, actor=actor))
            except (KeyError, ValueError) as exc:
                raise HTTPException(422, str(exc)) from exc
        aggregate = server._ai_trade_desk_scan_jobs().reconcile_review_job(
            scan_id, proposal_store=server._ai_trade_desk_store()
        )
        return {"scan_id": scan_id, "published": published, "count": len(published), "aggregate": aggregate, "orders_submitted": 0, "execution_intents_created": 0}

    def reconcile(scan_id: str, request: Request):
        require_human(request)
        try:
            return server._ai_trade_desk_scan_jobs().reconcile_review_job(
                scan_id, proposal_store=server._ai_trade_desk_store()
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    routes = [
        ("/api/ai-trade-desk/preview", preview, ["POST"]),
        ("/api/ai-trade-desk/scans/full-market", full_market, ["POST"]),
        ("/api/ai-trade-desk/scans/full-market-discovery", full_market_discovery, ["POST"]),
        ("/api/ai-trade-desk/scans/review", review, ["POST"]),
        ("/api/ai-trade-desk/scans/full-ai", full_ai, ["POST"]),
        ("/api/ai-trade-desk/scans", runs, ["GET"]),
        ("/api/ai-trade-desk/scans/{scan_id}", status, ["GET"]),
        ("/api/ai-trade-desk/scans/{scan_id}/cancel", cancel, ["POST"]),
        ("/api/ai-trade-desk/scans/{scan_id}/review-payload", review_payload, ["GET"]),
        ("/api/ai-trade-desk/scans/{scan_id}/review-import", review_import, ["POST"]),
        ("/api/ai-trade-desk/scans/{scan_id}/review-reconcile", reconcile, ["POST"]),
    ]
    existing = {getattr(route, "path", "") for route in server.app.routes}
    for path, endpoint, methods in routes:
        if path not in existing:
            server.app.add_api_route(path, endpoint, methods=methods)


if "dashboard_react.server" in __import__("sys").modules:
    threading.Timer(0.05, _register_dashboard_routes).start()
