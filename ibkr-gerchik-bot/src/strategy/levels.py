"""Price level detection helpers for Gerchik-style discretionary rules made deterministic."""

from __future__ import annotations

from typing import Dict, List

import pandas as pd

from src.config import SETTINGS


def _round_level(value: float) -> float:
    return round(float(value), 2)


def detect_support_resistance(bars: pd.DataFrame, window: int = 3) -> Dict[str, List[float]]:
    """Detect swing highs and lows as candidate support/resistance levels."""
    if bars.empty or len(bars) < (window * 2) + 1:
        return {"support": [], "resistance": []}

    support: List[float] = []
    resistance: List[float] = []

    for index in range(window, len(bars) - window):
        local_slice = bars.iloc[index - window : index + window + 1]
        low = float(bars.iloc[index]["low"])
        high = float(bars.iloc[index]["high"])
        if low == float(local_slice["low"].min()):
            support.append(_round_level(low))
        if high == float(local_slice["high"].max()):
            resistance.append(_round_level(high))

    return {
        "support": sorted(set(support)),
        "resistance": sorted(set(resistance)),
    }


def detect_daily_high_low(bars: pd.DataFrame) -> Dict[str, float]:
    """Return daily range boundaries from a bar set."""
    if bars.empty:
        return {"daily_high": 0.0, "daily_low": 0.0}
    return {
        "daily_high": _round_level(bars["high"].max()),
        "daily_low": _round_level(bars["low"].min()),
    }


def detect_consolidation_zones(bars: pd.DataFrame, window: int | None = None) -> List[Dict[str, float]]:
    """Detect tight trading ranges that can later break or reject."""
    window = window or SETTINGS.strategy.consolidation_window
    zones: List[Dict[str, float]] = []
    if bars.empty or len(bars) < window:
        return zones

    for start in range(0, len(bars) - window + 1):
        chunk = bars.iloc[start : start + window]
        high = float(chunk["high"].max())
        low = float(chunk["low"].min())
        midpoint = (high + low) / 2
        range_pct = (high - low) / midpoint if midpoint else 0.0
        if range_pct <= 0.02:
            zones.append(
                {
                    "start_index": float(start),
                    "end_index": float(start + window - 1),
                    "high": _round_level(high),
                    "low": _round_level(low),
                }
            )
    deduped: List[Dict[str, float]] = []
    seen = set()
    for zone in zones:
        key = (zone["high"], zone["low"])
        if key not in seen:
            seen.add(key)
            deduped.append(zone)
    return deduped


def build_level_map(bars: pd.DataFrame) -> Dict[str, object]:
    """Aggregate all supported level detections in one payload."""
    levels = detect_support_resistance(bars)
    levels.update(detect_daily_high_low(bars))
    levels["consolidation_zones"] = detect_consolidation_zones(bars)
    return levels
