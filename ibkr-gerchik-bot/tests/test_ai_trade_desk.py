from pathlib import Path
import time
import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.decision.ai_trade_desk import AITradeDeskStore, calculate_trade_metrics
from src.decision.ai_trade_desk_scan import AITradeDeskScanManager


def candidate():
    return {
        "symbol": "TEST",
        "strategy": "LP1",
        "side": "LONG",
        "source_scan_id": "scan_test",
        "entry": 81.08,
        "stop": 79.92,
        "target": 84.27,
        "rr": 2.75,
        "position_size": 12,
        "risk_pct": 5,
        "scanner_status": "CANDIDATE",
        "lifecycle_status": "ENTRY_TRIGGERED",
    }


def test_direction_aware_trade_math_matches_brief():
    long_metrics = calculate_trade_metrics(
        direction="LONG", entry=81.08, stop=79.92, target=84.27,
        rr=2.75, cash_allocation=1000, max_risk_pct=5, quantity=12,
    )
    assert long_metrics["risk_per_share"] == 1.16
    assert long_metrics["actual_risk"] == 13.92
    assert long_metrics["potential_profit"] == 38.28

    short_metrics = calculate_trade_metrics(
        direction="SHORT", entry=100, stop=102, target=94,
        rr=3, cash_allocation=1000, max_risk_pct=10, quantity=5,
    )
    assert short_metrics["risk_per_share"] == 2
    assert short_metrics["reward_per_share"] == 6
    assert short_metrics["rr"] == 3


@pytest.mark.parametrize(
    "values",
    [
        {"direction": "LONG", "entry": 80, "stop": 81, "target": 85},
        {"direction": "SHORT", "entry": 80, "stop": 79, "target": 85},
    ],
)
def test_invalid_direction_relationships_fail_closed(values):
    with pytest.raises(ValueError):
        calculate_trade_metrics(**values)


def test_store_is_idempotent_and_audits_revision(tmp_path: Path):
    store = AITradeDeskStore(tmp_path / "decision.db")
    first = store.ingest(candidate(), idempotency_key="persisted:test")
    duplicate = store.ingest(candidate(), idempotency_key="persisted:test")
    assert duplicate["proposal_id"] == first["proposal_id"]
    assert len(store.list()) == 1

    edited = store.update(first["proposal_id"], {"target": 85, "rr": 3, "keep_target": True}, actor="tester", reason="test edit")
    assert edited["revision"] == 1
    assert edited["target"] == 85
    assert len(store.revisions(first["proposal_id"])) == 1
    assert len(store.events(first["proposal_id"])) == 2

    with pytest.raises(ValueError, match="invalid transition"):
        store.transition(first["proposal_id"], "FILLED", actor="tester")
    ready = store.transition(first["proposal_id"], "UNDER_REVIEW", actor="tester")
    assert ready["status"] == "UNDER_REVIEW"


def test_ready_transition_rejects_entry_triggered_partial_quality_signal(tmp_path: Path):
    store = AITradeDeskStore(tmp_path / "decision.db")
    proposal = store.ingest({**candidate(), "source_timestamp": datetime.now(timezone.utc).isoformat(), "data_quality_complete": False, "missing_quality_data": ["spread"]}, idempotency_key="ready-blocked")
    store.transition(proposal["proposal_id"], "UNDER_REVIEW", actor="tester")

    with pytest.raises(ValueError, match="SIGNAL_NOT_CURRENTLY_READY"):
        store.transition(proposal["proposal_id"], "READY", actor="tester")


def test_ready_transition_requires_provenance_and_fresh_complete_source(tmp_path: Path):
    store = AITradeDeskStore(tmp_path / "decision.db")
    valid = {**candidate(), "lifecycle_status": "READY", "scan_timestamp": datetime.now(timezone.utc).isoformat(), "data_quality_complete": True}
    proposal = store.ingest(valid, idempotency_key="ready-valid")
    store.transition(proposal["proposal_id"], "UNDER_REVIEW", actor="tester")
    ready = store.transition(proposal["proposal_id"], "READY", actor="tester")
    assert ready["status"] == "READY"
    assert store.ready_transition_check(ready)["eligible"] is True


