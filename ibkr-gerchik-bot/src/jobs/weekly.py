"""Weekly review job."""

from __future__ import annotations

from collections import Counter
from typing import Dict, Iterable, List

from src.config import SETTINGS, append_markdown_log
from src.memory_context import load_workflow_context


def run_weekly(trade_results: Iterable[Dict[str, object]], sp500_week_return_pct: float = 0.0) -> Dict[str, object]:
    """Calculate weekly win rate, expectancy, and strategy-level review."""
    context = load_workflow_context(SETTINGS.paths.strategy_doc, SETTINGS.paths.research_log, SETTINGS.paths.trade_log)
    results = [dict(item) for item in trade_results]
    pnls = [float(item.get("pnl", 0.0)) for item in results]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    strategy_counter = Counter(str(item.get("strategy", "unknown")) for item in results)
    strategy_pnl: Dict[str, float] = {}
    for item in results:
        strategy = str(item.get("strategy", "unknown"))
        strategy_pnl[strategy] = strategy_pnl.get(strategy, 0.0) + float(item.get("pnl", 0.0))
    metrics = {
        "trade_count": len(results),
        "win_rate": round((len(wins) / len(results)) * 100, 2) if results else 0.0,
        "average_r": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "expectancy": round(((sum(wins) + sum(losses)) / len(results)), 2) if results else 0.0,
        "best_trade": round(max(pnls), 2) if pnls else 0.0,
        "worst_trade": round(min(pnls), 2) if pnls else 0.0,
        "best_strategy": max(strategy_pnl, key=strategy_pnl.get) if strategy_pnl else "none",
        "worst_strategy": min(strategy_pnl, key=strategy_pnl.get) if strategy_pnl else "none",
        "sp500_week_return_pct": sp500_week_return_pct,
    }
    review = {
        "metrics": metrics,
        "strategy_counts": dict(strategy_counter),
        "strategy_pnl": strategy_pnl,
        "context_loaded": bool(context["strategy_doc"]),
    }
    append_markdown_log(SETTINGS.paths.weekly_log, "Weekly Review", review)
    return review
