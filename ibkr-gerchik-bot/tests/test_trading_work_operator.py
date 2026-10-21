from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tools.trading_work_operator import AnalyticsRecoverySpec, OperatorError, TradingWorkOperator, _base_url


def _candidate(proposal_id: str = "aitd-fixture") -> dict:
    return {
        "proposal_id": proposal_id,
        "source_scan_id": "fixture:market:1",
        "gpt_review_job_id": "fixture-review",
        "parent_market_scan_id": "fixture-market",
        "opportunity_id": "opportunity-fixture",
        "proposal_origin_source_scan_id": "fixture:market:0",
        "reviewed_observation_id": "observation-fixture",
        "reviewed_source_candidate_id": "candidate-fixture",
        "symbol": "FIXTURE_ONLY",
        "strategy": "PRB1",
        "side": "LONG",
        "direction": "LONG",
        "lifecycle_status": "ENTRY_TRIGGERED",
        "entry": 81.08,
        "stop": 79.92,
        "target": 84.27,
        "level": 79.0,
        "level_type": "resistance",
        "planned_entry": 81.08,
        "entry_touch_time": "2026-10-20T13:30:00+00:00",
    }


def _review(proposal_id: str = "aitd-fixture") -> dict:
    return {
        "proposal_id": proposal_id,
        "source_scan_id": "fixture:market:1",
        "gpt_review_job_id": "fixture-review",
        "parent_market_scan_id": "fixture-market",
        "opportunity_id": "opportunity-fixture",
        "proposal_origin_source_scan_id": "fixture:market:0",
        "reviewed_observation_id": "observation-fixture",
        "reviewed_source_candidate_id": "candidate-fixture",
        "symbol": "FIXTURE_ONLY",
        "strategy": "PRB1",
        "direction": "LONG",
        "lifecycle_status": "ENTRY_TRIGGERED",
        "ai_reasoning": "Fixture-only reasoning; no live market claim.",
        "strengths": ["source identity matches"],
        "weaknesses": ["fixture data only"],
        "risk_observations": ["human approval remains required"],
        "review_result": "HUMAN_REVIEW_REQUIRED",
        "review_priority": "LOW",
        "model_provider": "OpenAI Codex fixture",
        "review_timestamp": "2026-10-20T00:00:00Z",
    }


def _healthy_operations(*, broker_status: str = "HEALTHY", market_status: str = "HEALTHY", market_reason: str | None = None) -> dict:
    return {
        "overall_status": "READY_FOR_MARKET_OPEN",
        "broker": {"status": broker_status, "account_identity_verified": broker_status == "HEALTHY", "reason_code": None if broker_status == "HEALTHY" else "IBKR_LOGIN_REQUIRED"},
        "services": {
            "market_data": {"status": market_status, "operational_state": market_status, "reason_code": market_reason, "heartbeat": {"fresh": market_status == "HEALTHY", "lock_owner_matches_process": market_reason != "LOCK_OWNER_MISMATCH"}},
            "strategy_scanner_mcp": {"status": "HEALTHY", "operational_state": "HEALTHY"},
            "trading_bot_mcp": {"status": "HEALTHY", "operational_state": "HEALTHY"},
            "execute_worker": {"status": "STOPPED", "operational_state": "STOPPED"},
        },
        "orders_submitted": 0,
        "approvals_created": 0,
        "execution_intents_created": 0,
    }


def _windows_host() -> dict:
    return {
        "local_host": "LOCAL_WINDOWS_HOST",
        "host_os": "Windows",
        "repository_access": True,
        "local_process_visibility": {"status": "available", "reason": None},
    }


def _preflight_requester(*, active: bool = False, dashboard_available: bool = True):
    def requester(path, **_kwargs):
        if not dashboard_available:
            raise OperatorError("Trading Work API is unavailable")
        responses = {
            "/api/services": {"services": {}, "orders_submitted": 0},
            "/api/ai-trade-desk/scans?limit=20": {"runs": [{"scan_id": "running", "status": "RUNNING"}] if active else []},
            "/api/ai-trade-desk/proposals": {"proposals": [_candidate()], "orders_submitted": 0, "approvals_created": 0, "execution_intents_created": 0},
            "/api/radar/top?limit=1": {"read_only": True, "orders_submitted": 0},
            "/api/market-screener/health": {"read_only": True, "orders_submitted": 0},
            "/api/daily-trader/health": {"read_only": True, "orders_submitted": 0},
        }
        return responses[path]
    return requester