def test_ready_transition_rejects_stale_source_observation(tmp_path: Path):
    store = AITradeDeskStore(tmp_path / "decision.db")
    stale = {**candidate(), "lifecycle_status": "READY", "scan_timestamp": "2020-01-01T00:00:00+00:00", "data_quality_complete": True}
    proposal = store.ingest(stale, idempotency_key="ready-stale")
    store.transition(proposal["proposal_id"], "UNDER_REVIEW", actor="tester")

    with pytest.raises(ValueError, match="STALE_SOURCE_OBSERVATION"):
        store.transition(proposal["proposal_id"], "READY", actor="tester")


def test_scan_manager_persists_stages_and_prevents_overlap(tmp_path: Path):
    seen = []
    import threading
    started = threading.Event()
    release = threading.Event()

    def pipeline(scan_id, stage, cancelled):
        seen.append(scan_id)
        started.set()
        release.wait(3)
        stage("RADAR_REFRESH", {"status": "COMPLETED"})
        stage("PROPOSALS", {"status": "COMPLETED", "created_or_reused_count": 2})
        return {"proposals_created": 2, "orders_submitted": 0}

    manager = AITradeDeskScanManager(tmp_path / "decision.db", pipeline)
    first = manager.submit()
    assert started.wait(3)
    second = manager.submit()
    assert second["scan_id"] == first["scan_id"]
    release.set()

    import time
    deadline = time.time() + 3
    current = manager.status(first["scan_id"])
    while current and current["status"] in {"QUEUED", "RUNNING"} and time.time() < deadline:
        time.sleep(0.01)
        current = manager.status(first["scan_id"])
    assert current["status"] == "COMPLETED"
    assert current["stage_results"]["PROPOSALS"]["created_or_reused_count"] == 2
    assert current["result"]["orders_submitted"] == 0
    assert seen == [first["scan_id"]]


def test_scan_manager_persists_zero_candidate_terminal_review_state(tmp_path: Path):
    def pipeline(scan_id, stage, cancelled):
        stage("GPT_ANALYSIS", {"status": "SKIPPED_NO_CANDIDATES", "reviewed_count": 0})
        stage("KANBAN_PUBLICATION", {"status": "SKIPPED_NO_CANDIDATES", "published_count": 0})
        return {
            "status": "SKIPPED_NO_CANDIDATES",
            "candidate_count": 0,
            "reviewed_count": 0,
            "published_count": 0,
            "orders_submitted": 0,
        }

    manager = AITradeDeskScanManager(tmp_path / "decision.db", pipeline)
    run = manager.submit(job_type="GPT_REVIEW", parent_scan_id="completed-market-scan")
    deadline = time.time() + 3
    current = manager.status(run["scan_id"])
    while current and current["status"] in {"QUEUED", "RUNNING"} and time.time() < deadline:
        time.sleep(0.01)
        current = manager.status(run["scan_id"])

    assert current["status"] == "SKIPPED_NO_CANDIDATES"
    assert current["result"]["reviewed_count"] == 0
    assert current["result"]["published_count"] == 0
    assert current["stage_results"]["KANBAN_PUBLICATION"]["published_count"] == 0


def _insert_review_job(manager, scan_id, parent_scan_id):
    now = "2026-10-20T00:00:00+00:00"
    with manager._connection() as con:
        con.execute(
            "INSERT INTO ai_trade_desk_scans(scan_id,job_type,parent_scan_id,status,stage,stage_results_json,result_json,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (parent_scan_id, "FULL_MARKET_SCAN", None, "COMPLETED", "PROPOSALS", json.dumps({"PROPOSALS": {"status": "COMPLETED", "proposal_ids": []}}), json.dumps({}), now),
        )
        con.execute(
            "INSERT INTO ai_trade_desk_scans(scan_id,job_type,parent_scan_id,status,stage,stage_results_json,result_json,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (scan_id, "GPT_REVIEW", parent_scan_id, "REVIEW_PENDING", "KANBAN_PUBLICATION", json.dumps({"GPT_ANALYSIS": {"status": "REVIEW_PENDING"}, "KANBAN_PUBLICATION": {"status": "PENDING_AI_REVIEW"}}), json.dumps({"status": "REVIEW_PENDING"}), now),
        )


