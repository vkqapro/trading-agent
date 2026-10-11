"""Typed models and configuration for Trend Radar.

Radar models are deliberately independent from strategy candidates and order
models.  A radar result says that an instrument is interesting; it is not a
trade instruction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RadarSettings:
    enabled: bool = True
    min_price: float = 5.0
    min_avg_dollar_volume: float = 20_000_000.0
    lookback_days: int = 250
    min_history_days: int = 120
    max_universe_size: int = 1000
    trend_green_threshold: float = 60.0
    trend_red_threshold: float = 40.0
    compression_threshold: float = 45.0
    range_window: int = 20
    slope_window: int = 20


@dataclass(frozen=True)
class RadarResult:
    symbol: str
    as_of: str
    trend: str
    trend_score: float
    price: float | None = None
    previous_trend: str | None = None
    trend_flip: bool = False
    trend_flip_from: str | None = None
    trend_flip_to: str | None = None
    trend_flip_date: str | None = None
    trend_flip_age_days: int | None = None
    breakout: bool = False
    breakout_direction: str | None = None
    breakout_score: float = 0.0
    breakout_date: str | None = None
    breakout_age_days: int | None = None
    range_high: float | None = None
    range_low: float | None = None
    compression_score: float = 0.0
    momentum_score: float = 0.0
    avg_volume: float = 0.0
    avg_dollar_volume: float = 0.0
    volume_ratio: float = 0.0
    atr: float | None = None
    atr_pct: float | None = None
    radar_score: float = 0.0
    reasons: tuple[str, ...] = field(default_factory=tuple)
    scanner_status: str = "NOT_SCANNED"
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "as_of": self.as_of,
            "trend": self.trend,
            "trend_score": self.trend_score,
            # This is the latest closed daily close used by the Radar
            # calculation. Keep the existing ``price`` name for compatibility
            # and expose an explicit alias for API consumers.
            "price": self.price,
            "close_price": self.price,
            "previous_trend": self.previous_trend,
            "trend_flip": self.trend_flip,
            "trend_flip_from": self.trend_flip_from,
            "trend_flip_to": self.trend_flip_to,
            "trend_flip_date": self.trend_flip_date,
            "trend_flip_age_days": self.trend_flip_age_days,
            "breakout": self.breakout,
            "breakout_direction": self.breakout_direction,
            "breakout_score": self.breakout_score,
            "breakout_date": self.breakout_date,
            "breakout_age_days": self.breakout_age_days,
            "range_high": self.range_high,
            "range_low": self.range_low,
            "compression_score": self.compression_score,
            "momentum_score": self.momentum_score,
            "avg_volume": self.avg_volume,
            "avg_dollar_volume": self.avg_dollar_volume,
            "volume_ratio": self.volume_ratio,
            "atr": self.atr,
            "atr_pct": self.atr_pct,
            "radar_score": self.radar_score,
            "reasons": list(self.reasons),
            "scanner_status": self.scanner_status,
            "created_at": self.created_at,
        }
        return payload

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> "RadarResult":
        values = dict(row)
        values["reasons"] = tuple(str(item) for item in (values.get("reasons") or ()))
        return cls(**{field: values.get(field) for field in cls.__dataclass_fields__})
