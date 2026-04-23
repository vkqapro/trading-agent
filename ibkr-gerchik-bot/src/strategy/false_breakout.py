"""False breakout detection."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from src.config import SETTINGS


def detect_false_breakout(bars: pd.DataFrame, level: float, direction: str) -> Dict[str, object]:
    """Detect a level breach followed by immediate rejection."""
    if len(bars) < 2:
        return {"signal": False, "reason": "not_enough_bars"}

    previous = bars.iloc[-2]
    current = bars.iloc[-1]
    tolerance = level * SETTINGS.strategy.level_tolerance_pct

    if direction == "short":
        broke = float(previous["high"]) > level + tolerance
        rejected = float(current["close"]) < level and float(current["high"]) >= level
        stop_loss = max(float(previous["high"]), float(current["high"])) + tolerance
        entry = float(current["close"])
        target = entry - (stop_loss - entry) * SETTINGS.risk.min_reward_risk_ratio
    else:
        broke = float(previous["low"]) < level - tolerance
        rejected = float(current["close"]) > level and float(current["low"]) <= level
        stop_loss = min(float(previous["low"]), float(current["low"])) - tolerance
        entry = float(current["close"])
        target = entry + (entry - stop_loss) * SETTINGS.risk.min_reward_risk_ratio

    signal = broke and rejected
    return {
        "signal": signal,
        "strategy": "false_breakout",
        "direction": direction,
        "entry": round(entry, 2),
        "stop_loss": round(stop_loss, 2),
        "target": round(target, 2),
        "level": round(level, 2),
    }
