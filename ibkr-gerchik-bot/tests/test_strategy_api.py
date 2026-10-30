from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone

import dashboard_react.server as server
import src.decision.strategy_control as strategy_control
from src.decision.audit import DecisionAudit
from src.decision.models import AgentMode, DecisionAction, DecisionCandidate, DecisionRequest, DecisionResponse, DecisionSnapshot
from src.decision.strategy_control import save_snapshot


class _Request:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class _QueryRequest:
    query_params = {}


def _settings(runtime_dir: Path):
    return SimpleNamespace(paths=SimpleNamespace(runtime_dir=runtime_dir))


def test_strategy_api_is_registry_backed_and_queues_manual_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SETTINGS", _settings(tmp_path))
    monkeypatch.setattr(strategy_control, "SETTINGS", _settings(tmp_path))
    catalog = server.api_decision_lab_strategies()
    assert {item["id"] for item in catalog["strategies"]} == {
        "all",
        "stock_screener",
        "bmsb",
        "gaussian",
        "gerchik_router",
    }
    assert server.api_decision_lab_control()["analysis_mode"] == "manual"
    updated = asyncio.run(
        server.api_decision_lab_update_control(
            _Request({"analysis_mode": "manual", "strategy_source": "bmsb"})
        )
    )
    assert updated["strategy_source"] == "bmsb"
    save_snapshot(
        {
            "analysis_snapshot_id": "analysis-latest",
            "scan_id": "analysis-latest",
            "completed_at": "2026-09-28T14:00:00+00:00",
            "source_counts": {"bmsb": 0},
            "source_status": {"bmsb": "NO_CANDIDATES"},
            "source_metadata": {
                "bmsb": {
                    "available": True,
                    "status": "NO_CANDIDATES",
                    "source_timestamp": "2026-09-28T14:00:00+00:00",
                }
            },
            "sources": {"bmsb": []},
        },
        runtime_dir=tmp_path,
    )
    monkeypatch.setattr(server, "refresh_current_source_snapshot", lambda **kwargs: {})
    queued = asyncio.run(server.api_decision_lab_run(_Request({})))
    assert queued["run"]["status"] == "QUEUED"
    assert queued["run"]["strategy_selection"] == "bmsb"
    assert queued["run"]["scan_id"] == "analysis-latest"
    assert queued["run"]["analysis_snapshot_id"] == "analysis-latest"
    assert queued["run"]["snapshot_timestamp"] == "2026-09-28T14:00:00+00:00"
    assert queued["state"] == "QUEUED"


def test_strategy_api_does_not_create_run_without_source_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SETTINGS", _settings(tmp_path))
    monkeypatch.setattr(strategy_control, "SETTINGS", _settings(tmp_path))
    asyncio.run(
        server.api_decision_lab_update_control(
            _Request({"analysis_mode": "manual", "strategy_source": "bmsb"})
        )
    )
    monkeypatch.setattr(
        server,
        "refresh_current_source_snapshot",
        lambda **kwargs: save_snapshot(
            {
                "analysis_snapshot_id": "analysis-unavailable",
                "scan_id": "analysis-unavailable",
                "source_counts": {"bmsb": 0},
                "source_status": {"bmsb": "SOURCE_UNAVAILABLE"},
                "source_metadata": {"bmsb": {"available": False, "status": "SOURCE_UNAVAILABLE"}},
                "sources": {"bmsb": []},
            },
            runtime_dir=tmp_path,
        ),
    )
    try:
        asyncio.run(server.api_decision_lab_run(_Request({})))
    except server.HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail["error"] == "SOURCE_DATA_UNAVAILABLE"
    else:
        raise AssertionError("A manual run must not be created without a completed source snapshot")
    assert strategy_control.list_runs(runtime_dir=tmp_path) == []


def test_strategy_api_rejects_manual_run_in_auto_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SETTINGS", _settings(tmp_path))
    monkeypatch.setattr(strategy_control, "SETTINGS", _settings(tmp_path))
    asyncio.run(
        server.api_decision_lab_update_control(
            _Request({"analysis_mode": "auto", "strategy_source": "all"})
        )
    )
    try:
        asyncio.run(server.api_decision_lab_run(_Request({})))
    except server.HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("AUTO mode must reject the manual run endpoint")


def test_strategy_api_surface_does_not_connect_to_ibkr():
    source = Path(server.__file__).read_text(encoding="utf-8")
    section = source[source.index("def api_decision_lab_strategies"):source.index("def api_decision_lab_test_provider")]
    assert "IBKRClient" not in section
    assert "connect(" not in section
    assert "place_" not in section


def test_decision_inspector_api_returns_sanitized_detail_without_broker_connection(tmp_path, monkeypatch):
    database_path = tmp_path / "decision_lab.db"
    monkeypatch.setattr(
        server,
        "SETTINGS",
        SimpleNamespace(decision_agent=SimpleNamespace(database_path=database_path)),
    )
    audit = DecisionAudit(database_path)
    candidate = DecisionCandidate(
        candidate_id="inspector-candidate-1",
        created_at=datetime.now(timezone.utc),
        asset_class="stock",
        symbol="AAPL",
        strategy="rebound",
        direction="long",
        entry=100.0,
        stop=98.0,
        target=106.0,
        reward_risk=3.0,
        metadata={"strategy_source": "bmsb", "account_id": "DU5454348"},
    )
    request = DecisionRequest(
        decision_id="inspector-decision-1",
        agent_id="GERCHIK_LLM_TEST",
        mode=AgentMode.SHADOW,
        provider="fake",
        model="fake-model",
        snapshot=DecisionSnapshot.from_candidate(candidate, session="closed"),
        allowed_actions=("WAIT", "REJECT"),
        run_id="inspector-run-1",
    )
    audit.record_candidate(candidate)
    audit.record_decision(
        request,
        DecisionResponse(action=DecisionAction.WAIT, confidence=0.6),
        provider_observation={
            "request_payload": {"user_prompt": {"account_id": "DU5454348"}},
            "raw_model_text_sanitized": "account_id=DU5454348 {\"action\":\"WAIT\"}",
            "parsed_response_json": {"action": "WAIT"},
        },
    )

    payload = server.api_decision_lab_decision("inspector-decision-1")
    assert payload["decision"]["decision_id"] == "inspector-decision-1"
    assert "DU5454348" not in str(payload)
    assert "prompt_payload_json" in payload["decision"]

    table_payload = server.api_decision_lab_decisions(_QueryRequest())
    assert table_payload["decisions"][0]["decision_id"] == "inspector-decision-1"
    assert "prompt_payload_json" not in table_payload["decisions"][0]
    monkeypatch.setattr(server, "list_runs", lambda limit=200: [{"run_id": "inspector-run-1", "status": "PARTIAL"}])
    run_payload = server.api_decision_lab_run_detail("inspector-run-1")
    assert run_payload["run"]["status"] == "PARTIAL"
    assert run_payload["decisions"][0]["decision_id"] == "inspector-decision-1"

    missing = False
    try:
        server.api_decision_lab_decision("missing-decision")
    except server.HTTPException as exc:
        missing = exc.status_code == 404
    assert missing
