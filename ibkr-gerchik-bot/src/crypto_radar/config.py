"""Environment-backed Crypto Trade Radar settings."""
from __future__ import annotations
import os
from .models import CryptoRadarSettings

def crypto_radar_settings() -> CryptoRadarSettings:
    def integer(name: str, default: int) -> int:
        try: return int(os.getenv(name, str(default)))
        except (TypeError, ValueError): return default
    def number(name: str, default: float) -> float:
        try: return float(os.getenv(name, str(default)))
        except (TypeError, ValueError): return default
    return CryptoRadarSettings(
        enabled=os.getenv("CRYPTO_RADAR_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"},
        lookback_days=integer("CRYPTO_RADAR_LOOKBACK_DAYS", 300),
        min_history_days=integer("CRYPTO_RADAR_MIN_HISTORY_DAYS", 200),
        max_universe_size=integer("CRYPTO_RADAR_MAX_UNIVERSE_SIZE", 200),
        trend_green_threshold=number("CRYPTO_RADAR_TREND_GREEN_THRESHOLD", 60.0),
        trend_red_threshold=number("CRYPTO_RADAR_TREND_RED_THRESHOLD", 40.0),
        compression_threshold=number("CRYPTO_RADAR_COMPRESSION_THRESHOLD", 45.0),
        range_window=integer("CRYPTO_RADAR_RANGE_WINDOW", 20),
        slope_window=integer("CRYPTO_RADAR_SLOPE_WINDOW", 20),
        min_quote_volume_usd=number("CRYPTO_RADAR_MIN_QUOTE_VOLUME_USD", 0.0),
    )
