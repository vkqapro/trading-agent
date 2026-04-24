"""Complex 3+ bar false breakout strategy."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.config import SETTINGS
from src.strategy.levels import Level
from src.strategy.signal_models import TradeSignal


def detect_false_breakout_complex(symbol: str, bars: pd.DataFrame, level: Level) -> Optional[TradeSignal]:
    if len(bars) < 4:
        return None
    structure = bars.tail(4)
    tolerance = level.price * SETTINGS.strategy.level_tolerance_pct
    closes = structure["close"].tolist()
    highs = structure["high"].tolist()
    lows = structure["low"].tolist()
    if all(close < level.price for close in closes[:-1]) and closes[-1] > level.price and max(highs[:-1]) - min(lows[:-1]) < SETTINGS.strategy.abnormal_range_multiplier * float(structure["high"].sub(structure["low"]).mean()) and level.nearest_upper_level is not None:
        return TradeSignal(
            symbol=symbol,
            strategy="false_breakout_complex",
            signal="BUY",
            direction="long",
            entry=round(float(closes[-1]), 2),
            stop=round(min(lows) - tolerance, 2),
            target=round(level.nearest_upper_level, 2),
            level_price=level.price,
            level_type=level.type,
            nearest_upper_level=level.nearest_upper_level,
            nearest_lower_level=level.nearest_lower_level,
        )
    if all(close > level.price for close in closes[:-1]) and closes[-1] < level.price and max(highs[:-1]) - min(lows[:-1]) < SETTINGS.strategy.abnormal_range_multiplier * float(structure["high"].sub(structure["low"]).mean()) and level.nearest_lower_level is not None:
        return TradeSignal(
            symbol=symbol,
            strategy="false_breakout_complex",
            signal="SELL",
            direction="short",
            entry=round(float(closes[-1]), 2),
            stop=round(max(highs) + tolerance, 2),
            target=round(level.nearest_lower_level, 2),
            level_price=level.price,
            level_type=level.type,
            nearest_upper_level=level.nearest_upper_level,
            nearest_lower_level=level.nearest_lower_level,
        )
    return None