def test_review_job_reconciliation_completes_from_authoritative_audit(tmp_path: Path):
    import json

    store = AITradeDeskStore(tmp_path / "decision.db")
    source = {**candidate(), "source_scan_id": "scanner-observation-1", "scan_timestamp": "2026-10-20T00:00:00+00:00"}
    proposal = store.ingest(source, idempotency_key="reconcile:complete", ai_review_status="PENDING")
    store.publish_ai_review(proposal["proposal_id"], {
        "source_scan_id": source["source_scan_id"], "symbol": "TEST", "strategy": "LP1",
        "ai_reasoning": "source-backed", "strengths": ["s"], "weaknesses": ["w"],
        "risk_observations": ["r"], "review_result": "HUMAN_REVIEW_REQUIRED", "model_provider": "fixture",
    }, actor="fixture-ai")
    manager = AITradeDeskScanManager(tmp_path / "decision.db", lambda *_: {})
    parent_id = "market-complete"
    review_id = "review-complete"
    with manager._connection() as con:
        parent_result = {"result": {"active": [{"symbol": "TEST", "scanner": source}]}}
        parent_stages = {"PROPOSALS": {"status": "COMPLETED", "proposal_ids": [proposal["proposal_id"]]}}
        con.execute("INSERT INTO ai_trade_desk_scans(scan_id,job_type,status,stage,stage_results_json,result_json,updated_at) VALUES(?,?,?,?,?,?,?)", (parent_id, "FULL_MARKET_SCAN", "COMPLETED", "PROPOSALS", json.dumps(parent_stages), json.dumps(parent_result), "2026-10-20T00:00:00+00:00"))
        con.execute("INSERT INTO ai_trade_desk_scans(scan_id,job_type,parent_scan_id,status,stage,stage_results_json,result_json,updated_at) VALUES(?,?,?,?,?,?,?,?)", (review_id, "GPT_REVIEW", parent_id, "REVIEW_PENDING", "KANBAN_PUBLICATION", json.dumps({"GPT_ANALYSIS": {"status": "REVIEW_PENDING"}, "KANBAN_PUBLICATION": {"status": "PENDING_AI_REVIEW"}}), json.dumps({"status": "REVIEW_PENDING"}), "2026-10-20T00:00:00+00:00"))
    result = manager.reconcile_review_job(review_id, proposal_store=store)
    assert result["status"] == "COMPLETED"
    assert result["result"]["reviewed_count"] == 1
    assert result["result"]["published_count"] == 1
    assert result["stage_results"]["GPT_ANALYSIS"]["status"] == "COMPLETED"
    assert result["stage_results"]["KANBAN_PUBLICATION"]["status"] == "COMPLETED"
    repeated = manager.reconcile_review_job(review_id, proposal_store=store)
    assert repeated["status"] == "COMPLETED"
    assert len(store.events(proposal["proposal_id"])) == 2


def test_review_job_reconciliation_fails_closed_for_missing_audit_and_shared_source(tmp_path: Path):
    import json

    store = AITradeDeskStore(tmp_path / "decision.db")
    long = {**candidate(), "symbol": "AIG", "strategy": "PRB2", "source_scan_id": "same-observation", "side": "LONG"}
    short = {**candidate(), "symbol": "AIG", "strategy": "LP1", "source_scan_id": "same-observation", "side": "SHORT", "lifecycle_status": "INVALIDATED"}
    proposal = store.ingest(long, idempotency_key="reconcile:long", ai_review_status="PENDING")
    manager = AITradeDeskScanManager(tmp_path / "decision.db", lambda *_: {})
    parent_id, review_id = "market-partial", "review-partial"
    with manager._connection() as con:
        source = {"result": {"active": [{"symbol": "AIG", "scanner": long}], "terminal": [{"symbol": "AIG", "scanner": short}]}}
        stages = {"PROPOSALS": {"status": "COMPLETED", "proposal_ids": [proposal["proposal_id"]]}}
        con.execute("INSERT INTO ai_trade_desk_scans(scan_id,job_type,status,stage,stage_results_json,result_json,updated_at) VALUES(?,?,?,?,?,?,?)", (parent_id, "FULL_MARKET_SCAN", "COMPLETED", "PROPOSALS", json.dumps(stages), json.dumps(source), "2026-10-20T00:00:00+00:00"))
        con.execute("INSERT INTO ai_trade_desk_scans(scan_id,job_type,parent_scan_id,status,stage,stage_results_json,result_json,updated_at) VALUES(?,?,?,?,?,?,?,?)", (review_id, "GPT_REVIEW", parent_id, "REVIEW_PENDING", "KANBAN_PUBLICATION", json.dumps({}), json.dumps({}), "2026-10-20T00:00:00+00:00"))
    result = manager.reconcile_review_job(review_id, proposal_store=store)
    assert result["status"] == "REVIEW_PARTIAL"
    assert result["result"]["reviewed_count"] == 0
    assert "AI_REVIEW_PUBLISHED_AUDIT_MISMATCH" in result["result"]["reconciliation"]["evidence"][0]["reasons"]


