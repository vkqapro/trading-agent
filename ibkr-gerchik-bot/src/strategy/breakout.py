"""Confirmed breakout strategy.

The strategy layer only returns signal data. Order placement and final
validation stay in the execution/risk layers.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.config import SETTINGS
from src.risk.stop_loss import round_number_guard
from src.risk.take_profit import reward_risk_ratio
from src.strategy.candles import full_range, is_bearish, is_bullish
from src.strategy.levels import Level
from src.strategy.signal_models import TradeSignal

ZONE_BUFFER_PCT = 0.0015
STOP_BUFFER_PCT = 0.0005
MIN_FALLBACK_REWARD_RISK = 3.0
MIN_ACCEPTABLE_REWARD_RISK = 2.0
MIN_ATR_TO_RISK_MULTIPLE = 5.0


def detect_breakout(symbol: str, bars: pd.DataFrame, level: Level) -> Optional[TradeSignal]:
    """Detect a Gerchik-style two-step confirmed breakout on intraday bars."""
    if bars.empty or len(bars) < 4:
        return None
    if float(level.strength_score or level.strength or 0.0) < SETTINGS.strategy.level_strength_threshold:
        return None

    session_bars = _current_session_bars(bars)
    if len(session_bars) < 4:
        return None

    zone_low, zone_high = _level_zone(level.price)
    setup_bars = session_bars.iloc[:-2]
    breakout_candle = session_bars.iloc[-2]
    confirmation_candle = session_bars.iloc[-1]
    breakout_index = _bar_index(bars, breakout_candle.name)

    long_signal = _build_long_signal(
        symbol=symbol,
        level=level,
        session_bars=session_bars,
        setup_bars=setup_bars,
        breakout_candle=breakout_candle,
        confirmation_candle=confirmation_candle,
        breakout_index=breakout_index,
        zone_low=zone_low,
        zone_high=zone_high,
    )
    if long_signal is not None:
        return long_signal

    return _build_short_signal(
        symbol=symbol,
        level=level,
        session_bars=session_bars,
        setup_bars=setup_bars,
        breakout_candle=breakout_candle,
        confirmation_candle=confirmation_candle,
        breakout_index=breakout_index,
        zone_low=zone_low,
        zone_high=zone_high,
    )


def _build_long_signal(
    *,
    symbol: str,
    level: Level,
    session_bars: pd.DataFrame,
    setup_bars: pd.DataFrame,
    breakout_candle: pd.Series,
    confirmation_candle: pd.Series,
    breakout_index: int,
    zone_low: float,
    zone_high: float,
) -> Optional[TradeSignal]:
    if not _acts_as_resistance(level, setup_bars, zone_high):
        return None
    if float(breakout_candle["close"]) <= zone_high:
        return None
    if float(confirmation_candle["close"]) <= zone_high:
        return None
    confirmation_type = _long_confirmation_type(level.price, breakout_candle, confirmation_candle)
    if confirmation_type is None:
        return None
    if _breakout_is_overextended(breakout_candle, setup_bars):
        return None

    entry = round(float(confirmation_candle["close"]), 2)
    stop = _long_stop(level.price, zone_low, breakout_candle, confirmation_candle)
    return _finalize_signal(
        symbol=symbol,
        level=level,
        session_bars=session_bars,
        entry=entry,
        stop=stop,
        direction="long",
        signal="BUY",
        zone_low=zone_low,
        zone_high=zone_high,
        breakout_index=breakout_index,
        confirmation_type=confirmation_type,
    )


def _build_short_signal(
    *,
    symbol: str,
    level: Level,
    session_bars: pd.DataFrame,
    setup_bars: pd.DataFrame,
    breakout_candle: pd.Series,
    confirmation_candle: pd.Series,
    breakout_index: int,
    zone_low: float,
    zone_high: float,
) -> Optional[TradeSignal]:
    if not _acts_as_support(level, setup_bars, zone_low):
        return None
    if float(breakout_candle["close"]) >= zone_low:
        return None
    if float(confirmation_candle["close"]) >= zone_low:
        return None
    confirmation_type = _short_confirmation_type(level.price, breakout_candle, confirmation_candle)
    if confirmation_type is None:
        return None
    if _breakout_is_overextended(breakout_candle, setup_bars):
        return None

    entry = round(float(confirmation_candle["close"]), 2)
    stop = _short_stop(level.price, zone_high, breakout_candle, confirmation_candle)
    return _finalize_signal(
        symbol=symbol,
        level=level,
        session_bars=session_bars,
        entry=entry,
        stop=stop,
        direction="short",
        signal="SELL",
        zone_low=zone_low,
        zone_high=zone_high,
        breakout_index=breakout_index,
        confirmation_type=confirmation_type,
    )


def _finalize_signal(
    *,
    symbol: str,
    level: Level,
    session_bars: pd.DataFrame,
    entry: float,
    stop: float,
    direction: str,
    signal: str,
    zone_low: float,
    zone_high: float,
    breakout_index: int,
    confirmation_type: str,
) -> Optional[TradeSignal]:
    risk_per_share = round(abs(entry - stop), 4)
    if risk_per_share <= 0:
        return None

    atr = float(level.atr_value or 0.0)
    if atr > 0 and atr < risk_per_share * MIN_ATR_TO_RISK_MULTIPLE:
        return None

    atr_used = _atr_used(entry, session_bars, atr)
    is_new_extreme = _is_clean_new_extreme(entry, session_bars, direction)
    if atr_used is not None and atr_used > SETTINGS.strategy.atr_travel_limit_pct and not is_new_extreme:
        return None

    target, target_level = _target(level, entry, stop, direction)
    if target is None:
        return None
    reward_risk = round(reward_risk_ratio(entry, stop, target), 2)
    if reward_risk < MIN_ACCEPTABLE_REWARD_RISK:
        return None

    return TradeSignal(
        symbol=symbol,
        strategy="confirmed_breakout",
        signal=signal,
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        level_price=level.price,
        level_type=level.type,
        nearest_upper_level=target_level if direction == "long" else None,
        nearest_lower_level=target_level if direction == "short" else None,
        reward_risk=reward_risk,
        risk_per_share=risk_per_share,
        atr=atr if atr > 0 else None,
        atr_used=None if atr_used is None else round(atr_used, 4),
        confidence=0.75,
        level_strength=float(level.strength_score or level.strength or 0.0),
        is_new_extreme=is_new_extreme,
        notes=[
            "reasons=two-step confirmed breakout",
            "confidence=0.75",
            "position_modifier=1.0",
            "context trend=BREAKOUT atr=OK news=UNKNOWN pattern=CONFIRMED_BREAKOUT",
        ],
        metadata={
            "level_price": level.price,
            "zone_low": zone_low,
            "zone_high": zone_high,
            "breakout_candle_index": breakout_index,
            "confirmation_type": confirmation_type,
        },
    )


def _level_zone(level_price: float) -> tuple[float, float]:
    buffer = float(level_price) * ZONE_BUFFER_PCT
    return round(float(level_price) - buffer, 4), round(float(level_price) + buffer, 4)


def _current_session_bars(bars: pd.DataFrame) -> pd.DataFrame:
    for column in ("datetime", "date"):
        if column not in bars.columns:
            continue
        timestamps = pd.to_datetime(bars[column], errors="coerce")
        if timestamps.notna().any():
            latest_session = timestamps.dropna().iloc[-1].date()
            session = bars.loc[timestamps.dt.date == latest_session]
            if not session.empty:
                return session
    return bars


def _acts_as_resistance(level: Level, setup_bars: pd.DataFrame, zone_high: float) -> bool:
    level_type = str(level.type).lower()
    if "resistance" in level_type:
        return True
    if "support" in level_type:
        return False
    if setup_bars.empty:
        return False
    return float(setup_bars.iloc[-1]["close"]) <= zone_high


def _acts_as_support(level: Level, setup_bars: pd.DataFrame, zone_low: float) -> bool:
    level_type = str(level.type).lower()
    if "support" in level_type:
        return True
    if "resistance" in level_type:
        return False
    if setup_bars.empty:
        return False
    return float(setup_bars.iloc[-1]["close"]) >= zone_low


def _long_confirmation_type(level_price: float, breakout_candle: pd.Series, candle: pd.Series) -> Optional[str]:
    if is_bullish(candle):
        return "bullish_confirmation"
    if float(candle["low"]) >= float(level_price):
        return "retest_hold"
    if float(candle["high"]) > float(breakout_candle["high"]) and float(candle["close"]) > float(breakout_candle["close"]):
        return "higher_high_higher_close"
    return None


def _short_confirmation_type(level_price: float, breakout_candle: pd.Series, candle: pd.Series) -> Optional[str]:
    if is_bearish(candle):
        return "bearish_confirmation"
    if float(candle["high"]) <= float(level_price):
        return "retest_reject"
    if float(candle["low"]) < float(breakout_candle["low"]) and float(candle["close"]) < float(breakout_candle["close"]):
        return "lower_low_lower_close"
    return None


def _breakout_is_overextended(breakout_candle: pd.Series, setup_bars: pd.DataFrame) -> bool:
    sample = setup_bars.tail(10)
    if sample.empty:
        return False
    recent_average_range = float((sample["high"].astype(float) - sample["low"].astype(float)).mean())
    if recent_average_range <= 0:
        return False
    return full_range(breakout_candle) > recent_average_range * 2.0


def _long_stop(level_price: float, zone_low: float, breakout_candle: pd.Series, confirmation_candle: pd.Series) -> float:
    buffer = max(float(level_price) * STOP_BUFFER_PCT, 0.01)
    structure_low = min(zone_low, float(breakout_candle["low"]), float(confirmation_candle["low"]))
    return round_number_guard(round(structure_low - buffer, 2), "long")


def _short_stop(level_price: float, zone_high: float, breakout_candle: pd.Series, confirmation_candle: pd.Series) -> float:
    buffer = max(float(level_price) * STOP_BUFFER_PCT, 0.01)
    structure_high = max(zone_high, float(breakout_candle["high"]), float(confirmation_candle["high"]))
    return round_number_guard(round(structure_high + buffer, 2), "short")


def _target(level: Level, entry: float, stop: float, direction: str) -> tuple[Optional[float], Optional[float]]:
    required_rr = max(float(SETTINGS.risk.min_reward_risk_ratio), MIN_ACCEPTABLE_REWARD_RISK)
    configured_rr = max(required_rr, MIN_FALLBACK_REWARD_RISK)
    risk = abs(entry - stop)
    if risk <= 0:
        return None, None
    next_level = level.nearest_upper_level if direction == "long" else level.nearest_lower_level

    if next_level is not None:
        next_level = float(next_level)
        if (direction == "long" and next_level > entry) or (direction == "short" and next_level < entry):
            if reward_risk_ratio(entry, stop, next_level) >= required_rr:
                return round(next_level, 2), round(next_level, 2)
            return None, None

    fallback = entry + configured_rr * risk if direction == "long" else entry - configured_rr * risk
    return round(fallback, 2), None


def _atr_used(entry: float, session_bars: pd.DataFrame, atr: float) -> Optional[float]:
    if atr <= 0 or session_bars.empty:
        return None
    session_low = float(session_bars["low"].astype(float).min())
    session_high = float(session_bars["high"].astype(float).max())
    distance_traveled = max(abs(entry - session_low), abs(session_high - entry))
    return distance_traveled / atr


def _is_clean_new_extreme(entry: float, session_bars: pd.DataFrame, direction: str) -> bool:
    prior_bars = session_bars.iloc[:-1]
    if prior_bars.empty:
        return False
    if direction == "long":
        return entry >= float(prior_bars["high"].astype(float).max())
    return entry <= float(prior_bars["low"].astype(float).min())


def _bar_index(bars: pd.DataFrame, index_label: object) -> int:
    try:
        return int(bars.index.get_loc(index_label))
    except (KeyError, TypeError, ValueError):
        return max(len(bars) - 2, 0)
