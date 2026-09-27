from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.decision.models import (
    AgentMode,
    DecisionAction,
    DecisionCandidate,
    DecisionRequest,
    DecisionSnapshot,
)
from src.decision.provider import (
    DecisionProviderError,
    HttpDecisionProvider,
    build_system_prompt,
)


def _request() -> DecisionRequest:
    candidate = DecisionCandidate(
        candidate_id="candidate-provider-1",
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
    snapshot = DecisionSnapshot.from_candidate(
        candidate,
        session="intraday",
        current_price=100.0,
        data_age_seconds=1.0,
    )
    return DecisionRequest(
        decision_id="decision-provider-1",
        agent_id="GERCHIK_LLM_01",
        mode=AgentMode.SHADOW,
        provider="local_openai",
        model="test-model",
        snapshot=snapshot,
        allowed_actions=tuple(action.value for action in DecisionAction),
    )


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_openai_compatible_provider_parses_strict_json_without_exposing_secrets() -> None:
    session = FakeSession(
        FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"action":"WAIT","confidence":0.61,"reason_codes":["WAIT_FOR_CONFIRMATION"],"summary":"Wait."}'
                        }
                    }
                ]
            }
        )
    )
    provider = HttpDecisionProvider(
        provider_name="local_openai",
        model="test-model",
        base_url="http://model.test/v1",
        api_key="secret-do-not-log",
        session=session,
        timeout_seconds=3,
    )

    result = provider.decide(_request())

    assert result.action is DecisionAction.WAIT
    assert session.calls[0]["url"] == "http://model.test/v1/chat/completions"
    assert "secret-do-not-log" not in str(session.calls[0].get("json"))
    assert session.calls[0]["headers"]["Authorization"] == "Bearer secret-do-not-log"


def test_provider_rejects_invalid_json_and_does_not_fail_open() -> None:
    session = FakeSession(
        FakeResponse(
            {"choices": [{"message": {"content": '{"action":"WIDEN_STOP"}'}}]}
        )
    )
    provider = HttpDecisionProvider(
        provider_name="local_openai",
        model="test-model",
        base_url="http://model.test/v1",
        session=session,
    )

    with pytest.raises(DecisionProviderError, match="invalid decision"):
        provider.decide(_request())


def test_provider_http_failure_is_a_provider_error() -> None:
    session = FakeSession(RuntimeError("connection refused"))
    provider = HttpDecisionProvider(
        provider_name="local_openai",
        model="test-model",
        base_url="http://model.test/v1",
        session=session,
    )

    with pytest.raises(DecisionProviderError, match="request failed"):
        provider.decide(_request())


def test_prompt_forbids_arbitrary_prices_and_broker_access() -> None:
    prompt = build_system_prompt()
    assert "Do not invent prices" in prompt
    assert "provided action menu" in prompt
    assert "broker" in prompt.lower()