def test_pipeline_skips_empty_gpt_review_without_model_call():
    from unittest.mock import patch
    from dashboard_react import server
    from src.decision.ai_trade_desk_scan import _run_ai_trade_desk_pipeline

    class Jobs:
        def status(self, scan_id):
            if scan_id == "review-scan":
                return {"job_type": "GPT_REVIEW", "parent_scan_id": "market-scan"}
            return {"result": {"active": [], "terminal": [], "orders_submitted": 0}}

    stages = []
    with patch.object(server, "_ai_trade_desk_scan_jobs", return_value=Jobs()):
        result = _run_ai_trade_desk_pipeline("review-scan", lambda name, data: stages.append((name, data)), lambda: False)

    assert result["status"] == "SKIPPED_NO_CANDIDATES"
    assert result["reviewed_count"] == 0
    assert result["published_count"] == 0
    assert stages[0][1]["status"] == "SKIPPED_NO_CANDIDATES"
    assert stages[1][1]["published_count"] == 0


def test_reviewable_candidates_attach_only_exact_persisted_proposal_ids(tmp_path: Path):
    from unittest.mock import patch
    from dashboard_react import server
    from src.decision.ai_trade_desk_scan import _reviewable_candidates

    store = AITradeDeskStore(tmp_path / "decision.db")
    source = candidate()
    source.update({"source_scan_id": "authoritative-scan", "scan_timestamp": "2026-10-20T00:00:00Z"})
    proposal = store.ingest(source, idempotency_key="authoritative-review", ai_review_status="PENDING")
    missing = {**source, "symbol": "UNPERSISTED"}

    with patch.object(server, "_ai_trade_desk_store", return_value=store):
        payload = _reviewable_candidates([source, missing])

    assert payload[0]["proposal_id"] == proposal["proposal_id"]
    assert payload[0]["proposal_status"] == "DISCOVERED"
    assert payload[1]["proposal_id"] is None
    assert payload[1]["proposal_status"] == "NOT_PERSISTED"


def test_store_preview_policy_and_ai_review_preserve_source(tmp_path: Path):
    store = AITradeDeskStore(tmp_path / "decision.db")
    source = candidate()
    source.update({"entry": 435.2882, "stop": 427.4009, "target": 451.063, "rr": 2})
    source["position_size"] = 63
    source["risk_pct"] = 0.5
    source["cash_allocation"] = 27423.16
    proposal = store.ingest(source, idempotency_key="review:test", ai_review_status="PENDING")
    preview = calculate_trade_metrics(
        direction="LONG", entry=440, stop=proposal["stop"], target=proposal["target"], rr=2,
        cash_allocation=proposal["cash_allocation"], max_risk_pct=proposal["max_risk_pct"], quantity=proposal["quantity"],
    )
    assert preview["target"] == 465.1982
    assert preview["quantity"] == 10
    assert proposal["source"]["position_size"] == 63
    reviewed = store.publish_ai_review(
        proposal["proposal_id"],
        {
            "source_scan_id": proposal["source_scan_id"], "symbol": proposal["symbol"], "strategy": proposal["strategy"],
            "ai_reasoning": "Source-backed review", "strengths": ["trend"], "weaknesses": ["quality"],
            "risk_observations": ["entry-triggered is not freshness"], "review_result": "REVIEW", "model_provider": "test-provider",
        },
        actor="reviewer",
    )
    assert reviewed["ai_review_status"] == "COMPLETED"
    assert reviewed["source"]["position_size"] == 63
    repeated = store.publish_ai_review(
        proposal["proposal_id"],
        {
            "source_scan_id": proposal["source_scan_id"], "symbol": proposal["symbol"], "strategy": proposal["strategy"],
            "ai_reasoning": "Different retry must not overwrite the first review", "strengths": [], "weaknesses": [],
            "risk_observations": [], "review_result": "REVIEW", "model_provider": "different-provider",
        },
        actor="retry",
    )
    assert repeated["ai_model"] == "test-provider"
    assert len(store.events(proposal["proposal_id"])) == 2


