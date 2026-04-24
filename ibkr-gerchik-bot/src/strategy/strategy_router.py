"""Central strategy router."""

from __future__ import annotations

from typing import List

import pandas as pd

from src.risk.stop_loss import calculate_stop_loss
from src.risk.take_profit import build_partial_targets, calculate_take_profit, reward_risk_ratio
from src.strategy.breakout import detect_breakout
from src.strategy.false_breakout_complex import detect_false_breakout_complex
from src.strategy.false_breakout_one_bar import detect_false_breakout_one_bar
from src.strategy.false_breakout_two_bar import detect_false_breakout_two_bar
from src.strategy.levels import Level
from src.strategy.rebound import detect_rebound
from src.strategy.signal_models import TradeSignal


def route_strategies(symbol: str, intraday_bars: pd.DataFrame, levels: List[Level]) -> List[TradeSignal]:
    signals: List[TradeSignal] = []
    for level in levels:
        candidates = [
            detect_rebound(symbol, intraday_bars, level),
            detect_breakout(symbol, intraday_bars, level),
            detect_false_breakout_one_bar(symbol, intraday_bars, level),
            detect_false_breakout_two_bar(symbol, intraday_bars, level),
            detect_false_breakout_complex(symbol, intraday_bars, level),
        ]
        for signal in candidates:
            if signal is None:
                continue
            adjusted_stop = calculate_stop_loss(signal.entry, signal.stop, signal.direction)
            if adjusted_stop is None:
                continue
            next_level = signal.nearest_upper_level if signal.direction == "long" else signal.nearest_lower_level
            target = calculate_take_profit(signal.entry, adjusted_stop, next_level if isinstance(next_level, float) else signal.target, signal.direction)
            if target is None:
                continue
            signal.stop = adjusted_stop
            signal.target = target
            signal.reward_risk = round(reward_risk_ratio(signal.entry, signal.stop, signal.target), 2)
            signal.partial_targets = build_partial_targets(signal.entry, signal.stop, signal.target, signal.direction)
            signals.append(signal)
    return signals
