"""Pure deterministic calculations for Trend Radar V1.

This module consumes a daily OHLCV frame and never imports a broker, scanner,
API, or storage module.  Keeping the calculations pure makes the safety
boundary explicit and keeps the formulas straightforward to test.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import pandas as pd

from .models import RadarResult, RadarSettings


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _normalize(bars: pd.DataFrame) -> pd.DataFrame:
    if bars is None or bars.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    data = bars.copy()
    for column in ("date", "open", "high", "low", "close", "volume"):
        if column not in data.columns:
            data[column] = None
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    return (
        data.dropna(subset=["date", "open", "high", "low", "close", "volume"])
        .loc[lambda frame: frame["close"] > 0]
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )


def _atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = data["close"].shift(1)
    true_range = pd.concat(
        [data["high"] - data["low"], (data["high"] - previous).abs(), (data["low"] - previous).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _adx_components(data: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    up = data["high"].diff()
    down = -data["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr = _atr(data, period).replace(0, math.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    denominator = (plus_di + minus_di).replace(0, math.nan)
    dx = 100 * (plus_di - minus_di).abs() / denominator
    adx = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return adx, plus_di, minus_di


def _slope_pct(series: pd.Series, window: int) -> float:
    if len(series) <= window:
        return 0.0
    start = _finite(series.iloc[-window - 1])
    end = _finite(series.iloc[-1])
    if start <= 0:
        return 0.0
    return ((end / start) ** (1 / window) - 1) * 100


def _trend_state(score: float, settings: RadarSettings) -> str:
    if score >= settings.trend_green_threshold:
        return "GREEN"
    if score <= settings.trend_red_threshold:
        return "RED"
    return "GREY"


def _business_age(current: pd.Timestamp, previous: str | None) -> int | None:
    if not previous:
        return None
    parsed = pd.to_datetime(previous, errors="coerce")
    if pd.isna(parsed):
        return None
    return max(0, len(pd.bdate_range(parsed.normalize(), current.normalize())) - 1)


def _compression(data: pd.DataFrame, window: int) -> tuple[float, float, float, float]:
    prior = data.iloc[:-1]
    if len(prior) < window:
        return 0.0, 0.0, 0.0, 0.0
    recent = prior.tail(window)
    close = _finite(recent["close"].iloc[-1])
    if close <= 0:
        return 0.0, 0.0, 0.0, 0.0
    recent_width_pct = (_finite(recent["high"].max()) - _finite(recent["low"].min())) / close * 100
    widths: list[float] = []
    for end in range(window, len(prior) + 1):
        sample = prior.iloc[end - window : end]
        sample_close = _finite(sample["close"].iloc[-1])
        if sample_close > 0:
            widths.append((_finite(sample["high"].max()) - _finite(sample["low"].min())) / sample_close * 100)
    baseline = float(pd.Series(widths[:-1] or widths).median()) if widths else recent_width_pct
    ratio = recent_width_pct / baseline if baseline > 0 else 1.0
    score = _clamp(1.0 - (ratio - 0.45) / 1.55) * 100
    return score, recent_width_pct, _finite(prior["high"].tail(window).max()), _finite(prior["low"].tail(window).min())


def analyze_daily_bars(
    symbol: str,
    bars: pd.DataFrame,
    *,
    previous_trend: str | None = None,
    settings: RadarSettings | None = None,
) -> RadarResult | None:
    """Calculate one latest closed-daily Radar observation.

    ``None`` means the frame does not contain enough valid history to make the
    observation.  The latest stored daily bar is used; the engine does not
    stitch an incomplete intraday session into the Radar observation.
    """
    cfg = settings or RadarSettings()
    data = _normalize(bars).tail(max(cfg.lookback_days, cfg.min_history_days)).reset_index(drop=True)
    if len(data) < cfg.min_history_days:
        return None

    close = data["close"]
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean() if len(data) >= 200 else None
    latest_close = _finite(close.iloc[-1])
    latest_ema20 = _finite(ema20.iloc[-1])
    latest_ema50 = _finite(ema50.iloc[-1])
    latest_ema200 = _finite(ema200.iloc[-1], latest_ema50) if ema200 is not None else latest_ema50

    price_factor = 0.5 + 0.5 * math.tanh(((latest_close / latest_ema20) - 1) / 0.03) if latest_ema20 > 0 else 0.5
    structure = 0.5
    structure += 0.18 if latest_close > latest_ema20 else -0.18
    structure += 0.18 if latest_ema20 > latest_ema50 else -0.18
    structure += 0.14 if latest_ema50 > latest_ema200 else -0.14
    structure = _clamp(structure)

    slope20 = _slope_pct(ema20, cfg.slope_window)
    slope50 = _slope_pct(ema50, cfg.slope_window)
    slope_factor = _clamp(0.5 + 0.25 * math.tanh(slope20 / 2.5) + 0.25 * math.tanh(slope50 / 2.0))

    adx, plus_di, minus_di = _adx_components(data)
    latest_adx = _finite(adx.iloc[-1], 20.0)
    latest_plus = _finite(plus_di.iloc[-1], 50.0)
    latest_minus = _finite(minus_di.iloc[-1], 50.0)
    directional = 0.5 + 0.5 * _clamp(latest_adx / 40.0) * (1 if latest_plus >= latest_minus else -1)
    recent_consistency = (close.tail(10) > ema20.tail(10)).mean()
    consistency = _clamp(_finite(recent_consistency, 0.5))

    trend_score = round(100 * _clamp(
        0.25 * price_factor
        + 0.25 * structure
        + 0.20 * slope_factor
        + 0.15 * directional
        + 0.15 * consistency
    ), 2)
    trend = _trend_state(trend_score, cfg)

    atr_series = _atr(data)
    atr = _finite(atr_series.iloc[-1], latest_close * 0.02)
    atr_pct = atr / latest_close * 100 if latest_close > 0 else 0.0
    avg_volume = _finite(data["volume"].iloc[:-1].tail(20).mean(), _finite(data["volume"].tail(20).mean()))
    avg_dollar_volume = _finite((data["close"].iloc[:-1] * data["volume"].iloc[:-1]).tail(20).mean())
    volume_ratio = _finite(data["volume"].iloc[-1] / avg_volume) if avg_volume > 0 else 0.0

    compression_score, _, range_high, range_low = _compression(data, cfg.range_window)
    direction = None
    if latest_close > range_high:
        direction = "BULLISH"
    elif latest_close < range_low:
        direction = "BEARISH"
    displacement = abs(latest_close - (range_high if direction == "BULLISH" else range_low)) / atr if direction else 0.0
    displacement_factor = _clamp(displacement / 1.5)
    volume_factor = _clamp((volume_ratio - 0.8) / 1.7)
    alignment_factor = 1.0 if (direction == "BULLISH" and trend == "GREEN") or (direction == "BEARISH" and trend == "RED") else (0.6 if trend == "GREY" else 0.3)
    prior_atr = _finite(atr_series.iloc[-2], atr) if len(atr_series) > 1 else atr
    volatility_factor = _clamp((atr / prior_atr - 0.8) / 1.2) if prior_atr > 0 else 0.0
    breakout_score = round(100 * _clamp(
        0.25 * (compression_score / 100)
        + 0.25 * displacement_factor
        + 0.20 * volume_factor
        + 0.20 * alignment_factor
        + 0.10 * volatility_factor
    ), 2)
    breakout = bool(direction and compression_score >= cfg.compression_threshold)
    if not breakout:
        direction = None

    return_factor = 0.5 + 0.25 * math.tanh(_finite(close.iloc[-1] / close.iloc[-6] - 1) / 0.05) + 0.25 * math.tanh(_finite(close.iloc[-1] / close.iloc[-21] - 1) / 0.15)
    recent = close.tail(cfg.range_window)
    range_position = _clamp((latest_close - _finite(recent.min())) / max(_finite(recent.max()) - _finite(recent.min()), 1e-9))
    momentum_score = round(100 * _clamp(0.55 * return_factor + 0.25 * range_position + 0.20 * slope_factor), 2)

    liquidity_score = _clamp(avg_dollar_volume / max(cfg.min_avg_dollar_volume, 1.0) * 0.7) * 100
    flip = bool(previous_trend and previous_trend != trend)
    as_of = pd.Timestamp(data["date"].iloc[-1]).date().isoformat()
    freshness_bonus = 5.0 if flip else 0.0
    breakout_bonus = 5.0 if breakout else 0.0
    radar_score = round(min(100.0, max(0.0, 0.35 * trend_score + 0.35 * breakout_score + 0.20 * momentum_score + 0.10 * liquidity_score + freshness_bonus + breakout_bonus)), 2)

    reasons: list[str] = [f"trend_{trend.lower()}"]
    if flip:
        reasons.append("fresh_trend_flip")
    if compression_score >= cfg.compression_threshold:
        reasons.append("range_compression")
    if breakout:
        reasons.append(f"{direction.lower()}_breakout")
    if volume_ratio >= 1.2:
        reasons.append("volume_expansion")
    if momentum_score >= 60:
        reasons.append("positive_momentum")
    elif momentum_score <= 40:
        reasons.append("negative_momentum")
    if avg_dollar_volume >= cfg.min_avg_dollar_volume:
        reasons.append("high_liquidity")

    return RadarResult(
        symbol=str(symbol).strip().upper(),
        as_of=as_of,
        price=round(latest_close, 4),
        trend=trend,
        trend_score=trend_score,
        previous_trend=previous_trend,
        trend_flip=flip,
        trend_flip_from=previous_trend if flip else None,
        trend_flip_to=trend if flip else None,
        trend_flip_date=as_of if flip else None,
        trend_flip_age_days=0 if flip else None,
        breakout=breakout,
        breakout_direction=direction,
        breakout_score=breakout_score,
        breakout_date=as_of if breakout else None,
        breakout_age_days=0 if breakout else None,
        range_high=round(range_high, 4),
        range_low=round(range_low, 4),
        compression_score=round(compression_score, 2),
        momentum_score=momentum_score,
        avg_volume=round(avg_volume, 2),
        avg_dollar_volume=round(avg_dollar_volume, 2),
        volume_ratio=round(volume_ratio, 4),
        atr=round(atr, 4),
        atr_pct=round(atr_pct, 4),
        radar_score=radar_score,
        reasons=tuple(reasons),
    )
