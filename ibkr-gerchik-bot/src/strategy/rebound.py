"""Rebound from level strategy."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.config import SETTINGS
from src.risk.stop_loss import round_number_guard
from src.risk.take_profit import reward_risk_ratio
from src.strategy.candles import body_size, full_range, is_bearish, is_bullish, lower_wick, upper_wick
from src.strategy.levels import Level
from src.strategy.signal_models import TradeSignal

ZONE_BUFFER_PCT = 0.0015
STOP_BUFFER_PCT = 0.0005
MIN_FALLBACK_REWARD_RISK = 3.0
MIN_ATR_TO_RISK_MULTIPLE = 5.0


def detect_rebound(symbol: str, bars: pd.DataFrame, level: Level) -> Optional[TradeSignal]:
    """Detect a confirmed rebound from a strong level using intraday candles."""
    if bars.empty or len(bars) < 3:
        return None
    if float(level.strength_score or level.strength or 0.0) < SETTINGS.strategy.level_strength_threshold:
        return None

    session_bars = _current_session_bars(bars)
    if len(session_bars) < 3:
        return None

    zone_low, zone_high = _level_zone(level)
    setup_bars = session_bars.iloc[:-2]
    rejection_candle = session_bars.iloc[-2]
    confirmation_candle = session_bars.iloc[-1]

    long_signal = _build_long_rebound(
        symbol=symbol,
        level=level,
        session_bars=session_bars,
        setup_bars=setup_bars,
        rejection_candle=rejection_candle,
        confirmation_candle=confirmation_candle,
        zone_low=zone_low,
        zone_high=zone_high,
    )
    if long_signal is not None:
        return long_signal

    return _build_short_rebound(
        symbol=symbol,
        level=level,
        session_bars=session_bars,
        setup_bars=setup_bars,
        rejection_candle=rejection_candle,
        confirmation_candle=confirmation_candle,
        zone_low=zone_low,
        zone_high=zone_high,
    )


def _build_long_rebound(
    *,
    symbol: str,
    level: Level,
    session_bars: pd.DataFrame,
    setup_bars: pd.DataFrame,
    rejection_candle: pd.Series,
    confirmation_candle: pd.Series,
    zone_low: float,
    zone_high: float,
) -> Optional[TradeSignal]:
    if not _approached_from_above(setup_bars, zone_high):
        return None
    if float(rejection_candle["low"]) > zone_high:
        return None
    if float(rejection_candle["close"]) <= zone_high:
        return None
    if not _strong_lower_rejection(rejection_candle):
        return None
    if not is_bullish(confirmation_candle):
        return None
    if float(confirmation_candle["close"]) <= max(float(rejection_candle["close"]), zone_high):
        return None

    entry = round(float(confirmation_candle["close"]), 2)
    stop = _long_stop(level.price, zone_low, rejection_candle, confirmation_candle)
    return _finalize_signal(
        symbol=symbol,
        level=level,
        session_bars=session_bars,
        signal="BUY",
        direction="long",
        entry=entry,
        stop=stop,
        zone_low=zone_low,
        zone_high=zone_high,
        confirmation_type="bullish_confirmation",
    )


def _build_short_rebound(
    *,
    symbol: str,
    level: Level,
    session_bars: pd.DataFrame,
    setup_bars: pd.DataFrame,
    rejection_candle: pd.Series,
    confirmation_candle: pd.Series,
    zone_low: float,
    zone_high: float,
) -> Optional[TradeSignal]:
    if not _approached_from_below(setup_bars, zone_low):
        return None
    if float(rejection_candle["high"]) < zone_low:
        return None
    if float(rejection_candle["close"]) >= zone_low:
        return None
    if not _strong_upper_rejection(rejection_candle):
        return None
    if not is_bearish(confirmation_candle):
        return None
    if float(confirmation_candle["close"]) >= min(float(rejection_candle["close"]), zone_low):
        return None

    entry = round(float(confirmation_candle["close"]), 2)
    stop = _short_stop(level.price, zone_high, rejection_candle, confirmation_candle)
    return _finalize_signal(
        symbol=symbol,
        level=level,
        session_bars=session_bars,
        signal="SELL",
        direction="short",
        entry=entry,
        stop=stop,
        zone_low=zone_low,
        zone_high=zone_high,
        confirmation_type="bearish_confirmation",
    )


def _finalize_signal(
    *,
    symbol: str,
    level: Level,
    session_bars: pd.DataFrame,
    signal: str,
    direction: str,
    entry: float,
    stop: float,
    zone_low: float,
    zone_high: float,
    confirmation_type: str,
) -> Optional[TradeSignal]:
    risk_per_share = round(abs(entry - stop), 4)
    if risk_per_share <= 0:
        return None

    atr = float(level.atr_value or 0.0)
    if atr > 0 and atr < risk_per_share * MIN_ATR_TO_RISK_MULTIPLE:
        return None

    atr_used = _atr_used(entry, session_bars, atr)
    if atr_used is not None and atr_used > SETTINGS.strategy.atr_travel_limit_pct:
        return None

    target, target_level = _target(level, entry, stop, direction)
    if target is None:
        return None
    reward_risk = round(reward_risk_ratio(entry, stop, target), 2)
    if reward_risk < float(SETTINGS.risk.min_reward_risk_ratio):
        return None

    return TradeSignal(
        symbol=symbol,
        strategy="rebound",
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
        confidence=0.7,
        level_strength=float(level.strength_score or level.strength or 0.0),
        metadata={
            "level_price": level.price,
            "zone_low": zone_low,
            "zone_high": zone_high,
            "confirmation_type": confirmation_type,
        },
        notes=[
            "reasons=confirmed rebound from strong level",
            "confidence=0.7",
            "position_modifier=1.0",
            "context trend=REBOUND atr=OK news=UNKNOWN pattern=REBOUND",
        ],
    )


def _level_zone(level: Level) -> tuple[float, float]:
    zone_low = float(level.zone_low if level.zone_low is not None else level.price)
    zone_high = float(level.zone_high if level.zone_high is not None else level.price)
    if round(zone_low, 4) == round(zone_high, 4):
        buffer = max(abs(float(level.price)) * ZONE_BUFFER_PCT, 0.01)
        zone_low = float(level.price) - buffer
        zone_high = float(level.price) + buffer
    return min(zone_low, zone_high), max(zone_low, zone_high)


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


def _approached_from_above(setup_bars: pd.DataFrame, zone_high: float) -> bool:
    if setup_bars.empty:
        return False
    return bool((setup_bars["close"].astype(float).tail(3) > zone_high).any())


def _approached_from_below(setup_bars: pd.DataFrame, zone_low: float) -> bool:
    if setup_bars.empty:
        return False
    return bool((setup_bars["close"].astype(float).tail(3) < zone_low).any())


def _strong_lower_rejection(candle: pd.Series) -> bool:
    return full_range(candle) > 0 and lower_wick(candle) > body_size(candle)


def _strong_upper_rejection(candle: pd.Series) -> bool:
    return full_range(candle) > 0 and upper_wick(candle) > body_size(candle)


def _long_stop(level_price: float, zone_low: float, rejection_candle: pd.Series, confirmation_candle: pd.Series) -> float:
    buffer = max(float(level_price) * STOP_BUFFER_PCT, 0.01)
    structure_low = min(zone_low, float(rejection_candle["low"]), float(confirmation_candle["low"]))
    return round_number_guard(round(structure_low - buffer, 2), "long")


def _short_stop(level_price: float, zone_high: float, rejection_candle: pd.Series, confirmation_candle: pd.Series) -> float:
    buffer = max(float(level_price) * STOP_BUFFER_PCT, 0.01)
    structure_high = max(zone_high, float(rejection_candle["high"]), float(confirmation_candle["high"]))
    return round_number_guard(round(structure_high + buffer, 2), "short")


def _target(level: Level, entry: float, stop: float, direction: str) -> tuple[Optional[float], Optional[float]]:
    required_rr = float(SETTINGS.risk.min_reward_risk_ratio)
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

    fallback_rr = max(required_rr, MIN_FALLBACK_REWARD_RISK)
    fallback = entry + fallback_rr * risk if direction == "long" else entry - fallback_rr * risk
    return round(fallback, 2), None


def _atr_used(entry: float, session_bars: pd.DataFrame, atr: float) -> Optional[float]:
    if atr <= 0 or session_bars.empty:
        return None
    session_low = float(session_bars["low"].astype(float).min())
    session_high = float(session_bars["high"].astype(float).max())
    return max(abs(entry - session_low), abs(session_high - entry)) / atr
