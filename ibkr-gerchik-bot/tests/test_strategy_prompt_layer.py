from __future__ import annotations

from datetime import datetime, timezone

from src.decision.applicability import evaluate_candidate_applicability
from src.decision.models import AgentMode, DecisionCandidate, DecisionRequest, DecisionSnapshot
from src.decision.prompt_compiler import compile_decision_prompt
from src.decision.prompt_presets import (
    default_preset,
    disable_preset,
    duplicate_preset,
    list_presets,
    save_preset,
)
from src.decision.strategy_definitions import current_strategy_parameters, get_strategy_definition


def _candidate(*, candidate_class: str = "WATCH_CANDIDATE", signal: str = "GAUSSIAN_NEAR_LONG") -> DecisionCandidate:
    return DecisionCandidate(
        candidate_id="candidate-1",
        created_at=datetime.now(timezone.utc),
        asset_class="stock",
        symbol="AAPL",
        strategy="Gaussian Channel",
        direction="long",
        entry=0.0,
        stop=0.0,
        target=0.0,
        metadata={
            "strategy_source": "gaussian",
            "candidate_class": candidate_class,
            "source_signal": signal,
        },
    )


def test_strategy_definitions_are_code_owned_and_expose_current_parameters():
    definition = get_strategy_definition("gaussian")
    assert definition.definition_version == "v1"
    assert definition.definition_hash
    assert "_gaussian_scan_symbol" in " ".join(definition.implementation_provenance)
    assert current_strategy_parameters("gaussian")["period"] == 144
    assert current_strategy_parameters("bmsb")["ema_period"] == 21


def test_monitor_entry_semantics_are_separate_from_analysis_only_capability():
    gaussian = get_strategy_definition("gaussian")
    bmsb = get_strategy_definition("bmsb")
    assert gaussian.candidate_class_mapping["GAUSSIAN_LONG_ENTRY"] == "ENTRY_SIGNAL"
    assert bmsb.candidate_class_mapping["CROSSED_LONG"] == "ENTRY_SIGNAL"
    assert gaussian.source_execution_capability == "ANALYSIS_ONLY"
    assert bmsb.source_execution_capability == "ANALYSIS_ONLY"


def test_position_management_without_same_symbol_position_is_not_applicable():
    result = evaluate_candidate_applicability(
        _candidate(candidate_class="POSITION_MANAGEMENT_SIGNAL", signal="GAUSSIAN_LONG_EXIT"),
        current_positions=[],
        broker_positions=[],
    )
    assert result.applicable is False
    assert result.outcome == "NOT_APPLICABLE"
    assert "AAPL" in result.reason


def test_position_management_with_same_symbol_position_is_applicable():
    result = evaluate_candidate_applicability(
        _candidate(candidate_class="POSITION_MANAGEMENT_SIGNAL", signal="GAUSSIAN_LONG_EXIT"),
        broker_positions=[{"symbol": "AAPL", "position": 5}],
    )
    assert result.applicable is True
    assert result.matching_position_count == 1


def test_compiler_is_source_aware_and_normalizes_analysis_only_zero_fields():
    compiled = compile_decision_prompt(
        strategy_source="gaussian",
        candidate=_candidate(),
        allowed_actions=("WAIT", "REJECT"),
        prompt_preset=default_preset("gaussian"),
    )
    assert compiled.user_payload["strategy_definition"]["strategy_source"] == "gaussian"
    assert compiled.user_payload["candidate"]["entry"] is None
    assert "account_id" not in compiled.text.lower()
    assert "authorization" not in compiled.text.lower()
    assert "stock screener" not in compiled.text.lower()


def test_compiler_omits_irrelevant_positions_for_entry_watch_candidates():
    compiled = compile_decision_prompt(
        strategy_source="gaussian",
        candidate=_candidate(candidate_class="WATCH_CANDIDATE", signal="GAUSSIAN_NEAR_LONG"),
        position_context=[{"symbol": "AAPL", "position": 5}, {"symbol": "MSFT", "position": 2}],
        allowed_actions=("WAIT", "REJECT"),
    )
    assert compiled.user_payload["position_context"] == []
    assert compiled.user_payload["position_context_scope"] == "omitted_for_entry_or_watch"
    assert "no_matching_position" not in compiled.text.lower()


def test_analysis_only_entry_semantics_keep_price_fields_null():
    candidate = _candidate(candidate_class="ENTRY_SIGNAL", signal="GAUSSIAN_LONG_ENTRY")
    candidate = candidate.__class__(
        **{**candidate.__dict__, "metadata": {**candidate.metadata, "execution_eligible": False}}
    )
    compiled = compile_decision_prompt(
        strategy_source="gaussian",
        candidate=candidate,
        allowed_actions=("WAIT", "REJECT"),
    )
    assert compiled.user_payload["candidate"]["entry"] is None
    assert compiled.user_payload["candidate"]["stop"] is None
    assert compiled.user_payload["candidate"]["target"] is None


def test_compiler_limits_position_context_to_same_symbol_for_exit_candidates():
    compiled = compile_decision_prompt(
        strategy_source="gaussian",
        candidate=_candidate(candidate_class="POSITION_MANAGEMENT_SIGNAL", signal="GAUSSIAN_LONG_EXIT"),
        position_context=[{"symbol": "AAPL", "position": 5}, {"symbol": "MSFT", "position": 2}],
        allowed_actions=("WAIT", "REJECT"),
    )
    assert compiled.user_payload["position_context"] == [{"symbol": "AAPL", "position": 5}]
    assert compiled.user_payload["position_context_scope"] == "same_symbol_position_management"


def test_decision_request_uses_compiled_prompt_without_duplicate_candidate_payload():
    compiled = compile_decision_prompt(
        strategy_source="gaussian",
        candidate=_candidate(),
        prompt_preset=default_preset("gaussian"),
    )
    request = DecisionRequest(
        decision_id="decision-1",
        agent_id="agent-1",
        mode=AgentMode.SHADOW,
        provider="deepseek",
        model="deepseek-flash",
        snapshot=DecisionSnapshot.from_candidate(_candidate(), session="test"),
        allowed_actions=("WAIT", "REJECT"),
        compiled_prompt=compiled.user_payload,
    )
    payload = request.to_prompt_payload()
    assert "compiled_prompt" in payload
    assert "candidate" not in payload
    assert "snapshot" not in payload


def test_custom_prompt_presets_are_versioned_and_built_ins_are_immutable(tmp_path):
    built_in = default_preset("gaussian", runtime_dir=tmp_path)
    custom = duplicate_preset(built_in["prompt_id"], name="Editable Gaussian", runtime_dir=tmp_path)
    version = save_preset(
        strategy_source="gaussian",
        name=custom["name"],
        prompt_text="Prefer WAIT when the Gaussian evidence is incomplete.",
        parent_prompt_id=custom["prompt_id"],
        as_new_version=True,
        runtime_dir=tmp_path,
    )
    assert custom["editable"] is True
    assert version["version"] == "v2"
    assert built_in["prompt_text"] == default_preset("gaussian", runtime_dir=tmp_path)["prompt_text"]
    disabled = disable_preset(custom["prompt_id"], runtime_dir=tmp_path)
    assert disabled["enabled"] is False
    assert any(item["prompt_id"] == version["prompt_id"] for item in list_presets("gaussian", runtime_dir=tmp_path))
