"""Candle utilities used across Gerchik-style setups."""

from __future__ import annotations

from typing import Iterable

import pandas as pd

from src.config import SETTINGS


def full_range(candle: pd.Series) -> float:
    return float(candle["high"]) - float(candle["low"])


def body_size(candle: pd.Series) -> float:
    return abs(float(candle["close"]) - float(candle["open"]))


def upper_wick(candle: pd.Series) -> float:
    return float(candle["high"]) - max(float(candle["open"]), float(candle["close"]))


def lower_wick(candle: pd.Series) -> float:
    return min(float(candle["open"]), float(candle["close"])) - float(candle["low"])


def is_bullish(candle: pd.Series) -> bool:
    return float(candle["close"]) > float(candle["open"])


def is_bearish(candle: pd.Series) -> bool:
    return float(candle["close"]) < float(candle["open"])


def close_location(candle: pd.Series) -> float:
    rng = full_range(candle)
    return 0.5 if rng <= 0 else (float(candle["close"]) - float(candle["low"])) / rng


def average_range(candles: pd.DataFrame) -> float:
    if candles.empty:
        return 0.0
    return float((candles["high"] - candles["low"]).mean())


def is_abnormal_candle(candle: pd.Series, candles: pd.DataFrame) -> bool:
    avg = average_range(candles)
    return bool(avg and full_range(candle) >= SETTINGS.strategy.abnormal_range_multiplier * avg)


def is_small_candle(candle: pd.Series, candles: pd.DataFrame) -> bool:
    avg = average_range(candles)
    return bool(avg and full_range(candle) <= SETTINGS.strategy.compression_range_multiplier * avg)


def close_above_level(candle: pd.Series, level: float) -> bool:
    return float(candle["close"]) > level


def close_below_level(candle: pd.Series, level: float) -> bool:
    return float(candle["close"]) < level


def wick_pierces_level_and_returns(candle: pd.Series, level: float) -> bool:
    pierced = float(candle["low"]) <= level <= float(candle["high"])
    closes_back_inside = (float(candle["close"]) > level and float(candle["open"]) > level) or (
        float(candle["close"]) < level and float(candle["open"]) < level
    )
    return pierced and closes_back_inside


def has_compression(candles: pd.DataFrame, minimum_count: int = 3) -> bool:
    if len(candles) < minimum_count:
        return False
    sample = candles.tail(minimum_count)
    avg = average_range(sample)
    if avg <= 0:
        return False
    return all(full_range(sample.iloc[idx]) <= avg * 1.15 for idx in range(len(sample)))


def ranges(values: Iterable[pd.Series]) -> list[float]:
    return [full_range(value) for value in values]
