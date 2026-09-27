"""Adapters from deterministic strategy signals into decision contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Mapping

from src.strategy.signal_models import TradeSignal

from .models import DecisionCandidate, sanitize_mapping


def _signal_timestamp(signal: TradeSignal) -> tuple[datetime, bool, str | None]:
    metadata = signal.metadata if isinstance(signal.metadata, Mapping) else {}
    raw = (
        metadata.get("signal_timestamp")
        or metadata.get("setup_timestamp")
        or metadata.get("timestamp")
        or metadata.get("source_bar_timestamp")
        or metadata.get("bar_timestamp")
        or metadata.get("candle_timestamp")
    )
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return (
                (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc),
                True,
                str(raw),
            )
        except ValueError:
            pass
    return datetime.now(timezone.utc), False, None


def _stable_setup_payload(
    signal: TradeSignal,
    *,
    setup_timestamp: str | None,
    source_signal_id: str | None,
) -> dict[str, object]:
    metadata = signal.metadata if isinstance(signal.metadata, Mapping) else {}
    stable_source = source_signal_id or metadata.get("signal_id") or metadata.get("source_signal_id")
    return {
        "asset_class": str(metadata.get("asset_class") or "stock").lower(),
        "symbol": signal.symbol.upper(),
        "strategy": signal.strategy,
        "direction": signal.direction,
        "level_price": signal.level_price,
        "level_type": signal.level_type,
        "entry": signal.entry,
        "stop": signal.stop,
        "target": signal.target,
        "setup_timestamp": str(setup_timestamp) if setup_timestamp else None,
        "source_signal_id": str(stable_source) if stable_source else None,
    }


def trade_signal_to_candidate(
    signal: TradeSignal,
    *,
    source_signal_id: str | None = None,
    market_context: Mapping[str, object] | None = None,
) -> DecisionCandidate:
    """Adapt a fully normalized ``TradeSignal`` without recalculating it."""
    created_at, identity_stable, setup_timestamp = _signal_timestamp(signal)
    stable_payload = _stable_setup_payload(
        signal,
        setup_timestamp=setup_timestamp,
        source_signal_id=source_signal_id,
    )
    digest = hashlib.sha256(
        json.dumps(stable_payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:32]
    metadata = dict(signal.metadata or {})
    metadata["candidate_key"] = stable_payload
    metadata["source_signal_id"] = source_signal_id
    metadata["identity_status"] = "stable" if identity_stable or stable_payload.get("source_signal_id") else "unstable"
    return DecisionCandidate(
        candidate_id=f"gerchik-{digest}",
        created_at=created_at,
        asset_class=str(metadata.get("asset_class") or "stock").lower(),
        symbol=signal.symbol.upper(),
        strategy=signal.strategy,
        direction=signal.direction,
        entry=signal.entry,
        stop=signal.stop,
        target=signal.target,
        level_price=signal.level_price,
        level_type=signal.level_type,
        level_strength=signal.level_strength,
        atr=signal.atr,
        reward_risk=signal.reward_risk,
        risk_per_share=signal.risk_per_share,
        confidence=signal.confidence,
        partial_targets=tuple(dict(item) for item in signal.partial_targets),
        source_signal_id=source_signal_id,
        market_context=dict(sanitize_mapping(market_context or {})),
        metadata=metadata,
    )


def mapping_to_candidate(
    payload: Mapping[str, object],
    *,
    asset_class: str = "crypto",
    source_signal_id: str | None = None,
    market_context: Mapping[str, object] | None = None,
) -> DecisionCandidate:
    """Adapt a calculated non-stock trade plan to the common decision contract."""
    symbol = str(_first(payload, "symbol", "instrument", "instId", default="")).strip().upper()
    strategy = str(_first(payload, "strategy", "strategy_name", default="crypto_strategy"))
    direction = str(_first(payload, "direction", "side", default="long")).lower()
    entry = float(_first(payload, "entry", "entry_price", default=0.0) or 0.0)
    stop = float(_first(payload, "stop", "stop_loss", "stop_price", default=0.0) or 0.0)
    target = float(_first(payload, "target", "take_profit", "target_price", default=0.0) or 0.0)
    setup_timestamp = str(
        _first(
            payload,
            "created_at",
            "timestamp",
            "signal_timestamp",
            "source_bar_timestamp",
            "bar_timestamp",
            "candle_timestamp",
            default="",
        )
    )
    provided_candidate_id = str(_first(payload, "candidate_id", default="") or "").strip() or None
    source_identity = source_signal_id or str(_first(payload, "signal_id", "source_signal_id", default="")) or None
    try:
        created_at = datetime.fromisoformat(setup_timestamp.replace("Z", "+00:00")) if setup_timestamp else datetime.now(timezone.utc)
    except ValueError:
        created_at = datetime.now(timezone.utc)
    stable_payload = {
        "asset_class": asset_class,
        "symbol": symbol,
        "strategy": strategy,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "setup_timestamp": setup_timestamp or None,
        "source_signal_id": source_identity,
    }
    digest = hashlib.sha256(json.dumps(stable_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:32]
    return DecisionCandidate(
        candidate_id=provided_candidate_id or f"gerchik-{digest}",
        created_at=created_at,
        asset_class=asset_class,
        symbol=symbol,
        strategy=strategy,
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        level_price=_float_or_none(_first(payload, "level_price", "level")),
        level_type=str(_first(payload, "level_type", default="")),
        atr=_float_or_none(_first(payload, "atr", "ATR")),
        reward_risk=_float_or_none(_first(payload, "reward_risk", "rr", "risk_reward")),
        risk_per_share=_float_or_none(_first(payload, "risk_per_share", "risk_per_unit")),
        confidence=_float_or_none(_first(payload, "confidence", "score")),
        source_signal_id=source_identity,
        market_context=dict(sanitize_mapping(market_context or {})),
        metadata={
            "candidate_key": stable_payload,
            "source": "mapping_adapter",
            "identity_status": "stable" if setup_timestamp or source_identity or provided_candidate_id else "unstable",
        },
    )


def _first(payload: Mapping[str, object], *names: str, default: object = None) -> object:
    for name in names:
        if name in payload and payload[name] is not None:
            return payload[name]
    return default


def _float_or_none(value: object) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
