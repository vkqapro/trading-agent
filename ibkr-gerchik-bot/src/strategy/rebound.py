"""Level rebound detection."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from src.config import SETTINGS


def detect_rebound(bars: pd.DataFrame, level: float, direction: str) -> Dict[str, object]:
    """Detect price touching a level and closing away from it."""
    if bars.empty:
        return {"signal": False, "reason": "not_enough_bars"}

    current = bars.iloc[-1]
    tolerance = level * SETTINGS.strategy.level_tolerance_pct
    entry = float(current["close"])

    if direction == "long":
        touched = float(current["low"]) <= level + tolerance
        closed_above = float(current["close"]) > level
        stop_loss = float(current["low"]) - tolerance
        target = entry + (entry - stop_loss) * SETTINGS.risk.min_reward_risk_ratio
        signal = touched and closed_above
    else:
        touched = float(current["high"]) >= level - tolerance
        closed_below = float(current["close"]) < level
        stop_loss = float(current["high"]) + tolerance
        target = entry - (stop_loss - entry) * SETTINGS.risk.min_reward_risk_ratio
        signal = touched and closed_below

    return {
        "signal": signal,
        "strategy": "rebound",
        "direction": direction,
        "entry": round(entry, 2),
        "stop_loss": round(stop_loss, 2),
        "target": round(target, 2),
        "level": round(level, 2),
    }
