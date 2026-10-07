"""Static, machine-readable descriptions for authoritative LP/PRB rule paths."""
from __future__ import annotations

from typing import Any

_PATTERN_DEFINITIONS: dict[str, dict[str, Any]] = {
    "lp1": {
        "strategy": "LP1",
        "direction": "LONG",
        "pattern_family": "level_reclaim",
        "semantic_steps": [
            "price approaches an eligible level from above",
            "one bar penetrates below the level and closes back above it",
            "the reclaim bar passes range, wick, close-position, and volume conditions",
            "the following signal bar reaches the planned entry trigger",
        ],
    },
    "lp2": {
        "strategy": "LP2",
        "direction": "LONG",
        "pattern_family": "level_reclaim_two_bar",
        "semantic_steps": [
            "price approaches an eligible level from above",
            "the penetration bar trades and closes below the level",
            "the reclaim bar closes back above the level and passes volume/range conditions",
            "the following signal bar reaches the planned entry trigger",
        ],
    },
    "prb1": {
        "strategy": "PRB1",
        "direction": "LONG",
        "pattern_family": "breakout",
        "semantic_steps": [
            "price approaches an eligible resistance level from below",
            "one breakout bar trades and closes above the level with valid volume",
            "the following signal bar opens holding the level",
            "the signal bar reaches the planned entry trigger",
        ],
    },
    "prb2": {
        "strategy": "PRB2",
        "direction": "LONG",
        "pattern_family": "breakout_hold_two_bar",
        "semantic_steps": [
            "price approaches an eligible resistance level from below",
            "the breakout bar trades and closes above the level with valid volume/range",
            "the hold bar preserves the breakout above the level",
            "the following signal bar opens holding the level and reaches the planned entry trigger",
        ],
    },
}

ANNOTATION_LABELS = {
    "penetration_reclaim_bar": "Penetration / Reclaim",
    "penetration_reject_bar": "Penetration / Reject",
    "penetration_bar": "Penetration",
    "reclaim_bar": "Reclaim",
    "reject_bar": "Reject",
    "breakout_bar": "Breakout",
    "breakdown_bar": "Breakdown",
    "hold_bar": "Hold",
    "trigger_bar": "Signal",
}


def pattern_definition(strategy_id: str) -> dict[str, Any]:
    """Return a fresh JSON-safe definition for a registered LP/PRB strategy."""
    definition = _PATTERN_DEFINITIONS[str(strategy_id).lower()]
    return {**definition, "semantic_steps": list(definition["semantic_steps"])}
