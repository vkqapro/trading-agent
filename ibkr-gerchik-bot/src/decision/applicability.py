"""Deterministic candidate applicability gates before any provider call."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .models import DecisionCandidate


@dataclass(frozen=True)
class ApplicabilityResult:
    applicable: bool
    outcome: str
    reason: str
    matching_position_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "applicable": self.applicable,
            "outcome": self.outcome,
            "reason": self.reason,
            "matching_position_count": self.matching_position_count,
        }


def _position_symbol(position: Mapping[str, object]) -> str:
    return str(position.get("symbol") or position.get("ticker") or position.get("localSymbol") or "").strip().upper()


def _has_quantity(position: Mapping[str, object]) -> bool:
    for key in ("quantity", "position", "qty", "shares", "units"):
        try:
            if abs(float(position.get(key) or 0.0)) > 0.0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def evaluate_candidate_applicability(
    candidate: DecisionCandidate,
    *,
    current_positions: Sequence[Mapping[str, object]] = (),
    broker_positions: Sequence[Mapping[str, object]] = (),
) -> ApplicabilityResult:
    metadata = candidate.metadata if isinstance(candidate.metadata, Mapping) else {}
    candidate_class = str(metadata.get("candidate_class") or "").strip().upper()
    source_signal = str(metadata.get("source_signal") or "").strip().upper()
    if candidate_class != "POSITION_MANAGEMENT_SIGNAL":
        return ApplicabilityResult(True, "APPLICABLE", "candidate_is_not_position_management")

    symbol = candidate.symbol.upper()
    positions = [*current_positions, *broker_positions]
    matches = [item for item in positions if isinstance(item, Mapping) and _position_symbol(item) == symbol and _has_quantity(item)]
    if not matches:
        return ApplicabilityResult(
            False,
            "NOT_APPLICABLE",
            f"no_matching_{symbol}_position_for_{source_signal or 'position_management_signal'}",
        )
    return ApplicabilityResult(True, "APPLICABLE", "matching_symbol_position_exists", len(matches))