@pytest.mark.parametrize("url", ["https://example.com", "http://10.0.0.5:8550", "http://127.0.0.1:8550/path"])
def test_operator_rejects_non_loopback_or_path_urls(url: str):
    with pytest.raises(ValueError):
        _base_url(url)


def test_preflight_uses_read_only_results_and_reports_active_scan():
    operator = TradingWorkOperator(requester=_preflight_requester(active=True), environment_probe=_windows_host)
    with patch.object(operator, "_ai_token_configured", return_value=True):
        result = operator.preflight(operations_probe=_healthy_operations)

    assert result["status"] == "SCAN_ALREADY_ACTIVE"
    assert result["trade_desk"]["active_scan_ids"] == ["running"]
    assert result["trade_desk"]["ai_ingestion_auth"] == "CONFIGURED"


def test_market_scan_requires_explicit_per_run_authorization():
    operator = TradingWorkOperator(requester=lambda *_args, **_kwargs: pytest.fail("request must not run"))
    with pytest.raises(OperatorError, match="explicit per-run"):
        operator.submit_full_market_scan(authorized=False)


def test_preflight_reports_every_dependency_when_dashboard_is_unavailable():
    operator = TradingWorkOperator(
        requester=_preflight_requester(dashboard_available=False),
        operations_probe=_healthy_operations,
        environment_probe=_windows_host,
    )
    result = operator.preflight()

    assert result["status"] == "BLOCKED"
    assert result["dependencies"]["dashboard_api"]["status"] == "UNAVAILABLE"
    assert result["dependencies"]["strategy_scanner_mcp"]["status"] == "HEALTHY"
    assert result["dependencies"]["execution_worker"]["informational_only"] is True
    assert result["trade_desk"]["active_scan_ids"] is None
    assert result["trade_desk"]["active_scan_status"] == "UNVERIFIED"


def test_preflight_does_not_tie_ai_token_readiness_to_dashboard_availability():
    operator = TradingWorkOperator(
        requester=_preflight_requester(dashboard_available=False),
        operations_probe=_healthy_operations,
        environment_probe=_windows_host,
    )
    with patch("tools.trading_work_operator._configured_ai_token", return_value="fixture-token"):
        result = operator.preflight()

    assert result["dependencies"]["ai_authentication"]["status"] == "CONFIGURED"


def test_preflight_keeps_execution_safety_separate_from_analytics_scan_readiness():
    operations = _healthy_operations()
    operations["safety"] = {"status": "ACTION_REQUIRED"}
    operations["execution_boundary"] = {"safe_to_start": False, "status": "ACTION_REQUIRED"}
    operator = TradingWorkOperator(requester=_preflight_requester(), operations_probe=lambda: operations, environment_probe=_windows_host)

    result = operator.preflight()

    assert result["analytics_scan_readiness"]["status"] == "READY"
    assert result["broker_execution_readiness"]["status"] == "NOT_READY"
    assert "PAPER_TRADING=true" in result["broker_execution_readiness"]["safety_rule"]


def test_preflight_identifies_unverified_local_host_without_starting_anything():
    operator = TradingWorkOperator(
        requester=_preflight_requester(),
        operations_probe=_healthy_operations,
        environment_probe=lambda: {"local_host": "LOCAL_HOST_UNVERIFIED", "repository_access": False},
    )

    assert operator.preflight()["status"] == "LOCAL_HOST_UNVERIFIED"


def test_market_scan_is_blocked_when_analytics_preflight_is_not_ready():
    operator = TradingWorkOperator(
        requester=_preflight_requester(dashboard_available=False),
        operations_probe=_healthy_operations,
        environment_probe=_windows_host,
    )
    with pytest.raises(OperatorError, match="analytics preflight"):
        operator.submit_full_market_scan(authorized=True)


