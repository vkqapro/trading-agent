"""Stop loss helpers."""

from __future__ import annotations

from typing import Optional

from src.config import SETTINGS


def round_number_guard(price: float, direction: str) -> float:
    fractional = round(price - int(price), 2)
    if fractional in {0.0, 0.25, 0.5, 0.75}:
        return round(price - 0.03, 2) if direction == "long" else round(price + 0.03, 2)
    return round(price, 2)


def calculate_stop_loss(entry_price: float, technical_stop: float, direction: str) -> Optional[float]:
    calculated_stop_distance = entry_price * SETTINGS.risk.calculated_stop_pct
    technical_distance = abs(entry_price - technical_stop)
    if technical_distance <= 0:
        return None
    if technical_distance > calculated_stop_distance * SETTINGS.risk.max_stop_vs_calculated_multiplier:
        return None
    return round_number_guard(technical_stop, direction)
