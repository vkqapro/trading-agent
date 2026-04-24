"""Premarket scanning job."""

from __future__ import annotations

from typing import Dict

from src.config import SETTINGS, append_markdown_log
from src.data.market_data import MarketDataService
from src.data.news import NewsService
from src.data.news_filter import NewsRiskFilter
from src.strategy.levels import build_level_map


def run_premarket(
    market_data: MarketDataService,
    news_service: NewsService,
    news_filter: NewsRiskFilter,
) -> Dict[str, object]:
    """Build a watchlist with detected levels and risk annotations per symbol."""
    watchlist: Dict[str, object] = {}
    earnings = news_service.fetch_earnings_calendar(SETTINGS.symbols)
    earnings_by_symbol = {str(item.get("symbol", "")): item for item in earnings}
    macro_risk = news_filter.get_macro_risk_context()

    for symbol in SETTINGS.symbols:
        bars = market_data.get_intraday_bars(symbol)
        if bars.empty:
            continue
        news_context = news_filter.get_symbol_risk_context(symbol)
        watchlist[symbol] = {
            "levels": build_level_map(bars),
            "news_blocked": news_context["blocked"],
            "matched_headlines": news_context["matched_headlines"],
            "earnings_event": earnings_by_symbol.get(symbol, {}),
        }

    blocked_symbols = [symbol for symbol, payload in watchlist.items() if payload.get("news_blocked")]
    append_markdown_log(
        SETTINGS.paths.research_log,
        "Premarket Scan",
        {
            "symbols_scanned": len(SETTINGS.symbols),
            "watchlist_symbols": list(watchlist.keys()),
            "blocked_symbols": blocked_symbols or "none",
            "macro_risk": macro_risk["blocked"],
        },
    )
    return watchlist
