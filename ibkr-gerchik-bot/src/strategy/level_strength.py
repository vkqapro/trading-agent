"""Level strength scoring logic."""

from __future__ import annotations

from typing import List

from src.config import SETTINGS
from src.strategy.levels import Level


def score_level(level: Level) -> float:
    score = 0.0
    if level.timeframe == "daily":
        score += 2.0
    score += min(level.touches, 5) * 0.7
    score += min(level.false_breakouts, 3) * 0.8
    if level.type in {"historical", "mirror", "limit_player"}:
        score += 1.5
    fractional = round(level.price - int(level.price), 2)
    if fractional in {0.0, 0.25, 0.5, 0.75}:
        score += 0.75
    if level.created_by.startswith("repeated"):
        score += 0.75
    if level.type == "consolidation" and level.touches < 2:
        score -= 1.0
    return round(score, 2)


def apply_strength_scores(levels: List[Level]) -> List[Level]:
    for level in levels:
        level.strength_score = score_level(level)
    return levels


def filter_strong_levels(levels: List[Level]) -> List[Level]:
    apply_strength_scores(levels)
    return [level for level in levels if level.strength_score >= SETTINGS.strategy.level_strength_threshold]