def _scan_submission_requester(*, daily_responses=None, active_on_final=False):
    base = _preflight_requester()
    daily_responses = iter(daily_responses or [{"status": "ok", "read_only": True, "orders_submitted": 0}])
    scan_calls = 0
    post_calls = 0

    def requester(path, **kwargs):
        nonlocal scan_calls, post_calls
        if path == "/api/daily-trader/health":
            value = next(daily_responses)
            if isinstance(value, Exception):
                raise value
            return value
        if path == "/api/ai-trade-desk/scans?limit=20":
            scan_calls += 1
            if active_on_final and scan_calls > 1:
                return {"runs": [{"scan_id": "appeared", "status": "RUNNING"}]}
        if path == "/api/ai-trade-desk/scans/full-market":
            post_calls += 1
            return {"scan_id": "new-scan", "status": "QUEUED", "orders_submitted": 0}
        return base(path, **kwargs)

    requester.calls = lambda: (scan_calls, post_calls)
    return requester


def test_submission_revalidates_transient_daily_trader_failure_then_submits_once():
    requester = _scan_submission_requester(
        daily_responses=[OperatorError("Trading Work API is unavailable"), {"status": "ok", "read_only": True, "orders_submitted": 0}],
    )
    sleeps = []
    operator = TradingWorkOperator(requester=requester, operations_probe=_healthy_operations, environment_probe=_windows_host, sleep=sleeps.append)

    result = operator.submit_full_market_scan(authorized=True)

    assert result["scan_id"] == "new-scan"
    assert len(sleeps) == 1
    assert requester.calls() == (3, 1)


def test_submission_keeps_persistent_daily_trader_not_ready_blocked_with_diagnostics():
    requester = _scan_submission_requester(
        daily_responses=[{"status": "degraded", "read_only": True, "orders_submitted": 0}],
    )
    sleeps = []
    operator = TradingWorkOperator(requester=requester, operations_probe=_healthy_operations, environment_probe=_windows_host, sleep=sleeps.append)

    with pytest.raises(OperatorError, match="DAILY_TRADER_NOT_READY") as raised:
        operator.submit_full_market_scan(authorized=True)

    assert len(sleeps) == 0
    assert "response_time_ms" in str(raised.value)
    assert requester.calls() == (1, 0)


@pytest.mark.parametrize(
    ("message", "transient"),
    [
        ("Trading Work API returned HTTP 504 for GET /api/daily-trader/health", True),
        ("database is locked", True),
        ("Trading Work API returned HTTP 401 for GET /api/daily-trader/health", False),
        ("unexpected health state", False),
    ],
)
def test_probe_classifies_transient_and_persistent_daily_trader_failures(message, transient):
    operator = TradingWorkOperator(requester=lambda *_args, **_kwargs: (_ for _ in ()).throw(OperatorError(message)))

    result = operator._probe("/api/daily-trader/health")

    assert result["status"] == "UNAVAILABLE"
    assert result["transient"] is transient
    assert result["endpoint"].endswith("/api/daily-trader/health")
    assert result["checked_at"]
    assert result["response_time_ms"] >= 0


def test_submission_blocks_if_active_scan_appears_during_final_revalidation():
    requester = _scan_submission_requester(active_on_final=True)
    operator = TradingWorkOperator(requester=requester, operations_probe=_healthy_operations, environment_probe=_windows_host)

    with pytest.raises(OperatorError, match="active scan appeared"):
        operator.submit_full_market_scan(authorized=True)

    assert requester.calls() == (2, 0)


def test_recovery_requires_explicit_safe_start_authorization():
    operator = TradingWorkOperator(requester=_preflight_requester(), operations_probe=_healthy_operations, environment_probe=_windows_host)
    with pytest.raises(OperatorError, match="authorize-safe-start"):
        operator.recover_analytics(authorized=False)


def test_recovery_refuses_unknown_ownership_and_never_calls_launcher(monkeypatch):
    calls = []
    operator = TradingWorkOperator(
        requester=_preflight_requester(dashboard_available=False),
        operations_probe=lambda: _healthy_operations(),
        environment_probe=_windows_host,
        recovery_launcher=lambda spec: calls.append(spec.key) or {"ok": True},
    )
    monkeypatch.setattr(operator, "_safe_to_recover_service", lambda key, _preflight: (False, "OWNERSHIP_AMBIGUOUS"))
    result = operator.recover_analytics(authorized=True)

    assert calls == []
    assert {item["reason_code"] for item in result["recovery_actions"]} == {"OWNERSHIP_AMBIGUOUS"}


