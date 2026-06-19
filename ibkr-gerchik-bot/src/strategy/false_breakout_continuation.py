"""Continuation entry after a prior-session false breakdown at support."""

from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from src.config import LOGGER, SETTINGS
from src.risk.take_profit import reward_risk_ratio
from src.strategy import decision_log
from src.strategy.false_breakout_one_bar import (
    _dict_to_trade_signal,
    _determine_news_risk,
    _empty_result,
    _merged_config,
    _zone_from_level,
)
from src.strategy.levels import Level
from src.strategy.signal_models import TradeSignal

STRATEGY_NAME = "false_breakout_continuation"
PATTERN_NAME = "FALSE_BREAKOUT_CONTINUATION"
MIN_CURRENT_BARS = 3
RECENT_PULLBACK_BARS = 4


def _normalize_bars(bars: pd.DataFrame) -> pd.DataFrame:
    normalized = bars.copy()
    sort_column = "datetime" if "datetime" in normalized.columns else ("date" if "date" in normalized.columns else "")
    if sort_column:
        normalized[sort_column] = pd.to_datetime(normalized[sort_column], errors="coerce")
        normalized = normalized.sort_values(sort_column)
    return normalized.reset_index(drop=True)


def _split_previous_and_current_session(bars: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    for column in ("datetime", "date"):
        if column not in bars.columns:
            continue
        parsed = pd.to_datetime(bars[column], errors="coerce")
        if parsed.isna().all():
            continue
        session_dates = parsed.dt.date
        latest_session = session_dates.iloc[-1]
        current = bars[session_dates == latest_session]
        previous = bars[session_dates < latest_session]
        if not previous.empty and len(current) >= MIN_CURRENT_BARS:
            return previous.reset_index(drop=True), current.reset_index(drop=True)

    split_at = max(0, len(bars) - max(MIN_CURRENT_BARS + 2, RECENT_PULLBACK_BARS))
    return bars.iloc[:split_at].reset_index(drop=True), bars.iloc[split_at:].reset_index(drop=True)


def _is_prior_false_breakdown(previous: pd.DataFrame, level: Level) -> bool:
    if previous.empty:
        return False
    zone = _zone_from_level(level, _merged_config(None))
    broke_below = float(previous["low"].min()) < zone.zone_low
    reclaimed_level = float(previous.iloc[-1]["close"]) > zone.level_price
    return broke_below and reclaimed_level


def _current_session_uptrend_from_level(current: pd.DataFrame, level: Level) -> bool:
    if len(current) < MIN_CURRENT_BARS:
        return False
    zone = _zone_from_level(level, _merged_config(None))
    recent = current.tail(max(MIN_CURRENT_BARS, RECENT_PULLBACK_BARS))
    closes = recent["close"].astype(float)
    lows = recent["low"].astype(float)
    latest_close = float(closes.iloc[-1])
    held_reclaim = float(lows.min()) >= zone.zone_low or float(recent.tail(2)["low"].min()) >= zone.level_price
    directional = latest_close > float(closes.iloc[0]) and latest_close > float(closes.iloc[-2])
    above_reclaim = latest_close > zone.zone_high
    return held_reclaim and directional and above_reclaim


def _continuation_stop(current: pd.DataFrame, entry: float) -> Optional[float]:
    recent = current.tail(max(MIN_CURRENT_BARS, RECENT_PULLBACK_BARS))
    recent_low = float(recent["low"].astype(float).min())
    max_distance = entry * SETTINGS.risk.calculated_stop_pct * SETTINGS.risk.max_stop_vs_calculated_multiplier * 0.95
    if max_distance <= 0:
        return None
    stop = max(recent_low, entry - max_distance)
    if stop >= entry:
        stop = entry - max_distance
    return round(stop, 2)


def detect_false_breakout_continuation(
    symbol: str,
    bars: pd.DataFrame,
    level: Level,
    news_context: Optional[Dict[str, object]] = None,
    decision_sink: decision_log.DecisionSink = None,
) -> Optional[TradeSignal]:
    """Detect a long follow-through after a prior false breakdown at support."""
    if bars.empty or len(bars) < MIN_CURRENT_BARS + 2:
        result = _empty_result("insufficient candles", _context(level, "LOW", "LOW"))
        decision_log.record(decision_sink, symbol, level, STRATEGY_NAME, result)
        return None

    news_risk = _determine_news_risk(news_context)
    if news_risk == "HIGH":
        result = _empty_result("news risk high", _context(level, "OK", news_risk))
        decision_log.record(decision_sink, symbol, level, STRATEGY_NAME, result)
        return None

    normalized = _normalize_bars(bars)
    previous, current = _split_previous_and_current_session(normalized)
    if not _is_prior_false_breakdown(previous, level):
        result = _empty_result("no prior false breakdown", _context(level, "OK", news_risk))
        decision_log.record(decision_sink, symbol, level, STRATEGY_NAME, result)
        return None
    if not _current_session_uptrend_from_level(current, level):
        result = _empty_result("no continuation uptrend from level", _context(level, "OK", news_risk))
        decision_log.record(decision_sink, symbol, level, STRATEGY_NAME, result)
        return None

    entry = round(float(current.iloc[-1]["close"]), 2)
    stop = _continuation_stop(current, entry)
    if stop is None or stop >= entry:
        result = _empty_result("invalid continuation stop", _context(level, "LOW", news_risk))
        decision_log.record(decision_sink, symbol, level, STRATEGY_NAME, result)
        return None

    target = _target_for_continuation(level, entry, stop)
    if target is None:
        result = _empty_result("reward risk too low", _context(level, "LOW", news_risk))
        decision_log.record(decision_sink, symbol, level, STRATEGY_NAME, result)
        return None

    score = _continuation_score(previous, current, level)
    context = _context(level, "OK", news_risk)
    result = {
        "signal": "BUY",
        "entry": entry,
        "stop": stop,
        "target": target,
        "position_modifier": 0.5 if news_risk == "MEDIUM" else 1.0,
        "confidence": round(min(score, 100.0) / 100.0, 2),
        "reason": ["prior false breakdown continuation"],
        "context": context,
    }
    LOGGER.info("Detected false breakout continuation long %s at %.2f level=%.2f", symbol, entry, level.price)
    decision_log.record(decision_sink, symbol, level, STRATEGY_NAME, result)
    return _dict_to_trade_signal(symbol, level, STRATEGY_NAME, result)


def _target_for_continuation(level: Level, entry: float, stop: float) -> Optional[float]:
    if isinstance(level.nearest_upper_level, float) and level.nearest_upper_level > entry:
        if reward_risk_ratio(entry, stop, level.nearest_upper_level) >= SETTINGS.risk.min_reward_risk_ratio:
            return round(level.nearest_upper_level, 2)
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    return round(entry + (SETTINGS.risk.min_reward_risk_ratio * risk), 2)


def _continuation_score(previous: pd.DataFrame, current: pd.DataFrame, level: Level) -> float:
    zone = _zone_from_level(level, _merged_config(None))
    prior_extension = max(zone.zone_low - float(previous["low"].min()), 0.0)
    current = current.tail(max(MIN_CURRENT_BARS, RECENT_PULLBACK_BARS))
    close_progress = float(current.iloc[-1]["close"]) - float(current.iloc[0]["close"])
    score = 55.0
    if prior_extension > 0:
        score += 10.0
    if close_progress > 0:
        score += 15.0
    if float(current["low"].min()) >= zone.level_price:
        score += 10.0
    if level.false_breakouts > 0:
        score += 10.0
    return score


def _context(level: Level, atr_status: str, news_risk: str) -> Dict[str, object]:
    zone = _zone_from_level(level, _merged_config(None))
    return {
        "trend": "UP",
        "atr_status": atr_status,
        "news_risk": news_risk,
        "zone": [round(zone.zone_low, 2), round(zone.zone_high, 2)],
        "pattern": PATTERN_NAME,
    }
