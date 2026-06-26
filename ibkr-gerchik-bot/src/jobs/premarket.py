"""Premarket scanning job."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from src.config import SETTINGS, append_markdown_log
from src.data.bar_store import load_bars, save_bars
from src.data.chart_history import ensure_required_chart_history
from src.data.market_data import MarketDataService
from src.data.news import NewsService
from src.data.news_filter import NewsRiskFilter
from src.memory_context import load_workflow_context
from src.reports.levels_export import export_premarket_levels_report
from src.strategy.atr import calculate_daily_atr, calculate_technical_atr
from src.strategy.level_strength import filter_strong_levels
from src.strategy.levels import Level, detect_levels, optimize_trade_levels
from src.workflow_log import append_workflow_snapshot


def _level_zone(level: Level) -> Tuple[float, float]:
    zone_low = level.zone_low if level.zone_low is not None else level.price
    zone_high = level.zone_high if level.zone_high is not None else level.price
    return min(float(zone_low), float(zone_high)), max(float(zone_low), float(zone_high))


def _level_center(level: Level) -> float:
    return float(level.center if level.center is not None else level.price)


def _spacing_context(levels: Sequence[Level], current_price: float, daily_atr: float) -> Dict[str, object]:
    """Describe whether current price has clean ATR room between nearby level zones."""
    ordered = sorted(levels, key=_level_center)
    lower: Optional[Level] = None
    upper: Optional[Level] = None
    inside: Optional[Level] = None

    for level in ordered:
        zone_low, zone_high = _level_zone(level)
        if zone_low <= current_price <= zone_high:
            inside = level
            break
        if zone_high < current_price:
            lower = level
            continue
        if zone_low > current_price and upper is None:
            upper = level
            break

    lower_zone_low: Optional[float] = None
    lower_zone_high: Optional[float] = None
    upper_zone_low: Optional[float] = None
    upper_zone_high: Optional[float] = None
    if lower is not None:
        lower_zone_low, lower_zone_high = _level_zone(lower)
    if upper is not None:
        upper_zone_low, upper_zone_high = _level_zone(upper)

    clean_gap: Optional[float] = None
    clean_gap_atr_pct: Optional[float] = None
    if lower_zone_high is not None and upper_zone_low is not None:
        clean_gap = max(upper_zone_low - lower_zone_high, 0.0)
        clean_gap_atr_pct = clean_gap / daily_atr if daily_atr > 0 else None

    minimum_gap = SETTINGS.strategy.min_clean_level_gap_atr_pct
    passes_clean_gap = bool(clean_gap_atr_pct is not None and clean_gap_atr_pct >= minimum_gap)
    if inside is not None:
        reason = "price_inside_level_zone"
    elif lower is None or upper is None:
        reason = "missing_bounding_level"
    elif not passes_clean_gap:
        reason = "levels_too_close"
    else:
        reason = "clean_room_between_levels"

    return {
        "current_price": round(current_price, 2),
        "daily_atr": round(daily_atr, 4),
        "lower_level": round(float(lower.price), 2) if lower is not None else None,
        "lower_zone_low": round(float(lower_zone_low), 2) if lower_zone_low is not None else None,
        "lower_zone_high": round(float(lower_zone_high), 2) if lower_zone_high is not None else None,
        "upper_level": round(float(upper.price), 2) if upper is not None else None,
        "upper_zone_low": round(float(upper_zone_low), 2) if upper_zone_low is not None else None,
        "upper_zone_high": round(float(upper_zone_high), 2) if upper_zone_high is not None else None,
        "inside_level": round(float(inside.price), 2) if inside is not None else None,
        "clean_gap": round(clean_gap, 4) if clean_gap is not None else None,
        "clean_gap_atr_pct": round(clean_gap_atr_pct, 4) if clean_gap_atr_pct is not None else None,
        "min_clean_gap_atr_pct": minimum_gap,
        "passes_clean_gap": passes_clean_gap,
        "reason": reason,
    }


def _premarket_monitor_idea(
    *,
    symbol: str,
    current_price: float,
    technical_atr: float,
    spacing: Dict[str, object],
    symbol_news: Dict[str, object],
    macro_risk: Dict[str, object],
) -> Optional[Dict[str, object]]:
    """Return a monitor-only idea when the symbol has enough clean level room."""
    if symbol_news.get("blocked") or macro_risk.get("blocked"):
        return None
    if spacing.get("reason") != "clean_room_between_levels":
        return None
    if current_price <= 0 or (technical_atr / current_price) < SETTINGS.strategy.minimum_technical_atr_pct:
        return None
    return {
        "symbol": symbol,
        "decision": "MONITOR",
        "reason": "clean_room_between_levels",
        "entry": "wait_for_intraday_strategy",
        "stop": "-",
        "target": "-",
        "nearest_lower": spacing.get("lower_level"),
        "nearest_upper": spacing.get("upper_level"),
        "clean_gap_atr_pct": spacing.get("clean_gap_atr_pct"),
    }


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
    chart_history_blocked: Dict[str, object] = {}
    macro_risk = news_filter.get_macro_risk_context()
    earnings = news_service.fetch_earnings_calendar(symbols)
    earnings_by_symbol = {str(item.get("symbol", "")): item for item in earnings}

    for symbol in symbols:
        chart_history = ensure_required_chart_history(market_data, symbol)
        if not chart_history.get("ready"):
            chart_history_blocked[symbol] = chart_history
            continue

        daily_bars = load_bars(symbol, "daily")
        if daily_bars.empty:
            daily_bars = market_data.get_daily_bars(symbol, duration=SETTINGS.strategy.chart_daily_duration)
        intraday_bars = market_data.get_intraday_bars(symbol, duration="2 D", bar_size="15 mins")
        if daily_bars.empty or intraday_bars.empty:
            continue
        # Persist bars for the dashboard so charts render without a live TWS
        # connection. Best-effort: failures must never break the scan.
        save_bars(symbol, "daily", daily_bars)
        save_bars(symbol, "intraday_15m", intraday_bars)
        detected_levels = detect_levels(symbol, daily_bars, intraday_bars)
        strong_levels = filter_strong_levels(detected_levels)
        if not strong_levels:
            continue

        current_price = float(intraday_bars.iloc[-1]["close"])
        daily_atr = calculate_daily_atr(daily_bars)
        raw_level_dicts = [level.to_dict() for level in strong_levels]
        optimized_levels = optimize_trade_levels(strong_levels, daily_atr)
        lower_level = max((level.price for level in optimized_levels if level.price < current_price), default=None)
        upper_level = min((level.price for level in optimized_levels if level.price > current_price), default=None)
        technical_atr = calculate_technical_atr(current_price, lower_level, upper_level)
        spacing = _spacing_context(optimized_levels, current_price, daily_atr)
        symbol_news = news_filter.get_symbol_risk_context(symbol)

        watchlist[symbol] = {
            "security_type": SETTINGS.symbol_security_type(symbol),
            "daily_atr": daily_atr,
            "technical_atr": technical_atr,
            "news_blocked": symbol_news["blocked"],
            "matched_headlines": symbol_news["matched_headlines"],
            "news_provider_hits": symbol_news.get("provider_hits", []),
            "news_source_types": symbol_news.get("source_types", []),
            "earnings_event": earnings_by_symbol.get(symbol, {}),
            "chart_history": chart_history,
            "level_spacing": spacing,
            "raw_levels": raw_level_dicts,
            "levels": [level.to_dict() for level in optimized_levels],
        }
        levels_log[symbol] = {
            "raw_levels": raw_level_dicts,
            "optimized_levels": [level.to_dict() for level in optimized_levels],
        }
        if len(ideas) < 3:
            monitor_idea = _premarket_monitor_idea(
                symbol=symbol,
                current_price=current_price,
                technical_atr=technical_atr,
                spacing=spacing,
                symbol_news=symbol_news,
                macro_risk=macro_risk,
            )
            if monitor_idea is not None:
                ideas.append(monitor_idea)

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
            "chart_history_blocked": chart_history_blocked or "none",
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
            "chart_history_blocked": chart_history_blocked,
        },
    )
    report_path = export_premarket_levels_report(watchlist)
    return {
        "watchlist": watchlist,
        "macro_risk": macro_risk,
        "ideas": ideas,
        "report_path": str(report_path),
    }