def test_recovery_starts_only_missing_approved_dashboard_then_rechecks(monkeypatch):
    before = {
        "status": "BLOCKED", "execution_environment": _windows_host(),
        "dependencies": {
            "dashboard_api": {"status": "UNAVAILABLE"},
            "strategy_scanner_mcp": {"status": "HEALTHY"},
            "market_data": {"status": "HEALTHY"},
            "ibkr_paper_gateway": {"status": "HEALTHY"},
        },
    }
    after = {**before, "status": "READY_FOR_OPERATOR_REVIEW", "dependencies": {**before["dependencies"], "dashboard_api": {"status": "HEALTHY"}}}
    launches: list[str] = []
    operator = TradingWorkOperator(recovery_launcher=lambda spec: launches.append(spec.key) or {"ok": True})
    calls = iter((before, after))
    monkeypatch.setattr(operator, "preflight", lambda: next(calls))
    monkeypatch.setattr(operator, "_launcher_is_approved", lambda spec: (True, None))

    result = operator.recover_analytics(authorized=True)

    assert launches == ["dashboard"]
    assert result["status"] == "READY_FOR_OPERATOR_REVIEW"
    assert result["recovery_actions"][0]["action"] == "START_REQUESTED"


def test_market_data_recovery_refuses_login_required_and_lock_owner_mismatch():
    base = {"execution_environment": _windows_host(), "dependencies": {"ibkr_paper_gateway": {"status": "WAITING_FOR_IBKR"}, "market_data": {"status": "UNAVAILABLE", "reason_code": "LOCK_OWNER_MISMATCH", "heartbeat": {"lock_owner_matches_process": False}}}}
    assert TradingWorkOperator._safe_to_recover_service("market_data", base) == (False, "IBKR_LOGIN_REQUIRED")
    base["dependencies"]["ibkr_paper_gateway"] = {"status": "HEALTHY"}
    assert TradingWorkOperator._safe_to_recover_service("market_data", base) == (False, "MARKET_DATA_OWNERSHIP_MISMATCH")


def test_preflight_keeps_scanner_mcp_and_ibkr_login_blockers_separate():
    operations = _healthy_operations(broker_status="WAITING_FOR_IBKR")
    operations["services"]["strategy_scanner_mcp"] = {"status": "STOPPED", "operational_state": "STOPPED", "reason_code": None}
    operator = TradingWorkOperator(requester=_preflight_requester(), operations_probe=lambda: operations, environment_probe=_windows_host)

    result = operator.preflight()

    assert result["status"] == "BLOCKED"
    assert {"ibkr_paper_gateway", "strategy_scanner_mcp"}.issubset(result["blocked_dependencies"])
    assert result["dependencies"]["ibkr_paper_gateway"]["reason_code"] == "IBKR_LOGIN_REQUIRED"


def test_preflight_only_labels_persisted_analysis_when_data_is_fresh_and_explicitly_read_only():
    operator = TradingWorkOperator(
        requester=_preflight_requester(),
        operations_probe=lambda: _healthy_operations(broker_status="WAITING_FOR_IBKR"),
        environment_probe=_windows_host,
    )

    result = operator.preflight()

    assert result["persisted_data_mode"] == "PERSISTED_ANALYSIS_ONLY"
    assert result["status"] == "BLOCKED"


def test_recovery_refuses_duplicate_dashboard_listener_ownership(monkeypatch):
    preflight = {
        "execution_environment": _windows_host(),
        "dependencies": {"dashboard_api": {"status": "UNAVAILABLE", "ownership_reason_code": "DUPLICATE_PROCESS_DETECTED"}},
    }
    assert TradingWorkOperator._safe_to_recover_service("dashboard", preflight) == (False, "OWNERSHIP_AMBIGUOUS")


