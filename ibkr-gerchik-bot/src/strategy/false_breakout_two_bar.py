"""Gerchik-style two-bar false breakout detection helpers with explainability."""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from src.config import LOGGER
from src.strategy.candles import body_size, full_range
from src.strategy.false_breakout_one_bar import (
    _atr_filters_ok,
    _build_context,
    _build_signal_dict,
    _confirmation_count,
    _confirmations_ok,
    _determine_news_risk,
    _dict_to_trade_signal,
    _empty_result,
    _evaluate_atr_status,
    _is_volatility_spike,
    _merged_config,
    _persist_daily_decision,
    _position_modifier,
    _resolve_atr,
    _target_for_direction,
    _trend_context_ok,
    _trend_label,
    _zone_from_level,
)
from src.strategy.levels import Level
from src.strategy.signal_models import TradeSignal


def _two_bar_long_score(first: pd.Series, second: pd.Series, confirmation: pd.Series, zone_high: float) -> float:
    score = 45.0
    score += 10.0 if float(first["low"]) < zone_high else 0.0
    score += 10.0 if float(second["close"]) > float(first["close"]) else 0.0
    score += 10.0 if float(confirmation["close"]) > float(second["close"]) else 0.0
    score += 10.0 if body_size(second) >= body_size(first) * 0.7 else 0.0
    return score


def _two_bar_short_score(first: pd.Series, second: pd.Series, confirmation: pd.Series, zone_low: float) -> float:
    score = 45.0
    score += 10.0 if float(first["high"]) > zone_low else 0.0
    score += 10.0 if float(second["close"]) < float(first["close"]) else 0.0
    score += 10.0 if float(confirmation["close"]) < float(second["close"]) else 0.0
    score += 10.0 if body_size(second) >= body_size(first) * 0.7 else 0.0
    return score


def detect_false_breakout(
    candles: pd.DataFrame,
    level: Level,
    atr: Optional[float] = None,
    news_context: Optional[Dict[str, object]] = None,
    config: Optional[Dict[str, float]] = None,
) -> Dict[str, object]:
    """Detect a two-bar false breakout structure around a level zone."""
    merged_config = _merged_config(config)
    news_risk = _determine_news_risk(news_context)
    normalized = candles.reset_index(drop=True)
    zone = _zone_from_level(level, merged_config)
    atr_value = _resolve_atr(normalized, level, atr)

    if len(normalized) < 4:
        context = _build_context(trend="RANGE", atr_status="LOW", news_risk=news_risk, zone=zone, pattern="TWO_BAR")
        return _empty_result(["insufficient candles"], context)

    confirmation_count = _confirmation_count(news_risk)
    if len(normalized) < confirmation_count + 3:
        context = _build_context(trend="RANGE", atr_status="LOW", news_risk=news_risk, zone=zone, pattern="TWO_BAR")
        return _empty_result(["insufficient candles"], context)

    first = normalized.iloc[-(confirmation_count + 2)]
    second = normalized.iloc[-(confirmation_count + 1)]
    confirmations = [normalized.iloc[-idx] for idx in range(confirmation_count, 0, -1)]
    prior_context = normalized.iloc[: -(confirmation_count + 2)]
    trend_source = prior_context if len(prior_context) >= 4 else normalized.head(len(normalized) - confirmation_count)
    context = _build_context(
        trend=_trend_label(trend_source),
        atr_status="OK" if atr_value > 0 else "LOW",
        news_risk=news_risk,
        zone=zone,
        pattern="TWO_BAR",
    )
    LOGGER.info(
        "Two-bar false breakout level=%.2f zone=(%.2f, %.2f) atr=%.4f news_risk=%s",
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

    if _is_volatility_spike([first, second, *confirmations], atr_value, merged_config):
        reasons.append("abnormal volatility spike")
        return _empty_result(reasons, context)

    long_break = float(first["close"]) < zone.zone_low
    long_return = float(second["close"]) > zone.zone_low
    long_no_continuation = float(second["low"]) >= float(first["low"])
    if long_break and long_return and long_no_continuation:
        if not _confirmations_ok(confirmations, "long", merged_config["confirmation_close_location"]):
            reasons.append("no confirmation candle")
            return _empty_result(reasons, context)
        if not _trend_context_ok(trend_source, "long", atr_value):
            reasons.append("trend mismatch")
            return _empty_result(reasons, context)
        entry = float(confirmations[-1]["close"])
        stop = min(float(first["low"]), float(second["low"]), *(float(candle["low"]) for candle in confirmations))
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
        score = _two_bar_long_score(first, second, confirmations[-1], zone.zone_high)
        if score < merged_config["score_threshold"]:
            reasons.append("weak setup score")
            return _empty_result(reasons, context)
        reasons.append("clean false breakout return")
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

    short_break = float(first["close"]) > zone.zone_high
    short_return = float(second["close"]) < zone.zone_high
    short_no_continuation = float(second["high"]) <= float(first["high"])
    if short_break and short_return and short_no_continuation:
        if not _confirmations_ok(confirmations, "short", merged_config["confirmation_close_location"]):
            reasons.append("no confirmation candle")
            return _empty_result(reasons, context)
        if not _trend_context_ok(trend_source, "short", atr_value):
            reasons.append("trend mismatch")
            return _empty_result(reasons, context)
        entry = float(confirmations[-1]["close"])
        stop = max(float(first["high"]), float(second["high"]), *(float(candle["high"]) for candle in confirmations))
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
        score = _two_bar_short_score(first, second, confirmations[-1], zone.zone_low)
        if score < merged_config["score_threshold"]:
            reasons.append("weak setup score")
            return _empty_result(reasons, context)
        reasons.append("clean false breakout return")
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

    if not long_break and not short_break:
        reasons.append("no breakout")
    elif (long_break and not long_return) or (short_break and not short_return):
        reasons.append("no return inside zone")
    else:
        reasons.append("no strong continuation failure")
    return _empty_result(reasons, context)


def detect_false_breakout_two_bar(
    symbol: str,
    bars: pd.DataFrame,
    level: Level,
    atr: Optional[float] = None,
    news_context: Optional[Dict[str, object]] = None,
    config: Optional[Dict[str, float]] = None,
) -> Optional[TradeSignal]:
    """Router-compatible wrapper returning a TradeSignal for two-bar false breakouts."""
    result = detect_false_breakout(bars, level, atr=atr, news_context=news_context, config=config)
    _persist_daily_decision(symbol, level, "false_breakout_two_bar", result)
    return _dict_to_trade_signal(symbol, level, "false_breakout_two_bar", result)