def test_isolated_authenticated_fixture_review_import_is_idempotent(tmp_path: Path):
    from dashboard_react import server
    from src.decision.ai_trade_desk_scan import _register_dashboard_routes
    from src.radar.service import source_observation_id

    _register_dashboard_routes()
    store = AITradeDeskStore(tmp_path / "fixture-only.db")
    fixture = candidate()
    fixture.update({
        "symbol": "FIXTURE_ONLY",
        "source_scan_id": source_observation_id(
            symbol="FIXTURE_ONLY",
            radar_as_of="2026-10-20",
            scan_timestamp="2026-10-20T00:00:00+00:00",
            payload={"signals": [{"strategy": "PRB1", "side": "LONG"}]},
        ),
        "strategy": "PRB1",
    })
    proposal = store.ingest(fixture, idempotency_key="fixture:phase2f", ai_review_status="PENDING")
    review = {
        "proposal_id": proposal["proposal_id"],
        "source_scan_id": proposal["source_scan_id"],
        "symbol": proposal["symbol"],
        "strategy": proposal["strategy"],
        "ai_reasoning": "Fixture-only review generated from the exact source fields; no live signal.",
        "strengths": ["source identity matches", "direction-aware levels are internally consistent"],
        "weaknesses": ["fixture is not a production scan", "no live market freshness claim"],
        "risk_observations": ["human approval remains required", "no duplicate entry without source revalidation"],
        "review_result": "HUMAN_REVIEW_REQUIRED",
        "model_provider": "OpenAI Codex GPT fixture review",
    }
    old_store = server._AI_TRADE_DESK_STORE
    old_jobs = server._AI_TRADE_DESK_SCAN_JOBS
    try:
        server._AI_TRADE_DESK_STORE = store
        server._AI_TRADE_DESK_SCAN_JOBS = type("FixtureJobs", (), {
            "status": lambda self, _scan_id: {"status": "REVIEW_PENDING"},
            "reconcile_review_job": lambda self, _scan_id, proposal_store: {"status": "REVIEW_PARTIAL"},
        })()
        with patch.dict(os.environ, {"AI_TRADE_DESK_AI_TOKEN": "fixture-only-token"}, clear=False):
            client = TestClient(server.app)
            response = client.post(
                "/api/ai-trade-desk/scans/fixture-review/review-import",
                headers={"X-AI-Trade-Desk-Role": "ai", "X-AI-Trade-Desk-Actor": "fixture-codex", "X-AI-Trade-Desk-Token": "fixture-only-token"},
                json={"reviews": [review]},
            )
            repeated = client.post(
                "/api/ai-trade-desk/scans/fixture-review/review-import",
                headers={"X-AI-Trade-Desk-Role": "ai", "X-AI-Trade-Desk-Actor": "fixture-codex", "X-AI-Trade-Desk-Token": "fixture-only-token"},
                json={"reviews": [review]},
            )
    finally:
        server._AI_TRADE_DESK_STORE = old_store
        server._AI_TRADE_DESK_SCAN_JOBS = old_jobs

    assert response.status_code == 200
    assert repeated.status_code == 200
    assert response.json()["count"] == 1
    assert repeated.json()["count"] == 1
    persisted = store.get(proposal["proposal_id"])
    assert persisted["ai_review_status"] == "COMPLETED"
    assert persisted["ai_model"] == "OpenAI Codex GPT fixture review"
    assert len(store.events(proposal["proposal_id"])) == 2
    assert response.json()["orders_submitted"] == 0
    assert response.json()["execution_intents_created"] == 0