def test_recovery_launcher_failure_is_reported_and_never_retried(monkeypatch):
    before = {
        "status": "BLOCKED", "execution_environment": _windows_host(),
        "dependencies": {
            "dashboard_api": {"status": "UNAVAILABLE"},
            "strategy_scanner_mcp": {"status": "HEALTHY"},
            "market_data": {"status": "HEALTHY"},
            "ibkr_paper_gateway": {"status": "HEALTHY"},
        },
    }
    launches: list[str] = []
    operator = TradingWorkOperator(recovery_launcher=lambda spec: launches.append(spec.key) or {"ok": False, "reason_code": "FIXTURE_LAUNCH_FAILURE"})
    calls = iter((before, before))
    monkeypatch.setattr(operator, "preflight", lambda: next(calls))
    monkeypatch.setattr(operator, "_launcher_is_approved", lambda spec: (True, None))

    result = operator.recover_analytics(authorized=True)

    assert launches == ["dashboard"]
    assert result["recovery_actions"][0]["action"] == "NOT_STARTED"
    assert result["recovery_actions"][0]["reason_code"] == "FIXTURE_LAUNCH_FAILURE"


def test_review_job_is_located_before_another_is_created():
    calls = []

    def requester(path, **kwargs):
        calls.append((path, kwargs))
        return {"runs": [{"scan_id": "existing-review", "job_type": "GPT_REVIEW", "parent_scan_id": "market-1", "status": "REVIEW_PENDING"}]}

    operator = TradingWorkOperator(requester=requester)
    result = operator.ensure_gpt_review_job("market-1", authorized=False)

    assert result["scan_id"] == "existing-review"
    assert [call[0] for call in calls] == ["/api/ai-trade-desk/scans?limit=100"]


def test_review_job_creation_requires_authorization_then_uses_exact_parent_scan_id():
    calls = []

    def requester(path, **kwargs):
        calls.append((path, kwargs))
        if path.endswith("?limit=100"):
            return {"runs": []}
        return {"scan_id": "new-review", "job_type": "GPT_REVIEW", "parent_scan_id": kwargs["payload"]["parent_scan_id"], "status": "QUEUED"}

    operator = TradingWorkOperator(requester=requester)
    with pytest.raises(OperatorError, match="review job creation"):
        operator.ensure_gpt_review_job("market-2", authorized=False)

    result = operator.ensure_gpt_review_job("market-2", authorized=True)
    assert result["parent_scan_id"] == "market-2"
    assert calls[-1][0] == "/api/ai-trade-desk/scans/review"
    assert calls[-1][1]["payload"] == {"parent_scan_id": "market-2"}


def test_validate_reviews_requires_exact_persisted_source_identity():
    operator = TradingWorkOperator(requester=lambda *_args, **_kwargs: {})
    payload = {"review_status": "AI_REVIEW_PENDING", "candidates": [_candidate()]}

    assert operator.validate_reviews(payload, [_review()])[0]["proposal_id"] == "aitd-fixture"
    bad = _review()
    bad["symbol"] = "OTHER"
    with pytest.raises(OperatorError, match="authoritative candidate"):
        operator.validate_reviews(payload, [bad])


def test_validate_reviews_enriches_legacy_reasoning_only_with_payload_identity():
    operator = TradingWorkOperator(requester=lambda *_args, **_kwargs: {})
    payload = {"review_status": "AI_REVIEW_PENDING", "candidates": [_candidate()]}
    legacy = _review()
    for field in (
        "gpt_review_job_id", "parent_market_scan_id", "opportunity_id",
        "proposal_origin_source_scan_id", "reviewed_observation_id",
        "reviewed_source_candidate_id", "direction", "lifecycle_status",
    ):
        legacy.pop(field)

    validated = operator.validate_reviews(payload, [legacy])[0]

    assert validated["source_scan_id"] == "fixture:market:1"
    assert validated["reviewed_observation_id"] == "observation-fixture"
    assert validated["proposal_origin_source_scan_id"] == "fixture:market:0"


def test_validate_reviews_reports_unverified_source_provenance():
    operator = TradingWorkOperator(requester=lambda *_args, **_kwargs: {})
    payload = {"review_status": "AI_REVIEW_PENDING", "candidates": [{**_candidate(), "source_scan_id": ""}]}
    review = _review()

    with pytest.raises(OperatorError, match="SOURCE_PROVENANCE_UNVERIFIED") as raised:
        operator.validate_reviews(payload, [review])

    assert "aitd-fixture" in str(raised.value)


