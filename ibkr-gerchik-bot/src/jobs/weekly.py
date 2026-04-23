"""Weekly metrics job."""

from __future__ import annotations

from typing import Dict, Iterable, List

from src.config import SETTINGS, append_markdown_log


def run_weekly(trade_results: Iterable[Dict[str, float]]) -> Dict[str, object]:
    """Calculate basic weekly performance metrics."""
    results: List[float] = [float(item.get("pnl", 0.0)) for item in trade_results]
    wins = [value for value in results if value > 0]
    losses = [value for value in results if value <= 0]
    metrics = {
        "trades": len(results),
        "win_rate": round((len(wins) / len(results)) * 100, 2) if results else 0.0,
        "gross_pnl": round(sum(results), 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
    }
    append_markdown_log(SETTINGS.paths.weekly_log, "Weekly Metrics", metrics)
    return metrics
