from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from src.decision.models import DecisionAction, DecisionResponse
from src.decision.provider import DecisionProviderError
from src.decision.provider_health import provider_health_request, run_provider_health_check


def _config(**overrides):
    values = {
        "provider": "deepseek",
        "model": "deepseek-flash",
        "local_model": "",
        "timeout_seconds": 1.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_provider_health_request_is_strict_non_trading_wait_contract():
    request = provider_health_request(provider_name="deepseek", model="deepseek-flash")
    candidate = request.snapshot.candidate

    assert request.allowed_actions == ("WAIT",)
    assert request.snapshot.allowed_actions == ("WAIT",)
    assert candidate.asset_class == "diagnostic"
    assert candidate.symbol == "TEST"
    assert candidate.strategy == "connectivity_check"
    assert candidate.metadata["non_trading"] is True
    assert candidate.metadata["diagnostic"] is True
    assert candidate.metadata["health_probe"] is True
    assert request.to_prompt_payload()["allowed_actions"] == ["WAIT"]


def test_provider_health_accepts_only_wait_and_does_not_persist_trading_records():
    provider = Mock(provider_name="deepseek", model="deepseek-flash")
    provider.decide.return_value = DecisionResponse(
        action=DecisionAction.WAIT,
        confidence=1.0,
        ranked_actions=(("WAIT", 1.0),),
        reason_codes=("CONNECTIVITY_OK",),
    )
    with (
        patch("src.decision.provider_health.build_provider", return_value=provider),
        patch("src.execution.order_manager.OrderManager.execute_trade") as execute_trade,
        patch("src.brokers.ibkr.IBKRClient.place_market_order") as broker_order,
    ):
        result = run_provider_health_check(_config())

    assert result.connected is True
    request = provider.decide.call_args.args[0]
    assert request.allowed_actions == ("WAIT",)
    assert request.snapshot.candidate.asset_class == "diagnostic"
    assert request.snapshot.candidate.symbol == "TEST"
    assert request.snapshot.candidate.metadata["non_trading"] is True
    assert "candidate_id" not in json.dumps(result.__dict__).lower()
    execute_trade.assert_not_called()
    broker_order.assert_not_called()


def test_provider_health_sanitizes_timeout_auth_and_schema_failures():
    failures = [
        (requests.Timeout("timed out"), "TIMEOUT"),
        (requests.HTTPError("unauthorized", response=SimpleNamespace(status_code=401)), "AUTH_ERROR"),
        (DecisionProviderError("provider returned invalid decision JSON"), "INVALID_RESPONSE"),
    ]
    for failure, expected in failures:
        provider = Mock(provider_name="deepseek", model="deepseek-flash")
        provider.decide.side_effect = failure
        with patch("src.decision.provider_health.build_provider", return_value=provider):
            result = run_provider_health_check(_config())
        assert result.status == "UNAVAILABLE"
        assert result.error == expected


def test_provider_health_timeout_is_bounded():
    provider = Mock(provider_name="deepseek", model="deepseek-flash")

    def slow_decide(_request):
        import time

        time.sleep(0.2)
        return DecisionResponse(action=DecisionAction.WAIT, confidence=1.0)

    provider.decide.side_effect = slow_decide
    with patch("src.decision.provider_health.build_provider", return_value=provider):
        result = run_provider_health_check(_config(timeout_seconds=0.02))

    assert result.status == "UNAVAILABLE"
    assert result.error == "TIMEOUT"
