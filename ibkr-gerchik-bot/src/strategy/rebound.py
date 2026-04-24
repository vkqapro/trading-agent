"""Rebound from level strategy."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.config import SETTINGS
from src.strategy.candles import is_bearish, is_bullish
from src.strategy.levels import Level
from src.strategy.signal_models import TradeSignal


def detect_rebound(symbol: str, bars: pd.DataFrame, level: Level) -> Optional[TradeSignal]:
    if bars.empty or len(bars) < 2:
        return None
    candle = bars.iloc[-1]
    tolerance = level.price * SETTINGS.strategy.level_tolerance_pct
    if level.price >= float(candle["close"]):
        touched = float(candle["high"]) >= level.price - tolerance
        rejected = float(candle["close"]) < level.price and is_bearish(candle)
        if touched and rejected and level.nearest_lower_level is not None:
            entry = round(float(candle["close"]) - tolerance, 2)
            stop = round(float(candle["high"]) + tolerance, 2)
            target = round(level.nearest_lower_level, 2)
            return TradeSignal(
                symbol=symbol,
                strategy="rebound",
                signal="SELL",
                direction="short",
                entry=entry,
                stop=stop,
                target=target,
                level_price=level.price,
                level_type=level.type,
                nearest_upper_level=level.nearest_upper_level,
                nearest_lower_level=level.nearest_lower_level,
            )
    else:
        touched = float(candle["low"]) <= level.price + tolerance
        rejected = float(candle["close"]) > level.price and is_bullish(candle)
        if touched and rejected and level.nearest_upper_level is not None:
            entry = round(float(candle["close"]) + tolerance, 2)
            stop = round(float(candle["low"]) - tolerance, 2)
            target = round(level.nearest_upper_level, 2)
            return TradeSignal(
                symbol=symbol,
                strategy="rebound",
                signal="BUY",
                direction="long",
                entry=entry,
                stop=stop,
                target=target,
                level_price=level.price,
                level_type=level.type,
                nearest_upper_level=level.nearest_upper_level,
                nearest_lower_level=level.nearest_lower_level,
            )
    return None
