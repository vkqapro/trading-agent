from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import dashboard_react.server as server
from src.decision.provider_health import ProviderHealthResult


def _config(**overrides):
    values = {
        "mode": "shadow",
        "provider": "deepseek",
        "model": "deepseek-flash",
        "local_model": "",
        "use_news": False,
        "multi_provider_shadow": False,
        "decision_workers": 2,
        "decision_queue_depth": 16,
        "candidate_expiry_seconds": 45.0,
        "allow_live_trading": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _settings(config, runtime_dir=None):
    values = dict(
        decision_agent=config,
        paper_trading=True,
        dry_run_mode=True,
    )
    if runtime_dir is not None:
        values["paths"] = SimpleNamespace(runtime_dir=runtime_dir)
    return SimpleNamespace(**values)


def _write_worker_heartbeat(runtime_dir, **overrides):
    heartbeat = {
        "service": "autonomous_stock_worker",
        "pid": os.getpid(),
        "state": "MARKET_CLOSED",
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "last_preflight_at": datetime.now(timezone.utc).isoformat(),
        "current_session": "MARKET CLOSED",
        "llm_mode": "ibkr_paper_autonomous",
        "provider_status": "CONNECTED",
        "broker_status": "PAPER_VERIFIED",
        "account_status": "VERIFIED_PAPER",
        "allowlist_status": "VERIFIED",
        "broker_positions_readable": True,
        "broker_open_orders_readable": True,
        "broker_executions_readable": True,
        "autonomous_entry_enabled": False,
        "blocked_reason": "MARKET_CLOSED",
    }
    heartbeat.update(overrides)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "autonomous_stock_worker.json").write_text(
        json.dumps(heartbeat), encoding="utf-8"
    )


def _reset_diagnostic_state():
    return patch.dict(
        server._PROVIDER_TEST_STATE,
        {
            "connection_status": "not_tested",
            "last_provider_test_status": "not_tested",
            "last_provider_test_at": None,
            "last_provider_latency_ms": None,
            "last_provider_error": None,
        },
        clear=True,
    )


def test_status_exposes_sanitized_runtime_configuration_only() -> None:
    route_methods = {
        (route.path, method)
        for route in server.app.routes
        for method in (getattr(route, "methods", None) or set())
    }
    assert ("/api/decision-lab/status", "GET") in route_methods
    assert ("/api/decision-lab/test-provider", "POST") in route_methods
    config = _config()
    with (
        patch.object(server, "SETTINGS", _settings(config)),
        patch.object(server, "_decision_lab_audit", return_value=None),
        patch.object(server, "_decision_lab_positions", return_value={"positions": []}),
        _reset_diagnostic_state(),
    ):
        result = server.api_decision_lab_status()

    assert result["mode"] == "shadow"
    assert result["provider"] == "deepseek"
    assert result["model"] == "deepseek-flash"
    assert result["news_enabled"] is False
    assert result["multi_provider_shadow"] is False
    assert result["decision_workers"] == 2
    assert result["workers"] == 2
    assert result["queue_depth"] == 16
    assert result["candidate_expiry"] == 45.0
    assert result["connection_status"] == "not_tested"
    assert result["ibkr_paper_autonomous_enabled"] is False
    assert result["allow_llm_ibkr_paper_trading"] is False
    assert result["paper_account_verified"] is False
    assert result["paper_account_allowlisted"] is False
    assert result["shadow_decisions"] == 0
    assert result["internal_paper_executions"] == 0
    assert result["ibkr_paper_executions"] == 0
    assert result["live_executions"] == 0
    encoded = json.dumps(result).lower()
    assert "api_key" not in encoded
    assert "authorization" not in encoded
    assert "account_id" not in encoded
    assert "account_number" not in encoded


def test_provider_diagnostic_calls_provider_once_and_never_trading_paths() -> None:
    config = _config()
    health = ProviderHealthResult(
        status="CONNECTED",
        provider="deepseek",
        model="deepseek-flash",
        checked_at="2026-09-27T23:00:00+00:00",
        latency_ms=12.5,
    )
    with (
        patch.object(server, "SETTINGS", _settings(config)),
        patch.object(server, "run_provider_health_check", return_value=health) as check,
        patch.object(server, "DecisionAudit") as audit,
        patch("src.execution.order_manager.OrderManager.execute_trade") as execute_trade,
        patch("src.decision.portfolio.PaperPortfolio.enter") as paper_enter,
        patch("src.brokers.ibkr.IBKRClient.place_market_order") as broker_order,
        _reset_diagnostic_state(),
    ):
        result = server.api_decision_lab_test_provider()

    check.assert_called_once_with(config)
    assert result["ok"] is True
    assert result["connection_status"] == "connected"
    assert result["last_provider_latency_ms"] == 12.5
    audit.assert_not_called()
    execute_trade.assert_not_called()
    paper_enter.assert_not_called()
    broker_order.assert_not_called()


