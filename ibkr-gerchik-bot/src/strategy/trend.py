"""Trend detection helpers."""

from __future__ import annotations

from typing import Literal

import pandas as pd


Trend = Literal["uptrend", "downtrend", "range", "trend_break"]


def detect_trend(bars: pd.DataFrame, lookback: int = 10) -> Trend:
    if len(bars) < 4:
        return "range"
    sample = bars.tail(lookback)
    highs = sample["high"].tolist()
    lows = sample["low"].tolist()
    if all(highs[idx] > highs[idx - 1] and lows[idx] > lows[idx - 1] for idx in range(1, len(sample))):
        return "uptrend"
    if all(highs[idx] < highs[idx - 1] and lows[idx] < lows[idx - 1] for idx in range(1, len(sample))):
        return "downtrend"
    if len(sample) >= 5:
        latest_close = float(sample.iloc[-1]["close"])
        prior_low = float(sample.iloc[-2]["low"])
        prior_high = float(sample.iloc[-2]["high"])
        if prior_low <= latest_close <= prior_high:
            return "trend_break"
    return "range"
