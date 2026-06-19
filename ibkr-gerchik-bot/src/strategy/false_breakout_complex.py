"""Gerchik-style complex 3+ bar false breakout detection helpers with explainability."""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from src.config import LOGGER
from src.strategy import decision_log
from src.strategy.candles import full_range
from src.strategy.false_breakout_one_bar import (
    _build_context,
    _build_signal_dict,
    _confirmation_count,
    _confirmations_ok,
    _determine_news_risk,
    _dict_to_trade_signal,
    _empty_result,
    _evaluate_atr_status,
    _merged_config,
    _resolve_atr,
    _stop_buffer,
    _target_for_direction,
    _trend_context_ok,
    _trend_label,
    _zone_from_level,
)
from src.strategy.levels import Level
from src.strategy.signal_models import TradeSignal


def _complex_score(trap_candles: pd.DataFrame, return_candle: pd.Series, confirmation: pd.Series, direction: str) -> float:
    score = 50.0
    trap_ranges = (trap_candles["high"].astype(float) - trap_candles["low"].astype(float)).tolist()
    average_trap_range = sum(trap_ranges) / max(len(trap_ranges), 1)
    if direction == "long":
        score += 10.0 if float(return_candle["close"]) > float(trap_candles.iloc[-1]["close"]) else 0.0
        score += 10.0 if float(confirmation["close"]) > float(return_candle["close"]) else 0.0
    else:
        score += 10.0 if float(return_candle["close"]) < float(trap_candles.iloc[-1]["close"]) else 0.0
        score += 10.0 if float(confirmation["close"]) < float(return_candle["close"]) else 0.0
    score += 10.0 if average_trap_range > 0 else 0.0
    return score