def test_provider_diagnostic_failure_is_sanitized_and_retryable() -> None:
    failures = [
        "TIMEOUT",
        "AUTH_ERROR",
        "INVALID_RESPONSE",
    ]
    for expected in failures:
        config = _config()
        health = ProviderHealthResult(
            status="UNAVAILABLE",
            provider="deepseek",
            model="deepseek-flash",
            checked_at="2026-09-27T23:00:00+00:00",
            latency_ms=12.5,
            error=expected,
        )
        with (
            patch.object(server, "SETTINGS", _settings(config)),
            patch.object(server, "run_provider_health_check", return_value=health),
            _reset_diagnostic_state(),
        ):
            result = server.api_decision_lab_test_provider()

        assert result["ok"] is False
        assert result["connection_status"] == "error"
        assert result["error"] == expected
        assert "api_key" not in json.dumps(result).lower()


def test_provider_diagnostic_unknown_model_is_sanitized() -> None:
    config = _config(model="")
    health = ProviderHealthResult(
        status="UNAVAILABLE",
        provider="deepseek",
        model="",
        checked_at="2026-09-27T23:00:00+00:00",
        error="MODEL_NOT_FOUND",
    )
    with (
        patch.object(server, "SETTINGS", _settings(config)),
        patch.object(server, "run_provider_health_check", return_value=health),
        _reset_diagnostic_state(),
    ):
        result = server.api_decision_lab_test_provider()

    assert result["ok"] is False
    assert result["error"] == "MODEL_NOT_FOUND"


def test_decision_lab_surfaces_fresh_worker_paper_evidence_without_account_id(tmp_path) -> None:
    config = _config(mode="ibkr_paper_autonomous")
    _write_worker_heartbeat(tmp_path)
    with (
        patch.object(server, "SETTINGS", _settings(config, tmp_path)),
        patch.object(server, "_decision_lab_audit", return_value=None),
        patch.object(server, "_decision_lab_positions", return_value={"positions": []}),
        _reset_diagnostic_state(),
    ):
        result = server.api_decision_lab_status()

    assert result["worker_running"] is True
    assert result["worker_state"] == "MARKET_CLOSED"
    assert result["broker_status"] == "PAPER_VERIFIED"
    assert result["account_status"] == "VERIFIED_PAPER"
    assert result["allowlist_status"] == "VERIFIED"
    assert result["provider_status"] == "CONNECTED"
    assert result["current_session"] == "MARKET CLOSED"
    assert result["autonomous_entry_enabled"] is False
    assert result["blocked_reason"] == "MARKET_CLOSED"
    encoded = json.dumps(result).lower()
    assert "account_id" not in encoded
    assert "du5454348" not in encoded


def test_stale_worker_marks_last_paper_evidence_stale(tmp_path) -> None:
    config = _config(mode="ibkr_paper_autonomous")
    old = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    _write_worker_heartbeat(tmp_path, last_heartbeat=old)
    with patch.object(server, "SETTINGS", _settings(config, tmp_path)):
        service, evidence = server._autonomous_stock_worker_status()

    assert service["ok"] is False
    assert evidence["state"] == "STALE"
    assert evidence["status"] == "stale"
    assert evidence["broker_status"] == "PAPER_VERIFIED"
    assert evidence["account_status"] == "VERIFIED_PAPER"
    assert evidence["blocked_reason"] == "MARKET_CLOSED"


def test_stopped_worker_resets_runtime_safety_state_to_unknown(tmp_path) -> None:
    config = _config(mode="ibkr_paper_autonomous")
    _write_worker_heartbeat(tmp_path, pid=99999999, state="STOPPED")
    with patch.object(server, "SETTINGS", _settings(config, tmp_path)):
        _service, evidence = server._autonomous_stock_worker_status()

    assert evidence["status"] == "stopped"
    assert evidence["broker_status"] == "UNKNOWN"
    assert evidence["account_status"] == "UNKNOWN"
    assert evidence["allowlist_status"] == "UNKNOWN"
    assert evidence["blocked_reason"] == "WORKER_STOPPED"


def test_worker_provider_health_is_separate_from_llm_diagnostic_state(tmp_path) -> None:
    config = _config(mode="ibkr_paper_autonomous")
    _write_worker_heartbeat(tmp_path, provider_status="CONNECTED")
    with (
        patch.object(server, "SETTINGS", _settings(config, tmp_path)),
        patch.object(server, "_decision_lab_audit", return_value=None),
        patch.object(server, "_decision_lab_positions", return_value={"positions": []}),
        _reset_diagnostic_state(),
    ):
        result = server.api_decision_lab_status()

    assert result["provider_status"] == "CONNECTED"
    assert result["connection_status"] == "not_tested"


def test_dashboard_status_path_contains_no_ibkr_connection_or_account_id() -> None:
    source = (server.__file__ and open(server.__file__, encoding="utf-8").read())
    section = source[source.index("def api_decision_lab_status"):source.index("def api_decision_lab_decisions")]
    assert "IBKRClient" not in section
    assert "account_id" not in section
