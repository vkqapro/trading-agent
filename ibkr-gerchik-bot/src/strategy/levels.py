"""Level detection helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

import pandas as pd

from src.config import SETTINGS


@dataclass
class Level:
    symbol: str
    price: float
    type: str
    timeframe: str
    touches: int
    false_breakouts: int
    strength_score: float
    created_by: str
    nearest_upper_level: Optional[float]
    nearest_lower_level: Optional[float]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def _round_price(price: float) -> float:
    return round(float(price), 2)


def _touches_near_price(bars: pd.DataFrame, price: float, tolerance_pct: float) -> int:
    tolerance = max(price * tolerance_pct, 0.05)
    touches = 0
    for _, row in bars.iterrows():
        if abs(float(row["high"]) - price) <= tolerance or abs(float(row["low"]) - price) <= tolerance:
            touches += 1
    return touches


def _false_breakout_count(bars: pd.DataFrame, price: float, tolerance_pct: float) -> int:
    tolerance = max(price * tolerance_pct, 0.05)
    count = 0
    for _, row in bars.iterrows():
        low = float(row["low"])
        high = float(row["high"])
        close = float(row["close"])
        if low < price - tolerance < close or high > price + tolerance > close:
            count += 1
    return count


def _make_level(symbol: str, price: float, level_type: str, timeframe: str, bars: pd.DataFrame, created_by: str) -> Level:
    touches = _touches_near_price(bars, price, SETTINGS.strategy.level_tolerance_pct)
    false_breakouts = _false_breakout_count(bars, price, SETTINGS.strategy.level_tolerance_pct)
    return Level(
        symbol=symbol,
        price=_round_price(price),
        type=level_type,
        timeframe=timeframe,
        touches=touches,
        false_breakouts=false_breakouts,
        strength_score=0.0,
        created_by=created_by,
        nearest_upper_level=None,
        nearest_lower_level=None,
    )


def detect_levels(symbol: str, daily_bars: pd.DataFrame, intraday_bars: pd.DataFrame) -> List[Level]:
    """Detect Gerchik-style levels from daily bars, refined with intraday context."""
    levels: List[Level] = []
    if daily_bars.empty:
        return levels

    avg_range = float((daily_bars["high"] - daily_bars["low"]).mean()) if not daily_bars.empty else 0.0

    # Trend reversal / historical swing levels.
    for idx in range(1, len(daily_bars) - 1):
        prev_high = float(daily_bars.iloc[idx - 1]["high"])
        current_high = float(daily_bars.iloc[idx]["high"])
        next_high = float(daily_bars.iloc[idx + 1]["high"])
        prev_low = float(daily_bars.iloc[idx - 1]["low"])
        current_low = float(daily_bars.iloc[idx]["low"])
        next_low = float(daily_bars.iloc[idx + 1]["low"])
        if current_high >= prev_high and current_high >= next_high:
            levels.append(_make_level(symbol, current_high, "historical", "daily", daily_bars, "swing_high"))
        if current_low <= prev_low and current_low <= next_low:
            levels.append(_make_level(symbol, current_low, "historical", "daily", daily_bars, "swing_low"))

    # Mirror / repeated exact price areas.
    rounded_lows = daily_bars["low"].round(2).value_counts()
    rounded_highs = daily_bars["high"].round(2).value_counts()
    for price, count in rounded_lows.items():
        if int(count) >= 3:
            levels.append(_make_level(symbol, float(price), "limit_player", "daily", daily_bars, "repeated_lows"))
    for price, count in rounded_highs.items():
        if int(count) >= 3:
            levels.append(_make_level(symbol, float(price), "mirror", "daily", daily_bars, "repeated_highs"))

    # Abnormal candle levels.
    if avg_range > 0:
        abnormal = daily_bars[(daily_bars["high"] - daily_bars["low"]) >= (SETTINGS.strategy.abnormal_range_multiplier * avg_range)]
        for _, row in abnormal.iterrows():
            levels.append(_make_level(symbol, float(row["high"]), "abnormal_candle", "daily", daily_bars, "abnormal_high"))
            levels.append(_make_level(symbol, float(row["low"]), "abnormal_candle", "daily", daily_bars, "abnormal_low"))

    # Consolidation levels.
    if len(daily_bars) >= SETTINGS.strategy.consolidation_window:
        chunk = daily_bars.tail(SETTINGS.strategy.consolidation_window)
        levels.append(_make_level(symbol, float(chunk["high"].max()), "consolidation", "daily", daily_bars, "consolidation_high"))
        levels.append(_make_level(symbol, float(chunk["low"].min()), "consolidation", "daily", daily_bars, "consolidation_low"))

    # Gap levels.
    for idx in range(1, len(daily_bars)):
        prior_close = float(daily_bars.iloc[idx - 1]["close"])
        current_open = float(daily_bars.iloc[idx]["open"])
        if abs(current_open - prior_close) / prior_close >= 0.01:
            levels.append(_make_level(symbol, max(prior_close, current_open), "gap", "daily", daily_bars, "gap_upper"))
            levels.append(_make_level(symbol, min(prior_close, current_open), "gap", "daily", daily_bars, "gap_lower"))

    deduped = dedupe_levels(levels)
    enrich_nearest_levels(deduped)
    return deduped


def dedupe_levels(levels: List[Level]) -> List[Level]:
    unique: Dict[tuple[str, float, str], Level] = {}
    for level in levels:
        key = (level.symbol, round(level.price, 2), level.type)
        if key not in unique or unique[key].touches < level.touches:
            unique[key] = level
    return list(unique.values())


def enrich_nearest_levels(levels: List[Level]) -> None:
    by_symbol: Dict[str, List[Level]] = {}
    for level in levels:
        by_symbol.setdefault(level.symbol, []).append(level)
    for symbol_levels in by_symbol.values():
        ordered = sorted(symbol_levels, key=lambda item: item.price)
        for idx, level in enumerate(ordered):
            level.nearest_lower_level = ordered[idx - 1].price if idx > 0 else None
            level.nearest_upper_level = ordered[idx + 1].price if idx < len(ordered) - 1 else None


def levels_to_frame(levels: List[Level]) -> pd.DataFrame:
    return pd.DataFrame([level.to_dict() for level in levels])
