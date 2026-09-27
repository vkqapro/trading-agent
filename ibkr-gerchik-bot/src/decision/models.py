"""Serializable contracts for autonomous decisions.

These models deliberately carry values calculated by the existing deterministic
engine.  They do not calculate indicators, prices, targets, stops or quantity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence


class AgentMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    PAPER_AUTONOMOUS = "paper_autonomous"
    IBKR_PAPER_AUTONOMOUS = "ibkr_paper_autonomous"
    LIVE_AUTONOMOUS = "live_autonomous"

    @classmethod
    def from_value(cls, value: object) -> "AgentMode":
        normalized = str(value or cls.OFF.value).strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(
                "LLM_AGENT_MODE must be one of: off, shadow, paper_autonomous, ibkr_paper_autonomous, live_autonomous"
            ) from exc


class DecisionAction(str, Enum):
    ENTER = "ENTER"
    WAIT = "WAIT"
    REJECT = "REJECT"


class PositionAction(str, Enum):
    HOLD = "HOLD"
    TRIM_50 = "TRIM_50"
    CLOSE = "CLOSE"
    MOVE_STOP_TO_BREAKEVEN = "MOVE_STOP_TO_BREAKEVEN"


_SENSITIVE_KEY_PARTS = (
    "key",
    "secret",
    "token",
    "password",
    "credential",
    "account_number",
    "account_id",
    "passphrase",
)


def _is_sensitive_key(key: object) -> bool:
    lowered = str(key).strip().lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def sanitize_mapping(value: object) -> object:
    """Return JSON-safe context with credentials/account identifiers removed."""
    if isinstance(value, Mapping):
        return {
            str(key): sanitize_mapping(item)
            for key, item in value.items()
            if not _is_sensitive_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_mapping(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)


def _finite(name: str, value: float | None, *, required: bool = False) -> float | None:
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    return numeric


def _timestamp(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class DecisionCandidate:
    candidate_id: str
    created_at: datetime
    asset_class: str
    symbol: str
    strategy: str
    direction: str
    entry: float
    stop: float
    target: float
    level_price: float | None = None
    level_type: str = ""
    level_strength: float | None = None
    atr: float | None = None
    reward_risk: float | None = None
    risk_per_share: float | None = None
    confidence: float | None = None
    partial_targets: tuple[dict[str, float], ...] = field(default_factory=tuple)
    source_signal_id: str | None = None
    market_context: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.candidate_id).strip():
            raise ValueError("candidate_id is required")
        if not str(self.symbol).strip():
            raise ValueError("symbol is required")
        if not str(self.strategy).strip():
            raise ValueError("strategy is required")
        for name in (
            "entry",
            "stop",
            "target",
            "level_price",
            "level_strength",
            "atr",
            "reward_risk",
            "risk_per_share",
            "confidence",
        ):
            _finite(name, getattr(self, name))
        object.__setattr__(self, "created_at", _timestamp(self.created_at))
        object.__setattr__(self, "market_context", sanitize_mapping(self.market_context))
        object.__setattr__(self, "metadata", sanitize_mapping(self.metadata))

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "created_at": self.created_at.isoformat(),
            "asset_class": self.asset_class,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "direction": self.direction,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "level_price": self.level_price,
            "level_type": self.level_type,
            "level_strength": self.level_strength,
            "atr": self.atr,
            "reward_risk": self.reward_risk,
            "risk_per_share": self.risk_per_share,
            "confidence": self.confidence,
            "partial_targets": [dict(item) for item in self.partial_targets],
            "source_signal_id": self.source_signal_id,
            "market_context": sanitize_mapping(self.market_context),
            "metadata": sanitize_mapping(self.metadata),
        }


@dataclass(frozen=True)
class DecisionSnapshot:
    candidate: DecisionCandidate
    session: str
    current_price: float | None = None
    data_age_seconds: float | None = None
    open_positions: int = 0
    open_risk_amount: float = 0.0
    daily_realized_pnl: float = 0.0
    market_context: dict[str, object] = field(default_factory=dict)
    allowed_actions: tuple[str, ...] = tuple(action.value for action in DecisionAction)

    def __post_init__(self) -> None:
        for name in (
            "current_price",
            "data_age_seconds",
            "open_risk_amount",
            "daily_realized_pnl",
        ):
            _finite(name, getattr(self, name))
        if self.open_positions < 0:
            raise ValueError("open_positions cannot be negative")
        object.__setattr__(self, "market_context", sanitize_mapping(self.market_context))

    @classmethod
    def from_candidate(cls, candidate: DecisionCandidate, **kwargs: object) -> "DecisionSnapshot":
        context = kwargs.pop("context", kwargs.pop("market_context", {}))
        return cls(candidate=candidate, market_context=dict(context or {}), **kwargs)

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate": self.candidate.to_dict(),
            "session": self.session,
            "current_price": self.current_price,
            "data_age_seconds": self.data_age_seconds,
            "open_positions": self.open_positions,
            "open_risk_amount": self.open_risk_amount,
            "daily_realized_pnl": self.daily_realized_pnl,
            "market_context": sanitize_mapping(self.market_context),
            "allowed_actions": list(self.allowed_actions),
        }


@dataclass(frozen=True)
class DecisionRequest:
    decision_id: str
    agent_id: str
    mode: AgentMode
    provider: str
    model: str
    snapshot: DecisionSnapshot
    allowed_actions: tuple[str, ...]
    requested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_prompt_payload(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "agent_id": self.agent_id,
            "mode": self.mode.value,
            "candidate": self.snapshot.candidate.to_dict(),
            "snapshot": self.snapshot.to_dict(),
            "allowed_actions": list(self.allowed_actions),
        }


@dataclass(frozen=True)
class DecisionResponse:
    action: DecisionAction | PositionAction
    confidence: float
    ranked_actions: tuple[tuple[str, float], ...] = tuple()
    reason_codes: tuple[str, ...] = tuple()
    summary: str = ""

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, object],
        *,
        allowed_actions: Sequence[DecisionAction | str],
    ) -> "DecisionResponse":
        if not isinstance(payload, Mapping):
            raise ValueError("decision response must be an object")
        allowed = {item.value if isinstance(item, DecisionAction) else str(item).upper() for item in allowed_actions}
        raw_action = str(payload.get("action", "")).strip().upper()
        if raw_action not in allowed:
            raise ValueError(f"action is not an allowed action: {raw_action or '<empty>'}")
        raw_confidence = payload.get("confidence")
        confidence = _finite("confidence", raw_confidence, required=True)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        ranked: list[tuple[str, float]] = []
        raw_ranked = payload.get("ranked_actions", [])
        if raw_ranked is not None:
            if not isinstance(raw_ranked, (list, tuple)):
                raise ValueError("ranked_actions must be a list")
            for item in raw_ranked:
                # Some OpenAI-compatible models serialize the same pair as an
                # object. Normalize only this bounded shape before applying the
                # existing action and score validation below.
                if isinstance(item, Mapping):
                    if "action" not in item or "score" not in item:
                        raise ValueError("ranked_actions objects require action and score")
                    item = (item["action"], item["score"])
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    raise ValueError("ranked_actions entries must be [action, score]")
                action = str(item[0]).strip().upper()
                if action not in allowed:
                    raise ValueError(f"ranked action is not allowed: {action}")
                score = _finite("ranked action score", item[1], required=True)
                if not 0.0 <= score <= 1.0:
                    raise ValueError("ranked action score must be between 0 and 1")
                ranked.append((action, score))
        raw_reasons = payload.get("reason_codes", [])
        if raw_reasons is None:
            raw_reasons = []
        if not isinstance(raw_reasons, (list, tuple)):
            raise ValueError("reason_codes must be a list")
        summary = str(payload.get("summary", "") or "").strip()
        if len(summary) > 500:
            raise ValueError("summary is too long")
        resolved_action: DecisionAction | PositionAction
        if raw_action in {item.value for item in DecisionAction}:
            resolved_action = DecisionAction(raw_action)
        elif raw_action in {item.value for item in PositionAction}:
            resolved_action = PositionAction(raw_action)
        else:  # guarded by the allowed-action check above
            raise ValueError(f"unsupported action: {raw_action}")
        return cls(
            action=resolved_action,
            confidence=confidence,
            ranked_actions=tuple((action, score) for action, score in ranked),
            reason_codes=tuple(str(item).strip() for item in raw_reasons if str(item).strip()),
            summary=summary,
        )
