"""False breakout detection — base module, intentionally disabled.

This module contains only a two-bar heuristic with no ATR filtering, no zone
awareness, and no confirmation candle requirement.  It must NOT be called
directly; use the subclass modules instead:
  - false_breakout_one_bar.detect_false_breakout_one_bar
  - false_breakout_two_bar.detect_false_breakout_two_bar
  - false_breakout_complex.detect_false_breakout_complex
"""

from __future__ import annotations

from typing import Dict

import pandas as pd

from src.config import SETTINGS

_DISABLED = True


def detect_false_breakout(bars: pd.DataFrame, level: float, direction: str) -> Dict[str, object]:
    """Detect a level breach followed by immediate rejection.

    .. deprecated::
        This function is disabled.  It uses only two bars with no ATR
        filtering, no zone awareness, and no confirmation candle — which
        produces unfiltered entries that violate Gerchik methodology.
        Call one of the subclass variants instead.
    """
    raise NotImplementedError(
        "detect_false_breakout (base) is disabled.  "
        "Use detect_false_breakout_one_bar, detect_false_breakout_two_bar, "
        "or detect_false_breakout_complex instead."
    )
    if len(bars) < 2:  # unreachable — kept so callers surface the error
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
