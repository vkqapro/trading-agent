"""Two-bar false breakout strategy."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.config import SETTINGS
from src.strategy.levels import Level
from src.strategy.signal_models import TradeSignal


def detect_false_breakout_two_bar(symbol: str, bars: pd.DataFrame, level: Level) -> Optional[TradeSignal]:
    if len(bars) < 2:
        return None
    first = bars.iloc[-2]
    second = bars.iloc[-1]
    tolerance = level.price * SETTINGS.strategy.level_tolerance_pct
    if float(first["close"]) < level.price - tolerance and float(second["close"]) > level.price + tolerance and level.nearest_upper_level is not None:
        stop = min(float(first["low"]), float(second["low"])) - tolerance
        return TradeSignal(
            symbol=symbol,
            strategy="false_breakout_two_bar",
            signal="BUY",
            direction="long",
            entry=round(float(second["close"]), 2),
            stop=round(stop, 2),
            target=round(level.nearest_upper_level, 2),
            level_price=level.price,
            level_type=level.type,
            nearest_upper_level=level.nearest_upper_level,
            nearest_lower_level=level.nearest_lower_level,
        )
    if float(first["close"]) > level.price + tolerance and float(second["close"]) < level.price - tolerance and level.nearest_lower_level is not None:
        stop = max(float(first["high"]), float(second["high"])) + tolerance
        return TradeSignal(
            symbol=symbol,
            strategy="false_breakout_two_bar",
            signal="SELL",
            direction="short",
            entry=round(float(second["close"]), 2),
            stop=round(stop, 2),
            target=round(level.nearest_lower_level, 2),
            level_price=level.price,
            level_type=level.type,
            nearest_upper_level=level.nearest_upper_level,
            nearest_lower_level=level.nearest_lower_level,
        )
    return None
