"""Onboard a newly added stock symbol into the bot workflow."""

from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from src.config import LOGGER, SETTINGS
from src.data.bar_store import load_bars, save_bars
from src.data.chart_history import ensure_required_chart_history
from src.data.market_data import MarketDataService
from src.data.news_filter import NewsRiskFilter
from src.jobs.premarket import _spacing_context
from src.strategy.atr import calculate_daily_atr, calculate_technical_atr
from src.strategy.level_strength import filter_strong_levels
from src.strategy.levels import detect_levels, optimize_trade_levels
from src.symbol_universe import add_stock_symbol, normalize_stock_symbol


def _resample_intraday_15m(intraday_bars: pd.DataFrame) -> pd.DataFrame:
    if intraday_bars is None or intraday_bars.empty or "date" not in intraday_bars.columns:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    frame = intraday_bars.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date")
    if frame.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    if "volume" not in frame:
        frame["volume"] = 0.0
    return (
        frame.set_index("date")
        .resample("15min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )


def _chart_history_from_saved_bars(symbol: str) -> Dict[str, object]:
    daily = load_bars(symbol, "daily")
    weekly = load_bars(symbol, "weekly")
    four_hour = load_bars(symbol, "intraday_4h")
    return {
        "ready": not daily.empty and not weekly.empty and not four_hour.empty,
        "source": "saved_bars",
        "daily": {"rows": int(len(daily)), "ready": not daily.empty},
        "weekly": {"rows": int(len(weekly)), "ready": not weekly.empty},
        "intraday_4h": {"rows": int(len(four_hour)), "ready": not four_hour.empty},
    }


def _build_watchlist_row_from_bars(
    *,
    symbol: str,
    daily_bars: pd.DataFrame,
    intraday_bars: pd.DataFrame,
    chart_history: Optional[Dict[str, object]] = None,
    symbol_news: Optional[Dict[str, object]] = None,
    earnings: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    detected_levels = detect_levels(symbol, daily_bars, intraday_bars)
    strong_levels = filter_strong_levels(detected_levels)
    daily_atr = calculate_daily_atr(daily_bars)
    optimized_levels = optimize_trade_levels(strong_levels, daily_atr) if strong_levels else []
    current_price = float(intraday_bars.iloc[-1]["close"])
    lower_level = max((level.price for level in optimized_levels if level.price < current_price), default=None)
    upper_level = min((level.price for level in optimized_levels if level.price > current_price), default=None)
    technical_atr = calculate_technical_atr(current_price, lower_level, upper_level)
    spacing = _spacing_context(optimized_levels, current_price, daily_atr)
    news_context = symbol_news or {
        "blocked": False,
        "matched_headlines": [],
        "provider_hits": [],
        "source_types": [],
    }
    raw_level_dicts = [level.to_dict() for level in strong_levels]
    return {
        "security_type": SETTINGS.symbol_security_type(symbol),
        "daily_atr": daily_atr,
        "technical_atr": technical_atr,
        "news_blocked": bool(news_context.get("blocked")),
        "matched_headlines": news_context.get("matched_headlines", []),
        "news_provider_hits": news_context.get("provider_hits", []),
        "news_source_types": news_context.get("source_types", []),
        "earnings_event": earnings or {},
        "chart_history": chart_history or _chart_history_from_saved_bars(symbol),
        "level_spacing": spacing,
        "raw_levels": raw_level_dicts,
        "levels": [level.to_dict() for level in optimized_levels],
    }


def run_symbol_onboarding_from_saved_bars(*, symbol: str) -> Dict[str, object]:
    """Build/repair a symbol watchlist plan from already persisted candles."""
    normalized = normalize_stock_symbol(symbol)
    add_result = add_stock_symbol(normalized)
    daily_bars = load_bars(normalized, "daily")
    intraday_bars = load_bars(normalized, "intraday_15m")
    if intraday_bars.empty:
        intraday_bars = _resample_intraday_15m(load_bars(normalized, "intraday_5m"))
        if not intraday_bars.empty:
            save_bars(normalized, "intraday_15m", intraday_bars)

    chart_history = _chart_history_from_saved_bars(normalized)
    if daily_bars.empty or intraday_bars.empty:
        return {
            "symbol": normalized,
            "added": bool(add_result.get("added")),
            "symbols_file": add_result.get("path"),
            "chart_history": chart_history,
            "intraday_5m_rows": int(len(load_bars(normalized, "intraday_5m"))),
            "watchlist_count": 0,
            "ready": False,
            "reason": "missing_saved_daily_or_intraday_bars",
            "watchlist": {},
        }

    row = _build_watchlist_row_from_bars(
        symbol=normalized,
        daily_bars=daily_bars,
        intraday_bars=intraday_bars,
        chart_history=chart_history,
    )
    ready = bool(row["levels"]) and not bool(row["news_blocked"])
    return {
        "symbol": normalized,
        "added": bool(add_result.get("added")),
        "symbols_file": add_result.get("path"),
        "chart_history": chart_history,
        "intraday_5m_rows": int(len(load_bars(normalized, "intraday_5m"))),
        "watchlist_count": 1,
        "raw_levels": len(row["raw_levels"]),
        "levels": len(row["levels"]),
        "ready": ready,
        "reason": "ready_from_saved_bars" if ready else "no_trade_levels_from_saved_bars",
        "watchlist": {normalized: row},
    }


def run_symbol_onboarding(
    *,
    symbol: str,
    market_data: MarketDataService,
    news_service: object,
    news_filter: NewsRiskFilter,
    account_snapshot: Dict[str, object],
) -> Dict[str, object]:
    """Persist a stock symbol, hydrate chart data, and build its watchlist plan."""
    normalized = normalize_stock_symbol(symbol)
    add_result = add_stock_symbol(normalized)

    LOGGER.info("Onboarding stock symbol %s (added=%s)", normalized, add_result.get("added"))
    chart_history = ensure_required_chart_history(market_data, normalized)

    intraday_5m_rows = 0
    try:
        intraday_5m = market_data.get_intraday_bars(
            normalized,
            duration=SETTINGS.strategy.intraday_bar_duration,
            bar_size=SETTINGS.strategy.intraday_bar_size,
            include_current_session=True,
        )
        if intraday_5m is not None and not intraday_5m.empty:
            save_bars(normalized, "intraday_5m", intraday_5m)
            intraday_5m_rows = int(len(intraday_5m))
    except Exception as exc:
        LOGGER.warning("Onboarding %s: 5-minute bar fetch failed: %s", normalized, exc)

    daily_bars = load_bars(normalized, "daily")
    if daily_bars.empty:
        daily_bars = market_data.get_daily_bars(normalized, duration=SETTINGS.strategy.chart_daily_duration)
        save_bars(normalized, "daily", daily_bars)

    intraday_bars = market_data.get_intraday_bars(normalized, duration="2 D", bar_size="15 mins")
    if intraday_bars is not None and not intraday_bars.empty:
        save_bars(normalized, "intraday_15m", intraday_bars)
    else:
        intraday_bars = load_bars(normalized, "intraday_15m")
    if intraday_bars.empty:
        intraday_bars = _resample_intraday_15m(load_bars(normalized, "intraday_5m"))
        if not intraday_bars.empty:
            save_bars(normalized, "intraday_15m", intraday_bars)

    if daily_bars.empty or intraday_bars.empty:
        return {
            "symbol": normalized,
            "added": bool(add_result.get("added")),
            "symbols_file": add_result.get("path"),
            "chart_history": chart_history,
            "intraday_5m_rows": intraday_5m_rows,
            "watchlist_count": 0,
            "ready": False,
            "reason": "missing_daily_or_intraday_bars",
            "watchlist": {},
        }

    symbol_news = news_filter.get_symbol_risk_context(normalized)
    earnings = {}
    try:
        earnings_items = news_service.fetch_earnings_calendar([normalized])
        earnings = next((item for item in earnings_items if str(item.get("symbol", "")).upper() == normalized), {})
    except Exception as exc:
        LOGGER.warning("Onboarding %s: earnings lookup failed: %s", normalized, exc)

    row = _build_watchlist_row_from_bars(
        symbol=normalized,
        daily_bars=daily_bars,
        intraday_bars=intraday_bars,
        chart_history=chart_history,
        symbol_news=symbol_news,
        earnings=earnings,
    )
    watchlist = {normalized: row}
    ready = bool(row["levels"]) and not bool(row["news_blocked"])
    return {
        "symbol": normalized,
        "added": bool(add_result.get("added")),
        "symbols_file": add_result.get("path"),
        "chart_history": chart_history,
        "intraday_5m_rows": intraday_5m_rows,
        "watchlist_count": 1,
        "raw_levels": len(row["raw_levels"]),
        "levels": len(row["levels"]),
        "ready": ready,
        "reason": "ready" if ready else "no_trade_levels_or_news_blocked",
        "watchlist": watchlist,
    }
