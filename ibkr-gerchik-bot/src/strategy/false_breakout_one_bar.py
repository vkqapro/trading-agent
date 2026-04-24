"""One-bar false breakout strategy."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.config import SETTINGS
from src.strategy.levels import Level
from src.strategy.signal_models import TradeSignal


def detect_false_breakout_one_bar(symbol: str, bars: pd.DataFrame, level: Level) -> Optional[TradeSignal]:
    if len(bars) < 2:
        return None
    prev_candle = bars.iloc[-2]
    candle = bars.iloc[-1]
    tolerance = level.price * SETTINGS.strategy.level_tolerance_pct
    if float(candle["low"]) < level.price - tolerance < float(candle["close"]) and float(candle["close"]) > float(prev_candle["low"]) and level.nearest_upper_level is not None:
        return TradeSignal(
            symbol=symbol,
            strategy="false_breakout_one_bar",
            signal="BUY",
            direction="long",
            entry=round(float(candle["close"]), 2),
            stop=round(float(candle["low"]) - tolerance, 2),
            target=round(level.nearest_upper_level, 2),
            level_price=level.price,
            level_type=level.type,
            nearest_upper_level=level.nearest_upper_level,
            nearest_lower_level=level.nearest_lower_level,
        )
    if float(candle["high"]) > level.price + tolerance > float(candle["close"]) and float(candle["close"]) < float(prev_candle["high"]) and level.nearest_lower_level is not None:
        return TradeSignal(
            symbol=symbol,
            strategy="false_breakout_one_bar",
            signal="SELL",
            direction="short",
            entry=round(float(candle["close"]), 2),
            stop=round(float(candle["high"]) + tolerance, 2),
            target=round(level.nearest_lower_level, 2),
            level_price=level.price,
            level_type=level.type,
            nearest_upper_level=level.nearest_upper_level,
            nearest_lower_level=level.nearest_lower_level,
        )
    return None
