"""Breakout of level strategy."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.config import SETTINGS
from src.strategy.candles import full_range, has_compression
from src.strategy.levels import Level
from src.strategy.signal_models import TradeSignal


def detect_breakout(symbol: str, bars: pd.DataFrame, level: Level) -> Optional[TradeSignal]:
    if len(bars) < 4:
        return None
    recent = bars.tail(4)
    breakout_candle = recent.iloc[-1]
    setup_bars = recent.iloc[:-1]
    tolerance = level.price * SETTINGS.strategy.level_tolerance_pct
    setup_avg_range = float((setup_bars["high"] - setup_bars["low"]).mean()) if not setup_bars.empty else 0.0
    breakout_too_large = bool(setup_avg_range and full_range(breakout_candle) >= (SETTINGS.strategy.abnormal_range_multiplier + 2.5) * setup_avg_range)
    if not has_compression(setup_bars, minimum_count=3) or breakout_too_large:
        return None

    if float(breakout_candle["close"]) > level.price + tolerance and level.nearest_upper_level is not None:
        return TradeSignal(
            symbol=symbol,
            strategy="breakout",
            signal="BUY",
            direction="long",
            entry=round(float(breakout_candle["close"]), 2),
            stop=round(level.price - tolerance, 2),
            target=round(level.nearest_upper_level, 2),
            level_price=level.price,
            level_type=level.type,
            nearest_upper_level=level.nearest_upper_level,
            nearest_lower_level=level.nearest_lower_level,
        )
    if float(breakout_candle["close"]) < level.price - tolerance and level.nearest_lower_level is not None:
        return TradeSignal(
            symbol=symbol,
            strategy="breakout",
            signal="SELL",
            direction="short",
            entry=round(float(breakout_candle["close"]), 2),
            stop=round(level.price + tolerance, 2),
            target=round(level.nearest_lower_level, 2),
            level_price=level.price,
            level_type=level.type,
            nearest_upper_level=level.nearest_upper_level,
            nearest_lower_level=level.nearest_lower_level,
        )
    return None