def test_publish_uses_ai_token_only_for_import_and_verifies_audit_event():
    calls = []

    def requester(path, **kwargs):
        calls.append((path, kwargs))
        if path.endswith("/review-import"):
            return {"published": [{"proposal_id": "aitd-fixture"}], "count": 1, "orders_submitted": 0, "execution_intents_created": 0}
        if path == "/api/ai-trade-desk/proposals/aitd-fixture":
            return {"events": [{"event_type": "AI_REVIEW_PUBLISHED"}], "orders_submitted": 0}
        raise AssertionError(path)

    operator = TradingWorkOperator(requester=requester)
    payload = {"review_status": "AI_REVIEW_PENDING", "candidates": [_candidate()]}
    with patch("tools.trading_work_operator._configured_ai_token", return_value="fixture-secret"):
        result = operator.publish_reviews("fixture-review", payload, [_review()])

    assert result["count"] == 1
    assert calls[0][1]["role"] == "ai"
    assert calls[0][1]["ai_token"] == "fixture-secret"
    assert calls[1][1]["ai_token"] is None


def test_isolated_operator_round_trip_uses_real_trade_desk_routes(tmp_path):
    from dashboard_react import server
    from src.decision.ai_trade_desk import AITradeDeskStore
    from src.decision.ai_trade_desk_scan import _register_dashboard_routes

    _register_dashboard_routes()
    source = _candidate()
    source.update({"side": "LONG", "scanner_status": "CANDIDATE", "lifecycle_status": "ENTRY_TRIGGERED", "position_size": 12, "risk_pct": 5, "rr": 2.75})
    store = AITradeDeskStore(tmp_path / "isolated-operator.db")
    proposal = store.ingest(source, idempotency_key="fixture:operator", ai_review_status="PENDING")

    class Jobs:
        def status(self, scan_id):
            if scan_id == "fixture-review":
                return {"status": "REVIEW_PENDING", "parent_scan_id": "fixture-market"}
            if scan_id == "fixture-market":
                return {"status": "COMPLETED", "result": {"result": {"active": [{"symbol": source["symbol"], "scanner": source, "radar": {"fixture": True}}]}}}
            return None

        def reconcile_review_job(self, scan_id, *, proposal_store):
            return {"scan_id": scan_id, "status": "REVIEW_PARTIAL"}

    old_store, old_jobs = server._AI_TRADE_DESK_STORE, server._AI_TRADE_DESK_SCAN_JOBS
    try:
        server._AI_TRADE_DESK_STORE, server._AI_TRADE_DESK_SCAN_JOBS = store, Jobs()
        client = TestClient(server.app)

        def requester(path, *, method, payload, role, actor, ai_token):
            headers = {}
            if role:
                headers.update({"X-AI-Trade-Desk-Role": role, "X-AI-Trade-Desk-Actor": actor})
            if ai_token:
                headers["X-AI-Trade-Desk-Token"] = ai_token
            response = client.request(method, path, headers=headers, json=payload)
            assert response.status_code == 200, response.text
            return response.json()

        operator = TradingWorkOperator(requester=requester)
        with patch("tools.trading_work_operator._configured_ai_token", return_value="fixture-token"), patch.dict("os.environ", {"AI_TRADE_DESK_AI_TOKEN": "fixture-token"}, clear=False):
            payload = operator.review_payload("fixture-review")
            review = {
                **_review(proposal["proposal_id"]),
                **{key: payload["candidates"][0][key] for key in (
                    "source_scan_id", "gpt_review_job_id", "parent_market_scan_id", "opportunity_id",
                    "proposal_origin_source_scan_id", "reviewed_observation_id", "reviewed_source_candidate_id",
                    "symbol", "strategy", "direction", "lifecycle_status",
                )},
            }
            result = operator.publish_reviews("fixture-review", payload, [review])
    finally:
        server._AI_TRADE_DESK_STORE, server._AI_TRADE_DESK_SCAN_JOBS = old_store, old_jobs

    assert payload["candidates"][0]["proposal_id"] == proposal["proposal_id"]
    assert result["count"] == 1
    assert store.get(proposal["proposal_id"])["ai_review_status"] == "COMPLETED"
    assert [event["event_type"] for event in store.events(proposal["proposal_id"])] == ["PROPOSAL_CREATED", "AI_REVIEW_PUBLISHED"]
