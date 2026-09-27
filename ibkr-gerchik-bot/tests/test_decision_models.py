from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.decision.models import (
    AgentMode,
    DecisionAction,
    DecisionCandidate,
    DecisionResponse,
    DecisionSnapshot,
)
from src.decision.candidate_adapter import trade_signal_to_candidate
from src.strategy.signal_models import TradeSignal


def _signal(**overrides: object) -> TradeSignal:
    payload = {
        "symbol": "AAPL",
        "strategy": "rebound",
        "signal": "BUY",
        "direction": "long",
        "entry": 100.0,
        "stop": 98.0,
        "target": 106.0,
        "level_price": 99.5,
        "level_type": "support",
        "reward_risk": 3.0,
        "risk_per_share": 2.0,
        "atr": 4.0,
        "atr_used": 0.25,
        "confidence": 0.7,
        "level_strength": 8.0,
        "partial_targets": [{"qty_pct": 0.5, "price": 106.0}],
        "metadata": {"signal_timestamp": "2026-09-26T10:00:00+00:00"},
    }
    payload.update(overrides)
    return TradeSignal(**payload)


def test_trade_signal_adapter_is_stable_for_the_same_setup() -> None:
    first = trade_signal_to_candidate(_signal())
    second = trade_signal_to_candidate(_signal())

    assert first.candidate_id == second.candidate_id
    assert first.entry == 100.0
    assert first.stop == 98.0
    assert first.target == 106.0
    assert first.asset_class == "stock"


def test_new_signal_timestamp_creates_a_new_candidate() -> None:
    first = trade_signal_to_candidate(_signal())
    second = trade_signal_to_candidate(
        _signal(metadata={"signal_timestamp": "2026-09-26T10:05:00+00:00"})
    )

    assert first.candidate_id != second.candidate_id


def test_candidate_rejects_non_finite_numeric_values() -> None:
    with pytest.raises(ValueError, match="finite"):
        DecisionCandidate(
            candidate_id="candidate-1",
            created_at=datetime.now(timezone.utc),
            asset_class="stock",
            symbol="AAPL",
            strategy="rebound",
            direction="long",
            entry=float("nan"),
            stop=98.0,
            target=106.0,
            level_price=99.5,
            level_type="support",
        )


def test_snapshot_is_compact_and_does_not_accept_secret_fields() -> None:
    candidate = trade_signal_to_candidate(_signal())
    snapshot = DecisionSnapshot.from_candidate(
        candidate,
        current_price=100.25,
        data_age_seconds=2.0,
        session="intraday",
        open_positions=1,
        open_risk_amount=200.0,
        daily_realized_pnl=-25.0,
        context={"news": "clear", "api_key": "must-not-be-kept"},
    )

    rendered = snapshot.to_dict()
    assert rendered["candidate"]["candidate_id"] == candidate.candidate_id
    assert rendered["current_price"] == 100.25
    assert "api_key" not in rendered["market_context"]
    assert len(str(rendered)) < 5000


def test_decision_response_accepts_only_allowed_actions_and_finite_confidence() -> None:
    response = DecisionResponse.from_mapping(
        {
            "action": "ENTER",
            "confidence": 0.84,
            "ranked_actions": [["ENTER", 0.84], ["WAIT", 0.12], ["REJECT", 0.04]],
            "reason_codes": ["CLEAN_SETUP"],
            "summary": "Clean confirmed setup.",
        },
        allowed_actions=(DecisionAction.ENTER, DecisionAction.WAIT, DecisionAction.REJECT),
    )

    assert response.action is DecisionAction.ENTER
    assert response.confidence == 0.84


def test_decision_response_accepts_bounded_ranked_action_objects() -> None:
    response = DecisionResponse.from_mapping(
        {
            "action": "WAIT",
            "confidence": 1.0,
            "ranked_actions": [{"action": "WAIT", "score": 1.0}],
        },
        allowed_actions=("WAIT",),
    )

    assert response.ranked_actions == (("WAIT", 1.0),)


def test_decision_response_rejects_incomplete_ranked_action_objects() -> None:
    with pytest.raises(ValueError, match="require action and score"):
        DecisionResponse.from_mapping(
            {
                "action": "WAIT",
                "confidence": 1.0,
                "ranked_actions": [{"action": "WAIT"}],
            },
            allowed_actions=("WAIT",),
        )


def test_decision_response_rejects_off_menu_or_invalid_json_shapes() -> None:
    with pytest.raises(ValueError, match="allowed action"):
        DecisionResponse.from_mapping(
            {"action": "WIDEN_STOP", "confidence": 0.5},
            allowed_actions=(DecisionAction.ENTER, DecisionAction.WAIT, DecisionAction.REJECT),
        )

    with pytest.raises(ValueError, match="confidence"):
        DecisionResponse.from_mapping(
            {"action": "WAIT", "confidence": "not-a-number"},
            allowed_actions=(DecisionAction.ENTER, DecisionAction.WAIT, DecisionAction.REJECT),
        )


def test_agent_mode_has_safe_default_and_explicit_values() -> None:
    assert AgentMode.OFF.value == "off"
    assert AgentMode.from_value("paper_autonomous") is AgentMode.PAPER_AUTONOMOUS
