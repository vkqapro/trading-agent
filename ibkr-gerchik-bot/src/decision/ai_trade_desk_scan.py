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


STAGES = ("RADAR_REFRESH", "BREAKOUTS", "FRESH_FLIPS", "RADAR_SETUPS", "PRODUCTION_SCANNER", "PROPOSALS")


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
                cancel_requested INTEGER NOT NULL DEFAULT 0, started_at TEXT,
                completed_at TEXT, updated_at TEXT NOT NULL
            )""")
            columns = {row[1] for row in con.execute("PRAGMA table_info(ai_trade_desk_scans)").fetchall()}
            if "job_type" not in columns:
                con.execute("ALTER TABLE ai_trade_desk_scans ADD COLUMN job_type TEXT NOT NULL DEFAULT 'FULL_MARKET_SCAN'")
            if "parent_scan_id" not in columns:
                con.execute("ALTER TABLE ai_trade_desk_scans ADD COLUMN parent_scan_id TEXT")
            con.execute("CREATE INDEX IF NOT EXISTS idx_ai_trade_desk_scans_updated ON ai_trade_desk_scans(updated_at)")

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        for key in ("stage_results_json", "result_json", "error_json"):
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

    def submit(self, *, job_type: str = "FULL_MARKET_SCAN", parent_scan_id: str | None = None) -> dict[str, Any]:
        normalized_type = str(job_type or "FULL_MARKET_SCAN").strip().upper()
        if normalized_type not in {"FULL_MARKET_SCAN", "GPT_REVIEW", "FULL_AI_SCAN"}:
            raise ValueError("unsupported AI Trade Desk job type")
        with self.lock:
            current = self.latest()
            if current and current["status"] in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}:
                return current
            scan_id = "aitd-scan-" + uuid.uuid4().hex
            now = _now()
            with self._connection() as con:
                con.execute("INSERT INTO ai_trade_desk_scans(scan_id,job_type,parent_scan_id,status,stage,stage_results_json,updated_at) VALUES(?,?,?,?,?,?,?)", (scan_id, normalized_type, parent_scan_id, "QUEUED", "QUEUED", "{}", now))
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
        from src.decision.ai_trade_desk import candidate_identity

        job = self._get(scan_id)
        if job is None:
            raise KeyError("review_job_not_found")
        if str(job.get("job_type") or "").upper() != "GPT_REVIEW":
            raise ValueError("review_job_required")
        parent_id = str(job.get("parent_scan_id") or "").strip()
        parent = self._get(parent_id) if parent_id else None
        if parent is None:
            raise ValueError("parent_market_scan_not_found")
        parent_result = parent.get("result") if isinstance(parent.get("result"), Mapping) else {}
        source = parent_result.get("result") if isinstance(parent_result.get("result"), Mapping) else parent_result
        candidates = _candidate_rows(source)
        proposal_stage = (parent.get("stage_results") or {}).get("PROPOSALS") or {}
        expected_ids = [str(item) for item in proposal_stage.get("proposal_ids") or [] if str(item).strip()]
        by_identity = {candidate_identity(item): item for item in candidates}
        evidence: list[dict[str, Any]] = []
        for proposal_id in expected_ids:
            proposal = proposal_store.get(proposal_id)
            candidate = by_identity.get(str(proposal.get("source_candidate_id") or "")) if proposal else None
            reasons: list[str] = []
            if proposal is None:
                reasons.append("PROPOSAL_NOT_FOUND")
            elif candidate is None:
                reasons.append("SOURCE_CANDIDATE_IDENTITY_MISMATCH")
            else:
                candidate_direction = str(candidate.get("side") or candidate.get("direction") or "").upper()
                for field, left, right in (
                    ("source_scan_id", proposal.get("source_scan_id"), candidate.get("source_scan_id")),
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
            else:
                for field in ("source_scan_id", "symbol", "strategy"):
                    if str(review.get(field) or "").upper() != str(proposal.get(field) or "").upper():
                        reasons.append(f"REVIEW_{field.upper()}_MISMATCH")
            evidence.append({"proposal_id": proposal_id, "published": not reasons, "reasons": reasons})

        expected_count = len(expected_ids)
        reviewed_count = sum(1 for item in evidence if item["published"])
        complete = expected_count == reviewed_count
        if expected_count == 0 and not candidates:
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


def _reviewable_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach only exact, persisted proposal identities to review candidates."""
    from dashboard_react import server
    from src.decision.ai_trade_desk import candidate_identity, opportunity_identity

    proposals = server._ai_trade_desk_store().list()
    by_candidate = {str(proposal.get("source_candidate_id") or ""): proposal for proposal in proposals}
    by_opportunity = {str(proposal.get("opportunity_id") or ""): proposal for proposal in proposals if proposal.get("opportunity_id")}
    reviewable: list[dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
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
    stage("RADAR_SETUPS", {"status": "COMPLETED" if universe.get("valid") else "EMPTY", **{key: universe.get(key) for key in ("source_scan_id", "snapshot_as_of", "eligible_symbol_count", "selected_symbols", "reason_code")}})
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
    return {"status": "COMPLETED", "scan_id": production.get("scan_id"), "candidate_count": len(candidates), "proposal_ids": published, "result": production, "orders_submitted": 0, "approvals_created": 0, "execution_intents_created": 0}


def _run_ai_trade_desk_pipeline(scan_id: str, stage: Callable[[str, Mapping[str, Any]], None], cancelled: Callable[[], bool]) -> Mapping[str, Any]:
    from dashboard_react import server

    job = server._ai_trade_desk_scan_jobs().status(scan_id) or {}
    job_type = str(job.get("job_type") or "FULL_MARKET_SCAN").upper()
    if job_type in {"FULL_MARKET_SCAN", "FULL_AI_SCAN"}:
        market = _run_market_stages(scan_id, stage, cancelled)
        if job_type == "FULL_MARKET_SCAN" or market.get("status") == "CANCELLED":
            return market
    parent_id = str(job.get("parent_scan_id") or scan_id)
    parent = server._ai_trade_desk_scan_jobs().status(parent_id) or {}
    result = parent.get("result") if isinstance(parent.get("result"), Mapping) else {}
    candidates = _candidate_rows(result.get("result") if isinstance(result.get("result"), Mapping) else result)
    review_candidates = [
        item for item in _reviewable_candidates(candidates)
        if item.get("review_classification") in {"NEW_OPPORTUNITY", "UPDATED_OPPORTUNITY"}
    ]
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
            return server._ai_trade_desk_scan_jobs().submit(job_type=job_type, parent_scan_id=str(parent) if parent else None)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    def full_market(request: Request):
        return submit("FULL_MARKET_SCAN", request)

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
        parent_result = parent.get("result") if isinstance(parent.get("result"), Mapping) else {}
        source = parent_result.get("result") if isinstance(parent_result.get("result"), Mapping) else parent_result
        candidates = [
            item for item in _reviewable_candidates(_candidate_rows(source))
            if item.get("review_classification") in {"NEW_OPPORTUNITY", "UPDATED_OPPORTUNITY"}
        ]
        review_status = "SKIPPED_NO_CANDIDATES" if item.get("status") == "SKIPPED_NO_CANDIDATES" else "AI_REVIEW_PENDING"
        return {
            "scan_id": scan_id,
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
        published = []
        for review_item in reviews:
            proposal_id = str(review_item.get("proposal_id") or "")
            if not proposal_id:
                raise HTTPException(422, "proposal_id is required")
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
