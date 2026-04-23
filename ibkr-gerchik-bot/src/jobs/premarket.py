"""Premarket scanning job."""

from __future__ import annotations

from typing import Dict

from src.config import SETTINGS, append_markdown_log
from src.data.market_data import MarketDataService
from src.strategy.levels import build_level_map


def run_premarket(market_data: MarketDataService) -> Dict[str, object]:
    """Build a watchlist with detected levels per symbol."""
    watchlist: Dict[str, object] = {}
    for symbol in SETTINGS.symbols:
        bars = market_data.get_intraday_bars(symbol)
        if bars.empty:
            continue
        watchlist[symbol] = build_level_map(bars)

    append_markdown_log(
        SETTINGS.paths.research_log,
        "Premarket Scan",
        {"symbols_scanned": len(SETTINGS.symbols), "watchlist_symbols": list(watchlist.keys())},
    )
    return watchlist
