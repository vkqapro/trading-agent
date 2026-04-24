"""Shared strategy data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


@dataclass
class TradeSignal:
    symbol: str
    strategy: str
    signal: str
    direction: str
    entry: float
    stop: float
    target: float
    level_price: float
    level_type: str
    nearest_upper_level: Optional[float] = None
    nearest_lower_level: Optional[float] = None
    reward_risk: float = 0.0
    partial_targets: List[Dict[str, float]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)
