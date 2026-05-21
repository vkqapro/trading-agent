"""Central strategy router."""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from src.config import LOGGER, SETTINGS
from src.risk.stop_loss import calculate_stop_loss
from src.risk.take_profit import build_partial_targets, calculate_take_profit, reward_risk_ratio
from src.strategy import false_breakout_one_bar as daily_decisions
from src.strategy.breakout import detect_breakout
from src.strategy.false_breakout_complex import detect_false_breakout_complex
from src.strategy.false_breakout_continuation import detect_false_breakout_continuation
from src.strategy.false_breakout_one_bar import detect_false_breakout_one_bar
from src.strategy.false_breakout_two_bar import detect_false_breakout_two_bar
from src.strategy.levels import Level
from src.strategy.rebound import detect_rebound
from src.strategy.signal_models import TradeSignal


def _target_is_on_correct_side(entry: float, target: Optional[float], direction: str) -> bool:
    if target is None:
        return False
    if direction == "long":
        return target > entry
    if direction == "short":
        return target < entry
    return False


def _target_reference(signal: TradeSignal) -> Optional[float]:
    next_level = signal.nearest_upper_level if signal.direction == "long" else signal.nearest_lower_level
    if isinstance(next_level, float) and _target_is_on_correct_side(signal.entry, next_level, signal.direction):
        return next_level
    return signal.target if _target_is_on_correct_side(signal.entry, signal.target, signal.direction) else None


def _level_zone(level: Level) -> list[float]:
    zone_low = level.zone_low if level.zone_low is not None else level.price
    zone_high = level.zone_high if level.zone_high is not None else level.price
    return [round(float(zone_low), 2), round(float(zone_high), 2)]


def _note_value(signal: TradeSignal, prefix: str, default: object) -> object:
    for note in signal.notes:
        if note.startswith(prefix):
            return note.removeprefix(prefix).strip()
    return default


def _signal_confidence(signal: TradeSignal) -> float:
    try:
        return float(_note_value(signal, "confidence=", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _signal_position_modifier(signal: TradeSignal) -> float:
    try:
        return float(_note_value(signal, "position_modifier=", 1.0))
    except (TypeError, ValueError):
        return 1.0


def _signal_original_reasons(signal: TradeSignal) -> List[str]:
    reasons = str(_note_value(signal, "reasons=", "") or "").strip()
    if not reasons or reasons == "none":
        return []
    return [reason.strip() for reason in reasons.split(",") if reason.strip()]


def _signal_context(signal: TradeSignal, level: Level, status: str) -> Dict[str, object]:
    raw_context = str(_note_value(signal, "context ", "") or "").strip()
    parsed: Dict[str, str] = {}
    for item in raw_context.split():
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parsed[key.strip()] = value.strip()
    return {
        "trend": parsed.get("trend", "RANGE"),
        "atr_status": parsed.get("atr", "OK" if status == "accepted" else "ROUTER_REJECTED"),
        "news_risk": parsed.get("news", "UNKNOWN"),
        "zone": _level_zone(level),
        "pattern": parsed.get("pattern", str(signal.strategy).upper()),
    }


def _router_result(
    *,
    signal: TradeSignal,
    level: Level,
    status: str,
    reasons: List[str],
    stop: Optional[float] = None,
    target: Optional[float] = None,
) -> Dict[str, object]:
    return {
        "signal": signal.signal if status == "accepted" else "NONE",
        "entry": signal.entry,
        "stop": stop if stop is not None else signal.stop,
        "target": target if target is not None else signal.target,
        "position_modifier": _signal_position_modifier(signal),
        "confidence": _signal_confidence(signal),
        "reason": reasons,
        "context": _signal_context(signal, level, status),
        "router_status": status,
        "raw_signal": signal.signal,
    }


def _persist_router_decision(
    *,
    symbol: str,
    level: Level,
    signal: TradeSignal,
    status: str,
    reasons: List[str],
    stop: Optional[float] = None,
    target: Optional[float] = None,
) -> None:
    if not str(signal.strategy).startswith("false_breakout"):
        return
    result = _router_result(signal=signal, level=level, status=status, reasons=reasons, stop=stop, target=target)
    daily_decisions._persist_daily_decision(symbol, level, signal.strategy, result)


def _stop_rejection_reason(signal: TradeSignal) -> str:
    technical_distance = abs(signal.entry - signal.stop)
    max_distance = signal.entry * SETTINGS.risk.calculated_stop_pct * SETTINGS.risk.max_stop_vs_calculated_multiplier
    if technical_distance <= 0:
        return "router_rejected: invalid_stop_distance"
    if technical_distance > max_distance:
        return f"router_rejected: stop_too_wide_after_rounding risk={technical_distance:.4f} max={max_distance:.4f}"
    return "router_rejected: invalid_stop"


def _target_rejection_reason(signal: TradeSignal, target_reference: Optional[float], adjusted_stop: float) -> str:
    if target_reference is None:
        if signal.direction == "long":
            return "router_rejected: invalid_long_target_below_entry"
        if signal.direction == "short":
            return "router_rejected: invalid_short_target_above_entry"
        return "router_rejected: missing_target"
    reward_risk = reward_risk_ratio(signal.entry, adjusted_stop, target_reference)
    return f"router_rejected: reward_risk_too_low_after_router rr={reward_risk:.2f}"


def route_strategies(
    symbol: str,
    intraday_bars: pd.DataFrame,
    levels: List[Level],
    news_context: Optional[Dict[str, object]] = None,
) -> List[TradeSignal]:
    signals: List[TradeSignal] = []
    for level in levels:
        candidates = [
            detect_rebound(symbol, intraday_bars, level),
            detect_breakout(symbol, intraday_bars, level),
            detect_false_breakout_one_bar(symbol, intraday_bars, level, news_context=news_context),
            detect_false_breakout_two_bar(symbol, intraday_bars, level, news_context=news_context),
            detect_false_breakout_complex(symbol, intraday_bars, level, news_context=news_context),
            detect_false_breakout_continuation(symbol, intraday_bars, level, news_context=news_context),
        ]
        for signal in candidates:
            if signal is None:
                continue
            adjusted_stop = calculate_stop_loss(signal.entry, signal.stop, signal.direction)
            if adjusted_stop is None:
                reasons = [_stop_rejection_reason(signal), *_signal_original_reasons(signal)]
                _persist_router_decision(symbol=symbol, level=level, signal=signal, status="rejected", reasons=reasons)
                LOGGER.info("Router rejected %s %s: %s", symbol, signal.strategy, reasons[0])
                continue
            target_reference = _target_reference(signal)
            target = calculate_take_profit(signal.entry, adjusted_stop, target_reference, signal.direction)
            if target is None:
                reasons = [_target_rejection_reason(signal, target_reference, adjusted_stop), *_signal_original_reasons(signal)]
                _persist_router_decision(
                    symbol=symbol,
                    level=level,
                    signal=signal,
                    status="rejected",
                    reasons=reasons,
                    stop=adjusted_stop,
                )
                LOGGER.info("Router rejected %s %s: %s", symbol, signal.strategy, reasons[0])
                continue
            signal.stop = adjusted_stop
            signal.target = target
            signal.reward_risk = round(reward_risk_ratio(signal.entry, signal.stop, signal.target), 2)
            signal.partial_targets = build_partial_targets(signal.entry, signal.stop, signal.target, signal.direction)
            _persist_router_decision(
                symbol=symbol,
                level=level,
                signal=signal,
                status="accepted",
                reasons=["router_accepted", *_signal_original_reasons(signal)],
                stop=adjusted_stop,
                target=target,
            )
            signals.append(signal)
    return signals
