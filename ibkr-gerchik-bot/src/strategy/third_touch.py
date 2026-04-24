"""Third touch setup detection."""

from __future__ import annotations

from typing import Dict, List

import pandas as pd

from src.config import SETTINGS


def _touch_indices(series: pd.Series, level: float, tolerance: float) -> List[int]:
    indices: List[int] = []
    for idx, value in enumerate(series.tolist()):
        if abs(float(value) - level) <= tolerance:
            indices.append(idx)
    return indices


def detect_third_touch(bars: pd.DataFrame, level: float, direction: str) -> Dict[str, object]:
    """Detect the third qualified interaction with a level."""
    if len(bars) < 5:
        return {"signal": "NONE", "reason": "not_enough_bars"}

    tolerance = level * SETTINGS.strategy.level_tolerance_pct
    source = bars["low"] if direction == "long" else bars["high"]
    touches = _touch_indices(source, level, tolerance)

    if len(touches) < 3:
        return {"signal": "NONE", "reason": "fewer_than_three_touches"}

    last_three = touches[-3:]
    spacing_ok = all(
        (last_three[idx] - last_three[idx - 1]) >= SETTINGS.strategy.third_touch_min_spacing
        for idx in range(1, len(last_three))
    )

    current = bars.iloc[-1]
    entry = float(current["close"])
    if direction == "long":
        confirmation = float(current["close"]) > level
        stop_price = level - tolerance
        target = entry + (entry - stop_price) * SETTINGS.risk.min_reward_risk_ratio
        signal = "BUY" if spacing_ok and confirmation and last_three[-1] == len(bars) - 1 else "NONE"
    else:
        confirmation = float(current["close"]) < level
        stop_price = level + tolerance
        target = entry - (stop_price - entry) * SETTINGS.risk.min_reward_risk_ratio
        signal = "SELL" if spacing_ok and confirmation and last_three[-1] == len(bars) - 1 else "NONE"

    return {
        "signal": signal,
        "strategy": "third_touch",
        "direction": direction,
        "entry": round(entry, 2),
        "stop": round(stop_price, 2),
        "target": round(target, 2),
        "level": round(level, 2),
        "touches": last_three,
    }
