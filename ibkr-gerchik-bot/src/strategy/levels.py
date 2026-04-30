"""Level detection helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
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
    nearest_upper_level: Optional[float] = None
    nearest_lower_level: Optional[float] = None
    first_touch_date: Optional[str] = None
    zone_low: Optional[float] = None
    zone_high: Optional[float] = None

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


def _touch_indices_near_prices(bars: pd.DataFrame, prices: List[float], tolerance_pct: float) -> List[int]:
    indices: List[int] = []
    for idx, (_, row) in enumerate(bars.iterrows()):
        high = float(row["high"])
        low = float(row["low"])
        for price in prices:
            tolerance = max(price * tolerance_pct, 0.05)
            if abs(high - price) <= tolerance or abs(low - price) <= tolerance:
                indices.append(idx)
                break
    return indices


def _first_touch_date_near_price(bars: pd.DataFrame, price: float, tolerance_pct: float) -> Optional[str]:
    tolerance = max(price * tolerance_pct, 0.05)
    for _, row in bars.iterrows():
        if abs(float(row["high"]) - price) <= tolerance or abs(float(row["low"]) - price) <= tolerance:
            raw_date = row.get("date")
            return _format_touch_date(raw_date)
    return None


def _format_touch_date(raw_date: object) -> Optional[str]:
    if raw_date is None:
        return None
    if isinstance(raw_date, pd.Timestamp):
        return raw_date.strftime("%m/%d/%y")
    if isinstance(raw_date, datetime):
        return raw_date.strftime("%m/%d/%y")
    if isinstance(raw_date, date):
        return raw_date.strftime("%m/%d/%y")
    if isinstance(raw_date, str):
        cleaned = raw_date.strip()
        if not cleaned:
            return None
        try:
            parsed = pd.to_datetime(cleaned)
        except (ValueError, TypeError):
            return cleaned
        return parsed.strftime("%m/%d/%y")
    return str(raw_date)


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


def _false_breakout_indices_for_prices(bars: pd.DataFrame, prices: List[float], tolerance_pct: float) -> List[int]:
    indices: List[int] = []
    for idx, (_, row) in enumerate(bars.iterrows()):
        low = float(row["low"])
        high = float(row["high"])
        close = float(row["close"])
        for price in prices:
            tolerance = max(price * tolerance_pct, 0.05)
            if low < price - tolerance < close or high > price + tolerance > close:
                indices.append(idx)
                break
    return indices


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
        first_touch_date=_first_touch_date_near_price(bars, price, SETTINGS.strategy.level_tolerance_pct),
        zone_low=_round_price(price),
        zone_high=_round_price(price),
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
    merged = merge_nearby_levels(deduped, daily_bars)
    enrich_nearest_levels(merged)
    return merged


def dedupe_levels(levels: List[Level]) -> List[Level]:
    unique: Dict[tuple[str, float, str], Level] = {}
    for level in levels:
        key = (level.symbol, round(level.price, 2), level.type)
        if key not in unique or unique[key].touches < level.touches:
            unique[key] = level
    return list(unique.values())


def merge_nearby_levels(levels: List[Level], daily_bars: pd.DataFrame) -> List[Level]:
    by_symbol_family: Dict[tuple[str, str, str], List[Level]] = {}
    for level in levels:
        family = _level_family(level.type)
        by_symbol_family.setdefault((level.symbol, level.timeframe, family), []).append(level)

    merged: List[Level] = []
    for symbol_levels in by_symbol_family.values():
        ordered = sorted(symbol_levels, key=lambda item: item.price)
        clusters: List[List[Level]] = []

        for level in ordered:
            if clusters and _can_merge_into_cluster(clusters[-1], level):
                clusters[-1].append(level)
            else:
                clusters.append([level])

        for cluster in clusters:
            merged.append(_merge_cluster(cluster, daily_bars))

    return merged


def _can_merge_into_cluster(cluster: List[Level], candidate: Level) -> bool:
    if not cluster:
        return False
    if cluster[0].symbol != candidate.symbol or cluster[0].timeframe != candidate.timeframe:
        return False
    if not _compatible_level_types(cluster[0].type, candidate.type):
        return False

    cluster_low = min(level.price for level in cluster)
    cluster_high = max(level.price for level in cluster)
    representative = max(cluster, key=lambda level: (level.touches, level.false_breakouts, -abs(level.price - candidate.price)))
    tolerance = max(
        representative.price * SETTINGS.strategy.level_merge_tolerance_pct,
        SETTINGS.strategy.level_merge_min_dollars,
    )
    return candidate.price <= cluster_high + tolerance and candidate.price >= cluster_low - tolerance


def _compatible_level_types(left: str, right: str) -> bool:
    return _level_family(left) == _level_family(right)


def _level_family(level_type: str) -> str:
    if level_type == "gap":
        return "gap"
    return "structural"


def _level_type_priority(level: Level) -> int:
    priorities = {
        "historical": 4,
        "limit_player": 3,
        "mirror": 3,
        "abnormal_candle": 2,
        "consolidation": 1,
        "gap": 0,
    }
    return priorities.get(level.type, 0)


def _merge_cluster(cluster: List[Level], daily_bars: pd.DataFrame) -> Level:
    representative = max(
        cluster,
        key=lambda level: (
            level.touches,
            level.false_breakouts,
            _level_type_priority(level),
            level.price,
        ),
    )
    prices = [level.price for level in cluster]
    touch_indices = _touch_indices_near_prices(daily_bars, prices, SETTINGS.strategy.level_tolerance_pct)
    false_breakout_indices = _false_breakout_indices_for_prices(daily_bars, prices, SETTINGS.strategy.level_tolerance_pct)
    first_touch_date = None
    if touch_indices:
        raw_date = daily_bars.iloc[touch_indices[0]].get("date")
        first_touch_date = _format_touch_date(raw_date)

    merged_level = Level(
        symbol=representative.symbol,
        price=representative.price,
        type=representative.type,
        timeframe=representative.timeframe,
        touches=len(touch_indices),
        false_breakouts=len(false_breakout_indices),
        strength_score=representative.strength_score,
        created_by=representative.created_by,
        nearest_upper_level=None,
        nearest_lower_level=None,
        first_touch_date=first_touch_date,
        zone_low=_round_price(min(prices)),
        zone_high=_round_price(max(prices)),
    )
    return merged_level


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
