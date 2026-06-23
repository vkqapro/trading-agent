"""Take-profit helpers."""

from __future__ import annotations

from typing import Dict, List, Optional

from src.config import SETTINGS


def calculate_take_profit(
    entry: float,
    stop: float,
    next_level: Optional[float],
    direction: str,
    *,
    allow_fallback: bool = False,
) -> Optional[float]:
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    if next_level is None:
        if not allow_fallback:
            return None
        fallback = entry + (SETTINGS.risk.min_reward_risk_ratio * risk if direction == "long" else -SETTINGS.risk.min_reward_risk_ratio * risk)
        return round(fallback, 2)
    if not isinstance(next_level, (float, int)):
        return None
    if direction == "long" and next_level <= entry:
        return None
    if direction == "short" and next_level >= entry:
        return None
    reward_risk = reward_risk_ratio(entry, stop, next_level)
    if reward_risk < SETTINGS.risk.min_reward_risk_ratio:
        return None
    return round(next_level, 2)


def reward_risk_ratio(entry: float, stop: float, target: float) -> float:
    risk = abs(entry - stop)
    reward = abs(target - entry)
    return 0.0 if risk <= 0 else reward / risk


def build_partial_targets(entry: float, stop: float, target: float, direction: str) -> List[Dict[str, float]]:
    one_r = abs(entry - stop)
    two_r = entry + (2 * one_r if direction == "long" else -2 * one_r)
    return [
        {"qty_pct": 0.5, "price": round(two_r, 2)},
        {"qty_pct": 0.5, "price": round(max(target, entry + 3 * one_r) if direction == "long" else min(target, entry - 3 * one_r), 2)},
    ]
