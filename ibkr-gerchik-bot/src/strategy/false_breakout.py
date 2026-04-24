"""False breakout detection."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from src.config import SETTINGS


def detect_false_breakout(bars: pd.DataFrame, level: float, direction: str) -> Dict[str, object]:
    """Detect a level breach followed by immediate rejection."""
    if len(bars) < 2:
        return {"signal": "NONE", "reason": "not_enough_bars"}

    previous = bars.iloc[-2]
    current = bars.iloc[-1]
    tolerance = level * SETTINGS.strategy.level_tolerance_pct

    if direction == "short":
        broke = float(previous["high"]) > level + tolerance
        rejected = float(current["close"]) < level and float(current["high"]) >= level
        stop_price = max(float(previous["high"]), float(current["high"])) + tolerance
        entry = float(current["close"])
        target = entry - (stop_price - entry) * SETTINGS.risk.min_reward_risk_ratio
        signal = "SELL" if broke and rejected else "NONE"
    else:
        broke = float(previous["low"]) < level - tolerance
        rejected = float(current["close"]) > level and float(current["low"]) <= level
        stop_price = min(float(previous["low"]), float(current["low"])) - tolerance
        entry = float(current["close"])
        target = entry + (entry - stop_price) * SETTINGS.risk.min_reward_risk_ratio
        signal = "BUY" if broke and rejected else "NONE"

    return {
        "signal": signal,
        "strategy": "false_breakout",
        "direction": direction,
        "entry": round(entry, 2),
        "stop": round(stop_price, 2),
        "target": round(target, 2),
        "level": round(level, 2),
    }
