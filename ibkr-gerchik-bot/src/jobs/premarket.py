"""Premarket scanning job."""

from __future__ import annotations

from typing import Dict, List, Sequence

from src.config import SETTINGS, append_markdown_log
from src.data.market_data import MarketDataService
from src.data.news import NewsService
from src.data.news_filter import NewsRiskFilter
from src.memory_context import load_workflow_context
from src.strategy.atr import calculate_daily_atr, calculate_technical_atr
from src.strategy.level_strength import filter_strong_levels
from src.strategy.levels import Level, detect_levels
from src.workflow_log import append_workflow_snapshot


def run_premarket(
    market_data: MarketDataService,
    news_service: NewsService,
    news_filter: NewsRiskFilter,
    account_snapshot: Dict[str, object],
    symbols: Sequence[str],
) -> Dict[str, object]:
    """Build the premarket research plan and level journal."""
    context = load_workflow_context(SETTINGS.paths.strategy_doc, SETTINGS.paths.research_log, SETTINGS.paths.trade_log)
    watchlist: Dict[str, object] = {}
    levels_log: Dict[str, object] = {}
    ideas: List[Dict[str, object]] = []
    macro_risk = news_filter.get_macro_risk_context()
    earnings = news_service.fetch_earnings_calendar(symbols)
    earnings_by_symbol = {str(item.get("symbol", "")): item for item in earnings}

    for symbol in symbols:
        daily_bars = market_data.get_daily_bars(symbol)
        intraday_bars = market_data.get_intraday_bars(symbol, duration="2 D", bar_size="15 mins")
        if daily_bars.empty or intraday_bars.empty:
            continue
        detected_levels = detect_levels(symbol, daily_bars, intraday_bars)
        strong_levels = filter_strong_levels(detected_levels)
        if not strong_levels:
            continue

        current_price = float(intraday_bars.iloc[-1]["close"])
        lower_level = max((level.price for level in strong_levels if level.price < current_price), default=None)
        upper_level = min((level.price for level in strong_levels if level.price > current_price), default=None)
        daily_atr = calculate_daily_atr(daily_bars)
        technical_atr = calculate_technical_atr(current_price, lower_level, upper_level)
        symbol_news = news_filter.get_symbol_risk_context(symbol)

        watchlist[symbol] = {
            "daily_atr": daily_atr,
            "technical_atr": technical_atr,
            "news_blocked": symbol_news["blocked"],
            "matched_headlines": symbol_news["matched_headlines"],
            "news_provider_hits": symbol_news.get("provider_hits", []),
            "news_source_types": symbol_news.get("source_types", []),
            "earnings_event": earnings_by_symbol.get(symbol, {}),
            "levels": [level.to_dict() for level in strong_levels],
        }
        levels_log[symbol] = [level.to_dict() for level in strong_levels]
        if len(ideas) < 3:
            best_level = strong_levels[0]
            ideas.append(
                {
                    "symbol": symbol,
                    "catalyst": symbol_news["matched_headlines"][0] if symbol_news["matched_headlines"] else "level interaction",
                    "entry": current_price,
                    "stop": lower_level if lower_level is not None else current_price * 0.99,
                    "target": upper_level if upper_level is not None else current_price * 1.03,
                    "decision": "HOLD" if symbol_news["blocked"] or macro_risk["blocked"] else "WATCH",
                }
            )

    append_markdown_log(
        SETTINGS.paths.research_log,
        "Premarket Research",
        {
            "strategy_loaded": bool(context["strategy_doc"]),
            "trade_log_context_loaded": bool(context["trade_log_tail"]),
            "research_log_context_loaded": bool(context["research_log_tail"]),
            "account_snapshot": account_snapshot,
            "research_symbols": list(symbols),
            "macro_risk": macro_risk["blocked"],
            "macro_news_sources": macro_risk.get("provider_hits", []),
            "macro_news_source_types": macro_risk.get("source_types", []),
            "actionable_ideas": ideas or "none",
            "trade_decision": "HOLD" if macro_risk["blocked"] else "READY_FOR_OPEN_VALIDATION",
        },
    )
    append_markdown_log(
        SETTINGS.paths.levels_log,
        "Daily Levels",
        {"levels": levels_log or "none"},
    )
    append_workflow_snapshot(
        SETTINGS.paths.research_log,
        "Premarket",
        {
            "watchlist": watchlist,
            "macro_risk": macro_risk,
            "ideas": ideas,
            "research_symbols": list(symbols),
        },
    )
    return {
        "watchlist": watchlist,
        "macro_risk": macro_risk,
        "ideas": ideas,
    }
