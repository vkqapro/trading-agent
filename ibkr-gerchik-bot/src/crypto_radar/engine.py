"""Pure deterministic, confirmed-candle calculations for Crypto Trade Radar."""
from __future__ import annotations

import math
from typing import Any
import pandas as pd
from .models import CryptoRadarResult, CryptoRadarSettings, IBKRCryptoContract, OKXCryptoInstrument


def _clamp(value: float, low=0.0, high=1.0) -> float:
    return max(low, min(high, float(value)))


def _normalise(frame: pd.DataFrame | None, *, cutoff: pd.Timestamp | None = None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "quote_volume", "confirmed"])
    data = frame.copy()
    for col in ("date", "open", "high", "low", "close", "volume"):
        if col not in data:
            data[col] = None
    if "quote_volume" not in data:
        data["quote_volume"] = None
    if "confirmed" not in data:
        data["confirmed"] = True
    data["date"] = pd.to_datetime(data["date"], errors="coerce", utc=True)
    for col in ("open", "high", "low", "close", "volume", "quote_volume"):
        data[col] = pd.to_numeric(data[col], errors="coerce")
    data["confirmed"] = data["confirmed"].fillna(True).astype(bool)
    data = data.dropna(subset=["date", "open", "high", "low", "close"]).loc[lambda x: x.close > 0]
    if cutoff is not None:
        data = data.loc[data["date"] <= cutoff]
    return data.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def _atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = data.close.shift(1)
    tr = pd.concat([(data.high-data.low), (data.high-prev).abs(), (data.low-prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()


def _slope(series: pd.Series, window: int) -> float:
    if len(series) <= window or not math.isfinite(float(series.iloc[-1])):
        return 0.0
    start = float(series.iloc[-window-1])
    return (float(series.iloc[-1]) / start - 1.0) if start > 0 else 0.0


def _score_timeframe(frame: pd.DataFrame, settings: CryptoRadarSettings) -> tuple[float, str, float, float, float, float, list[str]]:
    close = frame.close
    e20, e50 = _ema(close, 20), _ema(close, 50)
    last, a20, a50 = float(close.iloc[-1]), float(e20.iloc[-1]), float(e50.iloc[-1])
    structure = (0.4 if last > a20 else 0.1) + (0.35 if a20 > a50 else 0.1) + (0.25 if _slope(e20, settings.slope_window) > 0 else 0.05)
    consistency = float((close.tail(20) > e20.tail(20)).mean())
    score = 100 * _clamp(0.55 * structure + 0.25 * _clamp(0.5 + 5 * _slope(e20, settings.slope_window)) + 0.20 * consistency)
    state = "GREEN" if score >= settings.trend_green_threshold else "RED" if score <= settings.trend_red_threshold else "GREY"
    atr_series = _atr(frame)
    atr = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else last * .02
    atr_pct = atr / last * 100 if last else 0.0
    return score, state, atr, atr_pct, consistency, last, []


def analyze_crypto_radar(contract: IBKRCryptoContract | OKXCryptoInstrument, bars_by_timeframe: dict[str, pd.DataFrame], *, settings: CryptoRadarSettings | None = None, previous_trend: str | None = None) -> CryptoRadarResult:
    cfg = settings or CryptoRadarSettings()
    cutoff = pd.Timestamp.now(tz="UTC")
    daily = _normalise(bars_by_timeframe.get("1D"), cutoff=cutoff)
    confirmed = daily[daily.confirmed]
    diagnostics: list[str] = []
    if not contract.account_qualified:
        return CryptoRadarResult(contract, "", None, "UNAVAILABLE_FOR_ACCOUNT", diagnostics=(contract.diagnostics or "contract is not qualified for the connected account",))
    if len(confirmed) < cfg.min_history_days:
        return CryptoRadarResult(contract, "", None, "INSUFFICIENT_DATA", diagnostics=(f"need {cfg.min_history_days} confirmed daily candles, got {len(confirmed)}",))
    latest = confirmed.iloc[-1]
    latest_ts = pd.Timestamp(latest.date).tz_convert("UTC") if pd.Timestamp(latest.date).tzinfo else pd.Timestamp(latest.date, tz="UTC")
    latest_iso = latest_ts.isoformat().replace("+00:00", "Z")
    if not bool(daily.iloc[-1].confirmed):
        return CryptoRadarResult(contract, latest_iso, latest_iso, "UNCONFIRMED_BAR", diagnostics=("latest daily candle is incomplete",))
    trend_score, trend, atr, atr_pct, consistency, price, _ = _score_timeframe(confirmed, cfg)
    four = _normalise(bars_by_timeframe.get("4H"), cutoff=cutoff); one = _normalise(bars_by_timeframe.get("1H"), cutoff=cutoff)
    confirmation = 0.0; four_state = "GREY"
    if len(four) >= 50:
        confirmation, four_state, _, _, _, _, _ = _score_timeframe(four, cfg)
    alignment = "ALIGNED" if trend == four_state and trend in {"GREEN", "RED"} else "CONFLICT" if {trend, four_state} == {"GREEN", "RED"} else "NEUTRAL"
    prior = confirmed.iloc[:-1]
    window = prior.tail(cfg.range_window)
    range_high, range_low = float(window.high.max()), float(window.low.min())
    direction = "BULLISH" if price > range_high else "BEARISH" if price < range_low else None
    compression = 0.0
    if len(prior) >= cfg.range_window * 2:
        recent_width = (float(window.high.max()) - float(window.low.min())) / price
        baseline = (prior.iloc[-cfg.range_window*2:-cfg.range_window].high.max() - prior.iloc[-cfg.range_window*2:-cfg.range_window].low.min()) / price
        compression = 100 * _clamp(1 - recent_width / baseline) if baseline > 0 else 0.0
    quote = latest.get("quote_volume")
    if pd.isna(quote):
        quote = None
    else:
        quote = float(quote)
    volume_ratio = None
    liquidity_available = quote is not None
    if quote is not None:
        baseline = pd.to_numeric(confirmed.iloc[:-1].quote_volume, errors="coerce").dropna().tail(20).mean()
        volume_ratio = quote / float(baseline) if baseline and baseline > 0 else None
    liquidity_score = 0.0 if quote is None else 100 * _clamp((volume_ratio or 0) / 2)
    momentum = 50.0
    if len(one) >= 20:
        ret = float(one.close.iloc[-1] / one.close.iloc[-20] - 1)
        momentum = 100 * _clamp(.5 + ret * 5)
    breakout = bool(direction and compression >= cfg.compression_threshold)
    breakout_score = 100 * _clamp(.4 * compression/100 + .3 * (1 if direction else 0) + .3 * (1 if alignment == "ALIGNED" else 0.3))
    radar = 100 * _clamp(.45*trend_score/100 + .25*breakout_score/100 + .15*momentum/100 + .15*liquidity_score/100 + (.05 if breakout else 0))
    reasons = [f"trend_{trend.lower()}"]
    if alignment == "ALIGNED": reasons.append("timeframes_aligned")
    if alignment == "CONFLICT": reasons.append("timeframes_conflict")
    if breakout: reasons.append(f"{direction.lower()}_breakout")
    if previous_trend and previous_trend != trend: reasons.append("fresh_trend_flip")
    return CryptoRadarResult(contract, latest_iso, latest_iso, "CANDIDATE", trend, round(trend_score,2), round(confirmation,2), alignment, breakout, direction if breakout else None, round(breakout_score,2), round(compression,2), round(momentum,2), round(liquidity_score,2), liquidity_available, round(price,8), quote, round(volume_ratio,4) if volume_ratio is not None else None, round(atr,8), round(atr_pct,4), round(radar,2), previous_trend, bool(previous_trend and previous_trend != trend), tuple(reasons), tuple(diagnostics))
