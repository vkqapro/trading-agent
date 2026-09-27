from __future__ import annotations

from datetime import datetime, timezone

from src.decision.audit import DecisionAudit
from src.decision.models import (
    AgentMode,
    DecisionAction,
    DecisionCandidate,
    DecisionRequest,
    DecisionResponse,
    DecisionSnapshot,
)


def _request() -> DecisionRequest:
    candidate = DecisionCandidate(
        candidate_id="audit-candidate-1",
        created_at=datetime.now(timezone.utc),
        asset_class="stock",
        symbol="AAPL",
        strategy="rebound",
        direction="long",
        entry=100.0,
        stop=98.0,
        target=106.0,
        reward_risk=3.0,
    )
    return DecisionRequest(
        decision_id="audit-decision-1",
        agent_id="GERCHIK_LLM_01",
        mode=AgentMode.SHADOW,
        provider="local_openai",
        model="test-model",
        snapshot=DecisionSnapshot.from_candidate(
            candidate,
            session="intraday",
            current_price=100.1,
            data_age_seconds=1.0,
        ),
        allowed_actions=("ENTER", "WAIT", "REJECT"),
    )


def test_audit_persists_candidate_snapshot_decision_risk_and_execution(tmp_path) -> None:
    audit = DecisionAudit(tmp_path / "decision_lab.db")
    request = _request()
    response = DecisionResponse(
        action=DecisionAction.ENTER,
        confidence=0.8,
        ranked_actions=(("ENTER", 0.8), ("WAIT", 0.15), ("REJECT", 0.05)),
        reason_codes=("CLEAN_SETUP",),
        summary="Clean setup.",
    )

    audit.record_candidate(request.snapshot.candidate)
    audit.record_snapshot(request)
    audit.record_decision(request, response, latency_ms=21.5, token_usage={"input": 100, "output": 20})
    audit.record_risk(
        decision_id=request.decision_id,
        candidate_id=request.snapshot.candidate.candidate_id,
        approved=True,
        reasons=(),
        quantity=10,
        risk_amount=20.0,
    )
    audit.record_execution(
        decision_id=request.decision_id,
        candidate_id=request.snapshot.candidate.candidate_id,
        status="paper_simulated",
        order_ids={"entry": 0},
        position_id="paper-position-1",
    )

    rows = audit.list_decisions()
    assert len(rows) == 1
    assert rows[0]["action"] == "ENTER"
    assert rows[0]["risk_approved"] == 1
    assert rows[0]["execution_status"] == "paper_simulated"
    assert audit.status()["decisions"] == 1


def test_audit_redacts_secret_context_before_persisting(tmp_path) -> None:
    audit = DecisionAudit(tmp_path / "decision_lab.db")
    request = _request()
    unsafe = DecisionSnapshot.from_candidate(
        request.snapshot.candidate,
        session="intraday",
        context={"api_key": "secret", "news": "clear"},
    )
    request = DecisionRequest(
        decision_id=request.decision_id,
        agent_id=request.agent_id,
        mode=request.mode,
        provider=request.provider,
        model=request.model,
        snapshot=unsafe,
        allowed_actions=request.allowed_actions,
    )

    audit.record_snapshot(request)

    raw = audit.connection().execute(
        "SELECT snapshot_json FROM decision_snapshots WHERE decision_id = ?",
        (request.decision_id,),
    ).fetchone()[0]
    assert "secret" not in raw
    assert "api_key" not in raw


def test_audit_filters_history_without_exposing_database_path(tmp_path) -> None:
    audit = DecisionAudit(tmp_path / "decision_lab.db")
    request = _request()
    audit.record_candidate(request.snapshot.candidate)
    audit.record_snapshot(request)
    audit.record_decision(
        request,
        DecisionResponse(action=DecisionAction.WAIT, confidence=0.4),
        latency_ms=4.0,
    )

    rows = audit.list_decisions(action="WAIT", symbol="AAPL")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"
    assert str(tmp_path) not in str(rows[0])
