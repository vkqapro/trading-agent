"""Level rebound detection."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from src.config import SETTINGS


def detect_rebound(bars: pd.DataFrame, level: float, direction: str) -> Dict[str, object]:
    """Detect price touching a level and closing away from it."""
    if bars.empty:
        return {"signal": "NONE", "reason": "not_enough_bars"}

    current = bars.iloc[-1]
    tolerance = level * SETTINGS.strategy.level_tolerance_pct
    entry = float(current["close"])

    if direction == "long":
        touched = float(current["low"]) <= level + tolerance
        closed_above = float(current["close"]) > level
        stop_price = float(current["low"]) - tolerance
        target = entry + (entry - stop_price) * SETTINGS.risk.min_reward_risk_ratio
        signal = "BUY" if touched and closed_above else "NONE"
    else:
        touched = float(current["high"]) >= level - tolerance
        closed_below = float(current["close"]) < level
        stop_price = float(current["high"]) + tolerance
        target = entry - (stop_price - entry) * SETTINGS.risk.min_reward_risk_ratio
        signal = "SELL" if touched and closed_below else "NONE"

    return {
        "signal": signal,
        "strategy": "rebound",
        "direction": direction,
        "entry": round(entry, 2),
        "stop": round(stop_price, 2),
        "target": round(target, 2),
        "level": round(level, 2),
    }
