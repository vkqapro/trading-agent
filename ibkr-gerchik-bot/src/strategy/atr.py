"""ATR helpers for Gerchik-style trade filtering."""

from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd

from src.config import SETTINGS


def calculate_daily_atr(daily_bars: pd.DataFrame, lookback: int = 5) -> float:
    """Calculate ATR from the last 3-5 completed daily candles excluding abnormal tiny/huge bars."""
    sample = daily_bars.tail(max(lookback, 3)).copy()
    if sample.empty:
        return 0.0
    sample["range"] = sample["high"] - sample["low"]
    avg = float(sample["range"].mean()) if not sample.empty else 0.0
    if avg <= 0:
        return 0.0
    filtered = sample[(sample["range"] < 2 * avg) & (sample["range"] > avg / 3)]
    if filtered.empty:
        filtered = sample
    return round(float(filtered["range"].mean()), 4)


def calculate_technical_atr(current_price: float, lower_level: Optional[float], upper_level: Optional[float]) -> float:
    distances = [abs(current_price - level) for level in (lower_level, upper_level) if level is not None]
    return round(min(distances), 4) if distances else 0.0


def technical_atr_has_room(technical_atr: float, current_price: float) -> bool:
    if current_price <= 0:
        return False
    return (technical_atr / current_price) >= SETTINGS.strategy.minimum_technical_atr_pct


def atr_travel_filter(
    entry_price: float,
    session_low: float,
    session_high: float,
    daily_atr: float,
    is_new_extreme: bool,
) -> bool:
    """Reject trend entries if price already traveled 75% of ATR unless breaking to a new extreme."""
    if daily_atr <= 0:
        return True
    distance_traveled = max(abs(entry_price - session_low), abs(session_high - entry_price))
    if distance_traveled >= daily_atr * SETTINGS.strategy.atr_travel_limit_pct and not is_new_extreme:
        return False
    return True
