"""Level strength scoring helpers."""

from __future__ import annotations

from typing import List

from src.config import LOGGER, SETTINGS
from src.strategy.levels import Level


def _fallback_score(level: Level) -> float:
    score = 0.0
    score += float(level.touches) * 1.0
    score += float(level.false_breakouts) * 2.0
    score += 2.0 if level.timeframe == "daily" else 1.0
    if level.type == "mirror" or "mirror" in level.families:
        score += 1.0
    if level.type == "abnormal_candle" or "abnormal_candle" in level.families:
        score += 1.0
    fractional = round(abs((level.center or level.price) - int(level.center or level.price)), 2)
    if fractional in {0.0, 0.5}:
        score += 0.75
    if level.created_by.startswith("repeated"):
        score += 0.75
    return round(score, 2)


def score_level(level: Level) -> float:
    """Return the precomputed strength score, falling back to the legacy field."""
    score = float(level.strength or level.strength_score or 0.0)
    if score <= 0:
        score = _fallback_score(level)
    level.strength = round(score, 2)
    level.strength_score = level.strength
    return level.strength


def apply_strength_scores(levels: List[Level]) -> List[Level]:
    for level in levels:
        score_level(level)
    return levels


def filter_strong_levels(levels: List[Level]) -> List[Level]:
    apply_strength_scores(levels)
    threshold = SETTINGS.strategy.level_strength_threshold
    strong_levels = [level for level in levels if level.strength_score >= threshold]
    rejected = len(levels) - len(strong_levels)
    if rejected:
        LOGGER.info("Filtered %s weak levels below strength threshold %.2f", rejected, threshold)
    return sorted(strong_levels, key=lambda level: level.strength_score, reverse=True)
