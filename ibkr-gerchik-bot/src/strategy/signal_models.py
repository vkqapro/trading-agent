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
    risk_per_share: Optional[float] = None
    atr: Optional[float] = None
    atr_used: Optional[float] = None
    confidence: float = 0.0
    level_strength: Optional[float] = None
    is_new_extreme: bool = False
    partial_targets: List[Dict[str, float]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    metadata: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)
