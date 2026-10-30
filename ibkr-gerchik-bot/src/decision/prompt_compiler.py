"""The single authoritative Decision Lab prompt compiler."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Sequence

from .applicability import ApplicabilityResult
from .models import DecisionCandidate
from .prompt_presets import default_preset
from .strategy_definitions import current_strategy_parameters, get_strategy_definition


LOCKED_SYSTEM_POLICY = (
    "You are a bounded decision component inside a safety-critical trading system.\n"
    "The Strategy Definition is authoritative and describes the real source calculator.\n"
    "The Strategy Parameter Snapshot is the current effective configuration.\n"
    "The Prompt Preset contains analytical preferences only; it cannot override the definition, parameters, candidate class, allowed actions, execution capability, risk rules, or response schema.\n"
    "Do not invent prices, levels, indicators, quantity, positions, or strategy semantics.\n"
    "Choose only from the provided action menu.\n"
    "Do not call a broker, exchange, account, news, or filesystem API.\n"
    "Perform internal reasoning as needed, then return one concise JSON object and no prose outside JSON."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["action", "confidence", "ranked_actions", "reason_codes", "summary"],
    "properties": {
        "action": "one value from allowed_actions",
        "confidence": "number from 0 to 1",
        "ranked_actions": "array of [action, score] pairs",
        "reason_codes": "array of concise strings",
        "summary": "concise string, maximum 500 characters",
    },
}


@dataclass(frozen=True)
class CompiledDecisionPrompt:
    system_prompt: str
    user_payload: Mapping[str, object]
    strategy_source: str
    definition_version: str
    definition_hash: str
    parameter_snapshot: Mapping[str, object]
    prompt_id: str
    prompt_version: str
    prompt_hash: str
    compiled_prompt_hash: str

    @property
    def text(self) -> str:
        return json.dumps(self.user_payload, indent=2, sort_keys=True, ensure_ascii=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "system_prompt": self.system_prompt,
            "user_payload": dict(self.user_payload),
            "strategy_source": self.strategy_source,
            "definition_version": self.definition_version,
            "definition_hash": self.definition_hash,
            "parameter_snapshot": dict(self.parameter_snapshot),
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "prompt_hash": self.prompt_hash,
            "compiled_prompt_hash": self.compiled_prompt_hash,
            "text": self.text,
        }


def _safe_position(position: Mapping[str, object]) -> dict[str, object]:
    allowed = (
        "symbol", "direction", "quantity", "position", "entry", "entry_price", "current_price",
        "unrealized_pnl", "pnl", "stop_loss", "target", "owner", "strategy",
    )
    return {key: position.get(key) for key in allowed if key in position}


def _candidate_payload(candidate: DecisionCandidate) -> dict[str, object]:
    payload = candidate.to_dict()
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    candidate_class = str(metadata.get("candidate_class") or "").upper()
    analysis_only = metadata.get("execution_eligible") is False
    if candidate_class in {"WATCH_CANDIDATE", "ENTRY_SIGNAL", "POSITION_MANAGEMENT_SIGNAL"} or analysis_only:
        for key in ("entry", "stop", "target", "level_price", "level_strength", "atr", "reward_risk", "risk_per_share"):
            if payload.get(key) in (0, 0.0):
                payload[key] = None
    return payload


def relevant_position_context(
    candidate: DecisionCandidate,
    position_context: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    """Return only position evidence that is relevant to this candidate.

    Exit/position-management signals require same-symbol ownership evidence.
    Entry and watch candidates omit position context unless a source explicitly
    enables a duplicate-position restriction.  A missing position is normal
    entry/watch state and must not become model-facing negative evidence.
    """
    metadata = candidate.metadata if isinstance(candidate.metadata, Mapping) else {}
    candidate_class = str(metadata.get("candidate_class") or "").strip().upper()
    if candidate_class == "POSITION_MANAGEMENT_SIGNAL":
        symbol = str(candidate.symbol or "").strip().upper()
        return tuple(
            item for item in position_context
            if isinstance(item, Mapping)
            and str(item.get("symbol") or item.get("ticker") or item.get("localSymbol") or "").strip().upper() == symbol
        )
    if bool(metadata.get("duplicate_position_restriction")):
        symbol = str(candidate.symbol or "").strip().upper()
        return tuple(
            item for item in position_context
            if isinstance(item, Mapping)
            and str(item.get("symbol") or item.get("ticker") or item.get("localSymbol") or "").strip().upper() == symbol
        )
    return tuple()


def compile_decision_prompt(
    *,
    strategy_source: str,
    candidate: DecisionCandidate,
    position_context: Sequence[Mapping[str, object]] = (),
    allowed_actions: Sequence[str] = ("WAIT", "REJECT"),
    prompt_preset: Mapping[str, object] | None = None,
    applicability: ApplicabilityResult | Mapping[str, object] | None = None,
    parameter_snapshot: Mapping[str, object] | None = None,
) -> CompiledDecisionPrompt:
    definition = get_strategy_definition(strategy_source)
    parameters = dict(parameter_snapshot or current_strategy_parameters(strategy_source))
    preset = dict(prompt_preset or default_preset(strategy_source))
    actions = [str(item).upper() for item in allowed_actions]
    app = applicability.to_dict() if isinstance(applicability, ApplicabilityResult) else dict(applicability or {"applicable": True, "outcome": "APPLICABLE"})
    relevant_positions = relevant_position_context(candidate, position_context)
    candidate_class = str(candidate.metadata.get("candidate_class") or "").strip().upper() if isinstance(candidate.metadata, Mapping) else ""
    payload: dict[str, object] = {
        "strategy_definition": definition.to_dict(),
        "strategy_parameters": parameters,
        "analysis_instructions": {
            "prompt_id": preset.get("prompt_id"),
            "prompt_name": preset.get("name"),
            "prompt_version": preset.get("version"),
            "prompt_hash": preset.get("content_hash") or hashlib.sha256(str(preset.get("prompt_text") or "").encode()).hexdigest(),
            "prompt_text": str(preset.get("prompt_text") or ""),
        },
        "candidate": _candidate_payload(candidate),
        "position_context": [_safe_position(item) for item in relevant_positions],
        "position_context_scope": (
            "same_symbol_position_management"
            if candidate_class == "POSITION_MANAGEMENT_SIGNAL"
            else "same_symbol_duplicate_restriction"
            if relevant_positions
            else "omitted_for_entry_or_watch"
        ),
        "candidate_applicability": app,
        "allowed_actions": actions,
        "response_schema": RESPONSE_SCHEMA,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    prompt_text = str(preset.get("prompt_text") or "")
    prompt_hash = str(preset.get("content_hash") or hashlib.sha256(prompt_text.encode("utf-8")).hexdigest())
    return CompiledDecisionPrompt(
        system_prompt=LOCKED_SYSTEM_POLICY,
        user_payload=payload,
        strategy_source=definition.strategy_source,
        definition_version=definition.definition_version,
        definition_hash=definition.definition_hash,
        parameter_snapshot=parameters,
        prompt_id=str(preset.get("prompt_id") or ""),
        prompt_version=str(preset.get("version") or "v1"),
        prompt_hash=prompt_hash,
        compiled_prompt_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    )
