from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

import dashboard_react.server as server
from src.decision.models import DecisionAction, DecisionResponse
from src.decision.provider import DecisionProviderError


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


def _settings(config):
    return SimpleNamespace(
        decision_agent=config,
        paper_trading=True,
        dry_run_mode=True,
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
    provider = Mock(provider_name="deepseek", model="deepseek-flash")
    provider.decide.return_value = DecisionResponse(
        action=DecisionAction.WAIT,
        confidence=1.0,
        ranked_actions=(("WAIT", 1.0),),
        reason_codes=("CONNECTIVITY_OK",),
        summary="Provider connectivity test successful.",
    )
    with (
        patch.object(server, "SETTINGS", _settings(config)),
        patch.object(server, "build_provider", return_value=provider) as build,
        patch.object(server, "DecisionAudit") as audit,
        patch("src.execution.order_manager.OrderManager.execute_trade") as execute_trade,
        patch("src.decision.portfolio.PaperPortfolio.enter") as paper_enter,
        patch("src.brokers.ibkr.IBKRClient.place_market_order") as broker_order,
        _reset_diagnostic_state(),
    ):
        result = server.api_decision_lab_test_provider()

    build.assert_called_once_with(config)
    provider.decide.assert_called_once()
    request = provider.decide.call_args.args[0]
    assert request.allowed_actions == ("WAIT",)
    assert request.snapshot.candidate.asset_class == "diagnostic"
    assert request.snapshot.candidate.symbol == "TEST"
    assert request.snapshot.candidate.strategy == "connectivity_check"
    assert request.snapshot.candidate.direction == "none"
    assert request.snapshot.candidate.metadata["non_trading"] is True
    assert result["ok"] is True
    assert result["connection_status"] == "connected"
    assert result["last_provider_latency_ms"] is not None
    audit.assert_not_called()
    execute_trade.assert_not_called()
    paper_enter.assert_not_called()
    broker_order.assert_not_called()


def test_provider_diagnostic_failure_is_sanitized_and_retryable() -> None:
    failures = [
        (requests.Timeout("provider timed out"), "timeout"),
        (requests.HTTPError("unauthorized", response=SimpleNamespace(status_code=401)), "HTTP 401"),
        (DecisionProviderError("provider returned invalid decision JSON"), "invalid JSON response"),
    ]
    for error, expected in failures:
        config = _config()
        provider = Mock(provider_name="deepseek", model="deepseek-flash")
        provider.decide.side_effect = error
        with (
            patch.object(server, "SETTINGS", _settings(config)),
            patch.object(server, "build_provider", return_value=provider),
            _reset_diagnostic_state(),
        ):
            result = server.api_decision_lab_test_provider()

        assert result["ok"] is False
        assert result["connection_status"] == "error"
        assert result["error"] == expected
        assert "api_key" not in json.dumps(result).lower()


def test_provider_diagnostic_unknown_model_is_sanitized() -> None:
    config = _config(model="")
    with (
        patch.object(server, "SETTINGS", _settings(config)),
        patch.object(server, "build_provider", side_effect=ValueError("decision provider model is required")),
        _reset_diagnostic_state(),
    ):
        result = server.api_decision_lab_test_provider()

    assert result["ok"] is False
    assert result["error"] == "model unavailable or not configured"
    assert "required" not in result["error"]