def detect_false_breakout(
    candles: pd.DataFrame,
    level: Level,
    atr: Optional[float] = None,
    news_context: Optional[Dict[str, object]] = None,
    config: Optional[Dict[str, float]] = None,
) -> Dict[str, object]:
    """Detect a complex 3+ bar false breakout structure around a level zone."""
    merged_config = _merged_config(config)
    news_risk = _determine_news_risk(news_context)
    normalized = candles.reset_index(drop=True)
    zone = _zone_from_level(level, merged_config)

    if len(normalized) < 5:
        context = _build_context(trend="RANGE", atr_status="LOW", news_risk=news_risk, zone=zone, pattern="COMPLEX")
        return _empty_result(["insufficient candles"], context)

    confirmation_count = _confirmation_count(news_risk)
    minimum_length = confirmation_count + 4
    if len(normalized) < minimum_length:
        context = _build_context(trend="RANGE", atr_status="LOW", news_risk=news_risk, zone=zone, pattern="COMPLEX")
        return _empty_result(["insufficient candles"], context)

    structure = normalized.tail(min(len(normalized), confirmation_count + 5)).reset_index(drop=True)
    confirmations = [structure.iloc[-idx] for idx in range(confirmation_count, 0, -1)]
    return_candle = structure.iloc[-(confirmation_count + 1)]
    trap_candles = structure.iloc[: -(confirmation_count + 1)]
    prior_context = normalized.iloc[: -len(structure)]
    trend_source = prior_context if len(prior_context) >= 4 else normalized.head(len(normalized) - confirmation_count)
    atr_value = _resolve_atr(normalized, level, atr)
    context = _build_context(
        trend=_trend_label(trend_source),
        atr_status="OK" if atr_value > 0 else "LOW",
        news_risk=news_risk,
        zone=zone,
        pattern="COMPLEX",
    )
    LOGGER.info(
        "Complex false breakout level=%.2f zone=(%.2f, %.2f) atr=%.4f news_risk=%s",
        zone.level_price,
        zone.zone_low,
        zone.zone_high,
        atr_value,
        news_risk,
    )

    reasons: List[str] = []
    if news_risk == "HIGH":
        reasons.append("news risk high")
        return _empty_result(reasons, context)
    if len(trap_candles) < 3:
        reasons.append("insufficient candles")
        return _empty_result(reasons, context)
    if atr_value > 0 and any(
        full_range(trap_candles.iloc[idx]) > atr_value * merged_config["volatility_spike_atr_multiplier"]
        for idx in range(len(trap_candles))
    ):
        reasons.append("abnormal volatility spike")
        return _empty_result(reasons, context)

    minimum_close_location = merged_config["confirmation_close_location"]

    long_trap = all(float(trap_candles.iloc[idx]["close"]) < zone.zone_low for idx in range(len(trap_candles)))
    long_return = float(return_candle["close"]) > zone.zone_low
    long_no_impulse = max(float(trap_candles.iloc[idx]["high"]) for idx in range(len(trap_candles))) <= zone.zone_high + max(atr_value * 0.2, 0.1)
    if long_trap and long_return and long_no_impulse:
        total_extension = zone.zone_low - min(float(trap_candles.iloc[idx]["low"]) for idx in range(len(trap_candles)))
        if atr_value > 0 and total_extension > atr_value * 0.7:
            reasons.append("move already extended")
            context["atr_status"] = "OVEREXTENDED"
            return _empty_result(reasons, context)
        if not _confirmations_ok(confirmations, "long", minimum_close_location):
            reasons.append("no confirmation candle")
            return _empty_result(reasons, context)
        if not _trend_context_ok(trend_source, "long", atr_value):
            reasons.append("trend mismatch")
            return _empty_result(reasons, context)
        entry = float(confirmations[-1]["close"])
        structure_low = min(
            min(float(trap_candles.iloc[idx]["low"]) for idx in range(len(trap_candles))),
            float(return_candle["low"]),
            *(float(candle["low"]) for candle in confirmations),
        )
        stop = round(structure_low - _stop_buffer(level), 2)
        target = _target_for_direction(level, "long", entry, stop)
        if target is None:
            reasons.append("ATR too small")
            context["atr_status"] = "LOW"
            return _empty_result(reasons, context)
        context["atr_status"] = _evaluate_atr_status(entry, target, zone, atr_value, merged_config)
        if context["atr_status"] == "LOW":
            reasons.append("ATR too small")
            return _empty_result(reasons, context)
        if context["atr_status"] == "OVEREXTENDED":
            reasons.append("move already extended")
            return _empty_result(reasons, context)
        score = _complex_score(trap_candles, return_candle, confirmations[-1], "long")
        if score < merged_config["score_threshold"]:
            reasons.append("weak setup score")
            return _empty_result(reasons, context)
        reasons.append("clean false breakout return")
        LOGGER.info("Detected complex long false breakout at %.2f score=%.2f", entry, score)
        return _build_signal_dict(
            signal="BUY",
            entry=entry,
            stop=stop,
            target=target,
            news_risk=news_risk,
            score=score,
            reasons=reasons,
            context=context,
        )

    short_trap = all(float(trap_candles.iloc[idx]["close"]) > zone.zone_high for idx in range(len(trap_candles)))
    short_return = float(return_candle["close"]) < zone.zone_high
    short_no_impulse = min(float(trap_candles.iloc[idx]["low"]) for idx in range(len(trap_candles))) >= zone.zone_low - max(atr_value * 0.2, 0.1)
    if short_trap and short_return and short_no_impulse:
        total_extension = max(float(trap_candles.iloc[idx]["high"]) for idx in range(len(trap_candles))) - zone.zone_high
        if atr_value > 0 and total_extension > atr_value * 0.7:
            reasons.append("move already extended")
            context["atr_status"] = "OVEREXTENDED"
            return _empty_result(reasons, context)
        if not _confirmations_ok(confirmations, "short", minimum_close_location):
            reasons.append("no confirmation candle")
            return _empty_result(reasons, context)
        if not _trend_context_ok(trend_source, "short", atr_value):
            reasons.append("trend mismatch")
            return _empty_result(reasons, context)
        entry = float(confirmations[-1]["close"])
        structure_high = max(
            max(float(trap_candles.iloc[idx]["high"]) for idx in range(len(trap_candles))),
            float(return_candle["high"]),
            *(float(candle["high"]) for candle in confirmations),
        )
        stop = round(structure_high + _stop_buffer(level), 2)
        target = _target_for_direction(level, "short", entry, stop)
        if target is None:
            reasons.append("ATR too small")
            context["atr_status"] = "LOW"
            return _empty_result(reasons, context)
        context["atr_status"] = _evaluate_atr_status(entry, target, zone, atr_value, merged_config)
        if context["atr_status"] == "LOW":
            reasons.append("ATR too small")
            return _empty_result(reasons, context)
        if context["atr_status"] == "OVEREXTENDED":
            reasons.append("move already extended")
            return _empty_result(reasons, context)
        score = _complex_score(trap_candles, return_candle, confirmations[-1], "short")
        if score < merged_config["score_threshold"]:
            reasons.append("weak setup score")
            return _empty_result(reasons, context)
        reasons.append("clean false breakout return")
        LOGGER.info("Detected complex short false breakout at %.2f score=%.2f", entry, score)
        return _build_signal_dict(
            signal="SELL",
            entry=entry,
            stop=stop,
            target=target,
            news_risk=news_risk,
            score=score,
            reasons=reasons,
            context=context,
        )

    if not long_trap and not short_trap:
        reasons.append("no breakout")
    elif (long_trap and not long_return) or (short_trap and not short_return):
        reasons.append("no return inside zone")
    else:
        reasons.append("no strong continuation failure")
    return _empty_result(reasons, context)


def detect_false_breakout_complex(
    symbol: str,
    bars: pd.DataFrame,
    level: Level,
    atr: Optional[float] = None,
    news_context: Optional[Dict[str, object]] = None,
    config: Optional[Dict[str, float]] = None,
    decision_sink: decision_log.DecisionSink = None,
) -> Optional[TradeSignal]:
    """Router-compatible wrapper returning a TradeSignal for complex false breakouts."""
    result = detect_false_breakout(bars, level, atr=atr, news_context=news_context, config=config)
    decision_log.record(decision_sink, symbol, level, "false_breakout_complex", result)
    return _dict_to_trade_signal(symbol, level, "false_breakout_complex", result)

