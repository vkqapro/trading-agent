"""ATR-aware level detection and zone clustering helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Sequence

import pandas as pd

from src.config import LOGGER, SETTINGS

ZONE_BUFFER_MULTIPLIER = 0.12
MERGE_DISTANCE_MULTIPLIER = 0.25
ULTRA_CLOSE_DISTANCE_MULTIPLIER = 0.08
MAX_ZONE_WIDTH_ATR_MULTIPLIER = 0.5
RECENCY_WEIGHT_MAX = 2.0
MIN_TOUCHES = 2


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
    source_date: Optional[str] = None
    zone_low: Optional[float] = None
    zone_high: Optional[float] = None
    center: Optional[float] = None
    families: List[str] = field(default_factory=list)
    strength: float = 0.0
    atr_value: float = 0.0
    touch_indices: List[int] = field(default_factory=list, repr=False)
    false_breakout_indices: List[int] = field(default_factory=list, repr=False)
    last_touch_index: Optional[int] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.price = _round_price(self.price)
        self.zone_low = _round_price(self.zone_low if self.zone_low is not None else self.price)
        self.zone_high = _round_price(self.zone_high if self.zone_high is not None else self.price)
        self.center = _round_price(self.center if self.center is not None else (self.zone_low + self.zone_high) / 2)
        if not self.families:
            self.families = [_family_group(self.type), self.type]
        self.families = sorted({family for family in self.families if family})
        if not self.strength and self.strength_score:
            self.strength = float(self.strength_score)
        if not self.strength_score and self.strength:
            self.strength_score = round(float(self.strength), 2)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload.pop("touch_indices", None)
        payload.pop("false_breakout_indices", None)
        payload.pop("last_touch_index", None)
        return payload


def _round_price(price: float) -> float:
    return round(float(price), 2)


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


def calculate_atr(candles: pd.DataFrame, period: int = 5) -> float:
    """Calculate ATR from daily candles while excluding abnormal huge/tiny bars."""
    if candles.empty:
        return 0.0

    sample = candles.tail(max(period, 3)).copy()
    sample["range"] = sample["high"].astype(float) - sample["low"].astype(float)
    average_range = float(sample["range"].mean()) if not sample.empty else 0.0
    if average_range <= 0:
        return 0.0

    filtered = sample[
        (sample["range"] < 2.0 * average_range)
        & (sample["range"] > average_range / 3.0)
    ]
    if filtered.empty:
        filtered = sample

    atr_value = round(float(filtered["range"].mean()), 4)
    LOGGER.info("Level ATR calculation: period=%s atr=%.4f sample_size=%s filtered_size=%s", period, atr_value, len(sample), len(filtered))
    return atr_value


def _atr_distances(atr_value: float) -> tuple[float, float, float]:
    if atr_value <= 0:
        return 0.05, 0.10, 0.05
    zone_buffer = atr_value * SETTINGS.strategy.level_zone_buffer_atr_pct
    merge_distance = atr_value * SETTINGS.strategy.level_merge_distance_atr_pct
    ultra_close_distance = atr_value * SETTINGS.strategy.level_ultra_close_atr_pct
    return zone_buffer, merge_distance, ultra_close_distance


def _family_group(level_type: str) -> str:
    if level_type == "gap":
        return "gap"
    return "structural"


def _build_zone(price: float, zone_buffer: float) -> tuple[float, float, float]:
    zone_low = price - zone_buffer
    zone_high = price + zone_buffer
    center = (zone_low + zone_high) / 2.0
    return _round_price(zone_low), _round_price(zone_high), _round_price(center)


def _touch_indices_for_zone(bars: pd.DataFrame, zone_low: float, zone_high: float) -> List[int]:
    indices: List[int] = []
    for idx, (_, row) in enumerate(bars.iterrows()):
        high = float(row["high"])
        low = float(row["low"])
        if high >= zone_low and low <= zone_high:
            indices.append(idx)
    return indices


def _false_breakout_indices_for_zone(bars: pd.DataFrame, zone_low: float, zone_high: float) -> List[int]:
    indices: List[int] = []
    center = (zone_low + zone_high) / 2.0
    for idx, (_, row) in enumerate(bars.iterrows()):
        low = float(row["low"])
        high = float(row["high"])
        close = float(row["close"])
        if low < zone_low < close <= zone_high:
            indices.append(idx)
            continue
        if high > zone_high > close >= zone_low:
            indices.append(idx)
            continue
        if low < center < close and close <= zone_high:
            indices.append(idx)
            continue
        if high > center > close and close >= zone_low:
            indices.append(idx)
    return indices


def _first_touch_date_from_indices(bars: pd.DataFrame, indices: Sequence[int]) -> Optional[str]:
    if not indices:
        return None
    raw_date = bars.iloc[min(indices)].get("date")
    return _format_touch_date(raw_date)


def _source_date_from_row(row: pd.Series) -> Optional[str]:
    return _format_touch_date(row.get("date"))


def _source_date_from_repeated_prices(bars: pd.DataFrame, column: str, price: float) -> Optional[str]:
    matches = bars[bars[column].round(2) == round(price, 2)]
    if matches.empty:
        return None
    return _format_touch_date(matches.iloc[0].get("date"))


def _round_number_bonus(price: float) -> float:
    fractional = round(abs(price - int(price)), 2)
    if fractional in {0.0, 0.5}:
        return 0.75
    return 0.0


def _timeframe_weight(timeframe: str) -> float:
    return 2.0 if timeframe == "daily" else 1.0


def _recency_weight(last_touch_index: Optional[int], total_bars: int) -> float:
    if last_touch_index is None or total_bars <= 1:
        return 0.0
    bars_ago = max(total_bars - 1 - last_touch_index, 0)
    ratio = 1.0 - min(bars_ago / max(total_bars - 1, 1), 1.0)
    return round(ratio * RECENCY_WEIGHT_MAX, 2)


def _score_level(level: Level, total_bars: int) -> float:
    score = (
        float(level.touches) * 1.0
        + float(level.false_breakouts) * 2.0
        + _timeframe_weight(level.timeframe)
        + _recency_weight(level.last_touch_index, total_bars)
    )
    if "mirror" in level.families or level.type == "mirror":
        score += 1.0
    if level.type == "abnormal_candle" or "abnormal_candle" in level.families:
        score += 1.0
    score += _round_number_bonus(level.center or level.price)
    return round(score, 2)


def _touches_near_price(bars: pd.DataFrame, price: float, tolerance_pct: float) -> int:
    tolerance = max(price * tolerance_pct, 0.05)
    touches = 0
    for _, row in bars.iterrows():
        if abs(float(row["high"]) - price) <= tolerance or abs(float(row["low"]) - price) <= tolerance:
            touches += 1
    return touches


def _first_touch_date_near_price(bars: pd.DataFrame, price: float, tolerance_pct: float) -> Optional[str]:
    tolerance = max(price * tolerance_pct, 0.05)
    for _, row in bars.iterrows():
        if abs(float(row["high"]) - price) <= tolerance or abs(float(row["low"]) - price) <= tolerance:
            return _format_touch_date(row.get("date"))
    return None


def _raw_level(
    symbol: str,
    price: float,
    level_type: str,
    timeframe: str,
    bars: pd.DataFrame,
    zone_buffer: float,
    created_by: str,
) -> Level:
    zone_low, zone_high, center = _build_zone(price, zone_buffer)
    touch_indices = _touch_indices_for_zone(bars, zone_low, zone_high)
    false_breakout_indices = _false_breakout_indices_for_zone(bars, zone_low, zone_high)
    level = Level(
        symbol=symbol,
        price=price,
        type=level_type,
        timeframe=timeframe,
        touches=len(touch_indices),
        false_breakouts=len(false_breakout_indices),
        strength_score=0.0,
        created_by=created_by,
        first_touch_date=_first_touch_date_from_indices(bars, touch_indices),
        source_date=None,
        zone_low=zone_low,
        zone_high=zone_high,
        center=center,
        families=[_family_group(level_type), level_type],
        atr_value=zone_buffer / SETTINGS.strategy.level_zone_buffer_atr_pct
        if zone_buffer > 0 and SETTINGS.strategy.level_zone_buffer_atr_pct > 0
        else 0.0,
        touch_indices=touch_indices,
        false_breakout_indices=false_breakout_indices,
        last_touch_index=max(touch_indices) if touch_indices else None,
    )
    level.strength = _score_level(level, len(bars))
    level.strength_score = level.strength
    return level


def detect_levels(symbol: str, daily_bars: pd.DataFrame, intraday_bars: pd.DataFrame) -> List[Level]:
    """Detect ATR-aware Gerchik-style daily levels and merge them into zones."""
    del intraday_bars  # Daily bars are the authoritative source for level creation.
    if daily_bars.empty:
        return []

    atr_value = calculate_atr(daily_bars)
    zone_buffer, merge_distance, ultra_close_distance = _atr_distances(atr_value)
    LOGGER.info(
        "Level detection for %s: atr=%.4f zone_buffer=%.4f merge_distance=%.4f ultra_close=%.4f",
        symbol,
        atr_value,
        zone_buffer,
        merge_distance,
        ultra_close_distance,
    )

    levels: List[Level] = []
    avg_range = float((daily_bars["high"].astype(float) - daily_bars["low"].astype(float)).mean()) if not daily_bars.empty else 0.0

    for idx in range(1, len(daily_bars) - 1):
        prev_high = float(daily_bars.iloc[idx - 1]["high"])
        current_high = float(daily_bars.iloc[idx]["high"])
        next_high = float(daily_bars.iloc[idx + 1]["high"])
        prev_low = float(daily_bars.iloc[idx - 1]["low"])
        current_low = float(daily_bars.iloc[idx]["low"])
        next_low = float(daily_bars.iloc[idx + 1]["low"])
        if current_high >= prev_high and current_high >= next_high:
            level = _raw_level(symbol, current_high, "historical", "daily", daily_bars, zone_buffer, "swing_high")
            level.source_date = _source_date_from_row(daily_bars.iloc[idx])
            levels.append(level)
        if current_low <= prev_low and current_low <= next_low:
            level = _raw_level(symbol, current_low, "historical", "daily", daily_bars, zone_buffer, "swing_low")
            level.source_date = _source_date_from_row(daily_bars.iloc[idx])
            levels.append(level)

    rounded_lows = daily_bars["low"].round(2).value_counts()
    rounded_highs = daily_bars["high"].round(2).value_counts()
    for price, count in rounded_lows.items():
        if int(count) >= 3:
            level = _raw_level(symbol, float(price), "limit_player", "daily", daily_bars, zone_buffer, "repeated_lows")
            level.source_date = _source_date_from_repeated_prices(daily_bars, "low", float(price))
            levels.append(level)
    for price, count in rounded_highs.items():
        if int(count) >= 3:
            level = _raw_level(symbol, float(price), "mirror", "daily", daily_bars, zone_buffer, "repeated_highs")
            level.source_date = _source_date_from_repeated_prices(daily_bars, "high", float(price))
            levels.append(level)

    if avg_range > 0:
        abnormal = daily_bars[(daily_bars["high"].astype(float) - daily_bars["low"].astype(float)) >= (SETTINGS.strategy.abnormal_range_multiplier * avg_range)]
        for _, row in abnormal.iterrows():
            high_level = _raw_level(symbol, float(row["high"]), "abnormal_candle", "daily", daily_bars, zone_buffer, "abnormal_high")
            low_level = _raw_level(symbol, float(row["low"]), "abnormal_candle", "daily", daily_bars, zone_buffer, "abnormal_low")
            high_level.source_date = _source_date_from_row(row)
            low_level.source_date = _source_date_from_row(row)
            levels.append(high_level)
            levels.append(low_level)

    if len(daily_bars) >= SETTINGS.strategy.consolidation_window:
        chunk = daily_bars.tail(SETTINGS.strategy.consolidation_window)
        high_level = _raw_level(symbol, float(chunk["high"].max()), "consolidation", "daily", daily_bars, zone_buffer, "consolidation_high")
        low_level = _raw_level(symbol, float(chunk["low"].min()), "consolidation", "daily", daily_bars, zone_buffer, "consolidation_low")
        high_level.source_date = _source_date_from_row(chunk.iloc[-1])
        low_level.source_date = _source_date_from_row(chunk.iloc[-1])
        levels.append(high_level)
        levels.append(low_level)

    for idx in range(1, len(daily_bars)):
        prior_close = float(daily_bars.iloc[idx - 1]["close"])
        current_open = float(daily_bars.iloc[idx]["open"])
        if prior_close > 0 and abs(current_open - prior_close) / prior_close >= 0.01:
            upper = _raw_level(symbol, max(prior_close, current_open), "gap", "daily", daily_bars, zone_buffer, "gap_upper")
            lower = _raw_level(symbol, min(prior_close, current_open), "gap", "daily", daily_bars, zone_buffer, "gap_lower")
            upper.source_date = _source_date_from_row(daily_bars.iloc[idx])
            lower.source_date = _source_date_from_row(daily_bars.iloc[idx])
            levels.append(upper)
            levels.append(lower)

    deduped = dedupe_levels(levels)
    merged = merge_nearby_levels(
        deduped,
        daily_bars,
        atr_value=atr_value,
        merge_distance=merge_distance,
        ultra_close_distance=ultra_close_distance,
        zone_buffer=zone_buffer,
    )
    filtered = filter_weak_levels(merged, daily_bars, atr_value, merge_distance)
    enrich_nearest_levels(filtered)
    return sorted(filtered, key=lambda level: level.strength_score, reverse=True)


def dedupe_levels(levels: List[Level]) -> List[Level]:
    unique: Dict[tuple[str, float, str, str], Level] = {}
    for level in levels:
        key = (level.symbol, round(level.price, 2), level.type, level.timeframe)
        current = unique.get(key)
        if current is None or level.strength_score > current.strength_score:
            unique[key] = level
    return list(unique.values())


def merge_nearby_levels(
    levels: List[Level],
    daily_bars: pd.DataFrame,
    *,
    atr_value: Optional[float] = None,
    merge_distance: Optional[float] = None,
    ultra_close_distance: Optional[float] = None,
    zone_buffer: Optional[float] = None,
) -> List[Level]:
    if not levels:
        return []

    resolved_atr = atr_value if atr_value is not None else calculate_atr(daily_bars)
    resolved_zone_buffer, default_merge_distance, default_ultra_close = _atr_distances(resolved_atr)
    resolved_zone_buffer = zone_buffer if zone_buffer is not None else resolved_zone_buffer
    resolved_merge_distance = merge_distance if merge_distance is not None else default_merge_distance
    resolved_ultra_close = ultra_close_distance if ultra_close_distance is not None else default_ultra_close
    max_zone_width = resolved_atr * SETTINGS.strategy.max_level_zone_width_atr_pct if resolved_atr > 0 else None

    by_symbol_timeframe: Dict[tuple[str, str], List[Level]] = {}
    for level in levels:
        by_symbol_timeframe.setdefault((level.symbol, level.timeframe), []).append(level)

    merged_levels: List[Level] = []
    for (_, _), symbol_levels in by_symbol_timeframe.items():
        clusters = _cluster_levels(symbol_levels, resolved_merge_distance, resolved_ultra_close, max_zone_width=max_zone_width)
        for cluster in clusters:
            merged_levels.append(_merge_cluster(cluster, daily_bars, resolved_atr, resolved_zone_buffer))
    return merged_levels


def _cluster_levels(
    levels: List[Level],
    merge_distance: float,
    ultra_close_distance: float,
    *,
    max_zone_width: Optional[float] = None,
) -> List[List[Level]]:
    if not levels:
        return []

    ordered = sorted(levels, key=lambda level: level.price)
    clusters: List[List[Level]] = []
    current_cluster: List[Level] = [ordered[0]]
    current_low = ordered[0].zone_low if ordered[0].zone_low is not None else ordered[0].price
    current_high = ordered[0].zone_high if ordered[0].zone_high is not None else ordered[0].price

    for candidate in ordered[1:]:
        tail = current_cluster[-1]
        if not _should_merge(tail, candidate, merge_distance, ultra_close_distance):
            clusters.append(current_cluster)
            current_cluster = [candidate]
            current_low = candidate.zone_low if candidate.zone_low is not None else candidate.price
            current_high = candidate.zone_high if candidate.zone_high is not None else candidate.price
            continue

        candidate_low = candidate.zone_low if candidate.zone_low is not None else candidate.price
        candidate_high = candidate.zone_high if candidate.zone_high is not None else candidate.price
        prospective_low = min(current_low, candidate_low)
        prospective_high = max(current_high, candidate_high)
        prospective_width = prospective_high - prospective_low

        if max_zone_width is not None and prospective_width > max_zone_width:
            LOGGER.info(
                "Split merge cluster before %s %.2f: prospective_zone_width=%.4f > %.4f",
                candidate.type,
                candidate.price,
                prospective_width,
                max_zone_width,
            )
            clusters.append(current_cluster)
            current_cluster = [candidate]
            current_low = candidate_low
            current_high = candidate_high
            continue

        current_cluster.append(candidate)
        current_low = prospective_low
        current_high = prospective_high

    clusters.append(current_cluster)
    return clusters


def _should_merge(left: Level, right: Level, merge_distance: float, ultra_close_distance: float) -> bool:
    if left.symbol != right.symbol or left.timeframe != right.timeframe:
        return False

    distance = abs(left.price - right.price)
    same_family = bool(set(left.families) & set(right.families) & {"structural", "gap"})

    if distance <= ultra_close_distance:
        LOGGER.info("Merged level %s %.2f + %s %.2f (ultra-close %.4f)", left.type, left.price, right.type, right.price, distance)
        return True
    if distance <= merge_distance and same_family:
        LOGGER.info("Merged level %s %.2f + %s %.2f (same family distance %.4f)", left.type, left.price, right.type, right.price, distance)
        return True
    return False


def _merge_cluster(cluster: List[Level], daily_bars: pd.DataFrame, atr_value: float, zone_buffer: float) -> Level:
    weighted_touch_total = sum(max(level.touches, 1) for level in cluster)
    weighted_center = sum((level.center or level.price) * max(level.touches, 1) for level in cluster) / max(weighted_touch_total, 1)
    zone_low = min(level.zone_low if level.zone_low is not None else level.price for level in cluster)
    zone_high = max(level.zone_high if level.zone_high is not None else level.price for level in cluster)
    touch_indices = sorted({index for level in cluster for index in level.touch_indices})
    false_breakout_indices = sorted({index for level in cluster for index in level.false_breakout_indices})
    families = sorted({family for level in cluster for family in level.families})
    representative = max(
        cluster,
        key=lambda level: (
            level.strength_score,
            level.touches,
            level.false_breakouts,
            level.price,
        ),
    )

    merged = Level(
        symbol=representative.symbol,
        price=representative.price,
        type=representative.type,
        timeframe=representative.timeframe,
        touches=len(touch_indices),
        false_breakouts=len(false_breakout_indices),
        strength_score=0.0,
        created_by=representative.created_by,
        first_touch_date=_first_touch_date_from_indices(daily_bars, touch_indices),
        source_date=representative.source_date,
        zone_low=_round_price(zone_low),
        zone_high=_round_price(zone_high),
        center=_round_price(weighted_center),
        families=families,
        atr_value=atr_value,
        touch_indices=touch_indices,
        false_breakout_indices=false_breakout_indices,
        last_touch_index=max(touch_indices) if touch_indices else None,
    )
    merged.strength = _score_level(merged, len(daily_bars))
    merged.strength_score = merged.strength
    return merged


def filter_weak_levels(levels: List[Level], daily_bars: pd.DataFrame, atr_value: float, merge_distance: float) -> List[Level]:
    if not levels:
        return []

    kept: List[Level] = []
    for level in sorted(levels, key=lambda item: item.strength_score, reverse=True):
        zone_width = (level.zone_high or level.price) - (level.zone_low or level.price)
        if level.touches < MIN_TOUCHES:
            LOGGER.info("Rejected level %s %.2f: touches=%s < %s", level.type, level.price, level.touches, MIN_TOUCHES)
            continue
        max_zone_width = atr_value * SETTINGS.strategy.max_level_zone_width_atr_pct
        if atr_value > 0 and zone_width > max_zone_width:
            LOGGER.info("Rejected level %s %.2f: zone_width=%.4f > %.4f", level.type, level.price, zone_width, max_zone_width)
            continue
        if _is_floating(level, daily_bars):
            LOGGER.info("Rejected level %s %.2f: floating/no clear clustering", level.type, level.price)
            continue
        if _too_close_to_stronger(level, kept, merge_distance):
            LOGGER.info("Rejected level %s %.2f: too close to stronger level", level.type, level.price)
            continue
        kept.append(level)
    return kept


def _clean_gap_between_levels(lower: Level, upper: Level) -> float:
    lower_zone_high = lower.zone_high if lower.zone_high is not None else lower.price
    upper_zone_low = upper.zone_low if upper.zone_low is not None else upper.price
    return max(float(upper_zone_low) - float(lower_zone_high), 0.0)


def _clean_gap_atr_pct_between_levels(left: Level, right: Level, daily_atr: float) -> Optional[float]:
    if daily_atr <= 0:
        return None
    lower, upper = sorted([left, right], key=lambda item: item.center or item.price)
    return _clean_gap_between_levels(lower, upper) / daily_atr


def _trade_level_priority(level: Level) -> tuple[int, float, int, float]:
    return (
        int(level.touches),
        float(level.strength_score or level.strength or 0.0),
        int(level.false_breakouts),
        float(level.center or level.price),
    )


def optimize_trade_levels(
    levels: List[Level],
    daily_atr: float,
    *,
    min_gap_atr_pct: Optional[float] = None,
    min_touches: Optional[int] = None,
) -> List[Level]:
    """Return the tradable level set after enforcing clean ATR spacing.

    Raw detected levels can be close together because they represent evidence.
    This optimized set is what entry strategies should use, so adjacent chosen
    levels must have enough clean room between their zones.
    """
    if not levels:
        return []

    minimum_gap = SETTINGS.strategy.min_clean_level_gap_atr_pct if min_gap_atr_pct is None else float(min_gap_atr_pct)
    minimum_touches = SETTINGS.strategy.min_trade_level_touches if min_touches is None else int(min_touches)
    eligible = [level for level in levels if int(level.touches) >= minimum_touches]
    if not eligible:
        LOGGER.info("Optimized trade levels: no levels met min_touches=%s", minimum_touches)
        return []
    if daily_atr <= 0 or minimum_gap <= 0:
        optimized = sorted(eligible, key=lambda level: level.strength_score, reverse=True)
        enrich_nearest_levels(optimized)
        return optimized

    selected: List[Level] = []
    for candidate in sorted(eligible, key=_trade_level_priority, reverse=True):
        too_close_to_selected = False
        for selected_level in selected:
            clean_gap_atr_pct = _clean_gap_atr_pct_between_levels(candidate, selected_level, daily_atr)
            if clean_gap_atr_pct is None or clean_gap_atr_pct < minimum_gap:
                too_close_to_selected = True
                LOGGER.info(
                    "Excluded trade level %s %.2f: clean_gap_atr=%.4f < %.4f near selected %.2f",
                    candidate.type,
                    candidate.price,
                    clean_gap_atr_pct or 0.0,
                    minimum_gap,
                    selected_level.price,
                )
                break
        if not too_close_to_selected:
            selected.append(candidate)

    optimized = sorted(selected, key=lambda level: level.strength_score, reverse=True)
    enrich_nearest_levels(optimized)
    LOGGER.info(
        "Optimized trade levels: raw=%s eligible=%s optimized=%s min_gap_atr=%.4f min_touches=%s",
        len(levels),
        len(eligible),
        len(optimized),
        minimum_gap,
        minimum_touches,
    )
    return optimized


def _is_floating(level: Level, daily_bars: pd.DataFrame) -> bool:
    if level.touches >= 3:
        return False
    if level.false_breakouts > 0:
        return False
    if len(level.touch_indices) < 2:
        return True
    touch_span = max(level.touch_indices) - min(level.touch_indices)
    return touch_span >= max(int(len(daily_bars) * 0.6), 2)


def _zones_overlap(left: Level, right: Level) -> bool:
    left_low = left.zone_low if left.zone_low is not None else left.price
    left_high = left.zone_high if left.zone_high is not None else left.price
    right_low = right.zone_low if right.zone_low is not None else right.price
    right_high = right.zone_high if right.zone_high is not None else right.price
    return not (left_high < right_low or right_high < left_low)


def _too_close_to_stronger(level: Level, stronger_levels: Sequence[Level], merge_distance: float) -> bool:
    for stronger in stronger_levels:
        if stronger.symbol != level.symbol or stronger.timeframe != level.timeframe:
            continue
        if _zones_overlap(level, stronger):
            return True
        if abs((level.center or level.price) - (stronger.center or stronger.price)) <= merge_distance:
            return True
    return False


def enrich_nearest_levels(levels: List[Level]) -> None:
    by_symbol: Dict[str, List[Level]] = {}
    for level in levels:
        by_symbol.setdefault(level.symbol, []).append(level)

    for symbol_levels in by_symbol.values():
        ordered = sorted(symbol_levels, key=lambda item: item.center or item.price)
        for idx, level in enumerate(ordered):
            lower = ordered[idx - 1] if idx > 0 else None
            upper = ordered[idx + 1] if idx < len(ordered) - 1 else None
            level.nearest_lower_level = lower.center if lower is not None else None
            level.nearest_upper_level = upper.center if upper is not None else None


def levels_to_frame(levels: List[Level]) -> pd.DataFrame:
    return pd.DataFrame([level.to_dict() for level in levels])
