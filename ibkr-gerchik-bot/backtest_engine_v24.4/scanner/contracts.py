"""Stable contracts for deterministic scanner inputs and JSON outputs."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping

SCHEMA_VERSION = "1.1"
SCANNER_VERSION = "0.2.0"


class ScanStatus(str, Enum):
    ENTRY_SIGNAL = "ENTRY_SIGNAL"
    NO_SIGNAL = "NO_SIGNAL"
    REJECTED = "REJECTED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    ERROR = "ERROR"


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    display_name: str
    version: str
    directions: tuple[str, ...]
    timeframe: str
    required_fields: tuple[str, ...]
    minimum_bars: int
    adapter: Callable[..., "StrategyScanResult"]
    production_enabled: bool = True

    def metadata(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "display_name": self.display_name,
            "version": self.version,
            "directions": list(self.directions),
            "timeframe": self.timeframe,
            "required_fields": list(self.required_fields),
            "minimum_bars": self.minimum_bars,
            "production_enabled": self.production_enabled,
        }


@dataclass(frozen=True)
class StrategyScanResult:
    run_id: str
    symbol: str
    strategy_id: str
    strategy_version: str
    direction: str
    timeframe: str
    as_of: str | None
    status: ScanStatus
    matched: bool
    signal: Mapping[str, Any]
    market: Mapping[str, Any]
    level: Mapping[str, Any]
    trade_plan: Mapping[str, Any]
    quality: Mapping[str, Any]
    evidence: Mapping[str, Any]
    pattern_definition: Mapping[str, Any] = field(default_factory=dict)
    pattern_bars: Mapping[str, Any] = field(default_factory=dict)
    trigger_evidence: Mapping[str, Any] = field(default_factory=dict)
    rejection_evidence: tuple[str, ...] = field(default_factory=tuple)
    chart_annotations: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    rejection_reasons: tuple[str, ...] = field(default_factory=tuple)
    data: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["rejection_evidence"] = list(self.rejection_evidence)
        payload["chart_annotations"] = [dict(item) for item in self.chart_annotations]
        payload["reason_codes"] = list(self.reason_codes)
        payload["rejection_reasons"] = list(self.rejection_reasons)
        payload["warnings"] = list(self.warnings)
        return payload
