"""Descriptive winner-versus-loser comparisons with no significance claims."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from .metrics import robust_stats


COMPARISON_FIELDS = (
    "entry_minutes_after_open",
    "atr_pct",
    "planned_reward_risk",
    "mfe_r",
    "mae_r",
    "time_in_trade_minutes",
    "adverse_entry_deviation",
)


def winner_loser_comparison(records: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    winners = [record for record in records if record.get("pnl") is not None and Decimal(str(record["pnl"])) > 0]
    losers = [record for record in records if record.get("pnl") is not None and Decimal(str(record["pnl"])) < 0]
    if not winners or not losers:
        return None
    comparisons: dict[str, Any] = {}
    for field in COMPARISON_FIELDS:
        winner_values = [Decimal(str(item[field])) for item in winners if item.get(field) is not None]
        loser_values = [Decimal(str(item[field])) for item in losers if item.get(field) is not None]
        if winner_values and loser_values:
            comparisons[field] = {"winners": robust_stats(winner_values), "losers": robust_stats(loser_values)}
    return {
        "winner_count": len(winners),
        "loser_count": len(losers),
        "comparisons": comparisons,
        "interpretation_limit": "Descriptive only. Correlation does not establish causation.",
    }