def test_store_rejects_candidate_without_source_provenance(tmp_path: Path):
    store = AITradeDeskStore(tmp_path / "decision.db")
    source = candidate()
    source.pop("source_scan_id", None)

    with pytest.raises(ValueError, match="SOURCE_PROVENANCE_UNVERIFIED"):
        store.ingest(source, idempotency_key="missing-source")


def test_radar_scanner_source_identity_is_carried_into_returned_signal(tmp_path: Path):
    from src.radar.models import RadarResult, RadarSettings
    from src.radar.service import RadarService, source_observation_id
    from src.storage.trend_radar_store import TrendRadarStore

    radar_store = TrendRadarStore(tmp_path / "radar.db")
    radar_store.upsert(RadarResult(symbol="FIXTURE", as_of="2026-10-20", trend="GREEN", trend_score=80, radar_score=80, created_at="2026-10-20T12:00:00+00:00"))
    service = RadarService(
        store=radar_store,
        settings=RadarSettings(min_avg_dollar_volume=1_000_000),
        scanner_runner=lambda _symbol: {
            "source_scan_id": "scanner-run-fixture",
            "signals": [{"strategy": "PRB1", "side": "LONG", "lifecycle_status": "PENDING_ENTRY"}],
            "signals_found": 1,
        },
    )

    result = service.scan_symbol("FIXTURE")

    assert result["source_scan_id"] == "scanner-run-fixture"
    assert result["signals"][0]["source_scan_id"] == "scanner-run-fixture"

    generated = source_observation_id(
        symbol="FIXTURE",
        radar_as_of="2026-10-20",
        scan_timestamp="2026-10-20T12:00:00+00:00",
        payload={"signals": [{"strategy": "PRB1", "side": "LONG"}]},
    )
    assert generated == source_observation_id(
        symbol="FIXTURE",
        radar_as_of="2026-10-20",
        scan_timestamp="2026-10-20T12:00:00+00:00",
        payload={"signals": [{"strategy": "PRB1", "side": "LONG"}]},
    )
    assert generated != source_observation_id(
        symbol="FIXTURE",
        radar_as_of="2026-10-20",
        scan_timestamp="2026-10-20T12:00:00+00:00",
        payload={"signals": [{"strategy": "PRB1", "side": "SHORT"}]},
    )


def test_missing_scanner_id_gets_persisted_observation_identity(tmp_path: Path):
    from src.radar.models import RadarResult, RadarSettings
    from src.radar.service import RadarService
    from src.storage.trend_radar_store import TrendRadarStore

    radar_store = TrendRadarStore(tmp_path / "radar.db")
    radar_store.upsert(RadarResult(symbol="FIXTURE", as_of="2026-10-20", trend="GREEN", trend_score=80, radar_score=80, created_at="2026-10-20T12:00:00+00:00"))
    service = RadarService(
        store=radar_store,
        settings=RadarSettings(min_avg_dollar_volume=1_000_000),
        scanner_runner=lambda _symbol: {
            "scan_timestamp": "2026-10-20T12:00:00+00:00",
            "signals": [{"strategy": "PRB1", "side": "LONG", "lifecycle_status": "PENDING_ENTRY"}],
            "signals_found": 1,
        },
    )

    result = service.scan_symbol("FIXTURE")
    persisted = service.list_results(limit=10)

    assert result["source_scan_id"].startswith("scanner-observation-")
    assert result["signals"][0]["source_scan_id"] == result["source_scan_id"]
    row = next(item for item in persisted if item["symbol"] == "FIXTURE")
    assert row["scanner_source_scan_id"] == result["source_scan_id"]


def test_reused_proposal_does_not_overwrite_source_provenance(tmp_path: Path):
    store = AITradeDeskStore(tmp_path / "decision.db")
    source = candidate()
    first = store.ingest(source, idempotency_key="same-observation")
    changed = {**source, "source_scan_id": "different-authoritative-source"}

    reused = store.ingest(changed, idempotency_key="same-observation")

    assert reused["proposal_id"] == first["proposal_id"]
    assert reused["source_scan_id"] == first["source_scan_id"]

