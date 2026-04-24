"""Market open job."""

from __future__ import annotations

from typing import Dict, List

from src.config import SETTINGS, append_markdown_log
from src.data.market_data import MarketDataService
from src.data.news_filter import NewsRiskFilter
from src.execution.order_manager import OrderManager
from src.memory_context import load_workflow_context
from src.strategy.atr import atr_travel_filter, technical_atr_has_room
from src.strategy.levels import Level
from src.strategy.strategy_router import route_strategies
from src.workflow_log import append_workflow_snapshot


def run_open(
    market_data: MarketDataService,
    order_manager: OrderManager,
    news_filter: NewsRiskFilter,
    watchlist: Dict[str, object],
    account_equity: float,
    cash_available: float,
    current_positions: List[Dict[str, object]],
    open_risk_amount: float,
) -> List[Dict[str, object]]:
    """Re-check live prices, scan setups, validate, and execute."""
    executed: List[Dict[str, object]] = []
    skipped: List[Dict[str, object]] = []
    context = load_workflow_context(SETTINGS.paths.strategy_doc, SETTINGS.paths.research_log, SETTINGS.paths.trade_log)
    if not context["research_log_tail"]:
        append_workflow_snapshot(SETTINGS.paths.research_log, "Open", {"blocked": True, "reason": "missing_research"})
        return executed
    if news_filter.is_macro_risk():
        append_workflow_snapshot(SETTINGS.paths.research_log, "Open", {"blocked": True, "reason": "macro_risk"})
        return executed

    for symbol, plan in watchlist.items():
        if plan.get("news_blocked"):
            skipped.append({"symbol": symbol, "reason": "symbol_news_risk"})
            continue
        intraday_bars = market_data.get_intraday_bars(symbol, duration="2 D", bar_size="5 mins")
        quote = market_data.get_quote(symbol)
        if intraday_bars.empty or quote.get("last", 0.0) <= 0:
            skipped.append({"symbol": symbol, "reason": "missing_live_data"})
            continue

        levels = [Level(**level) for level in plan.get("levels", [])]
        candidate_signals = route_strategies(symbol, intraday_bars, levels)
        if not candidate_signals:
            skipped.append({"symbol": symbol, "reason": "no_signal"})
            continue

        session_low = float(intraday_bars["low"].min())
        session_high = float(intraday_bars["high"].max())
        for signal in candidate_signals:
            atr_ok = technical_atr_has_room(float(plan.get("technical_atr", 0.0)), signal.entry)
            trend_ok = atr_travel_filter(signal.entry, session_low, session_high, float(plan.get("daily_atr", 0.0)), False)
            if not atr_ok or not trend_ok:
                skipped.append({"symbol": symbol, "reason": "atr_filter"})
                continue
            success, payload = order_manager.execute_trade(
                signal,
                account_equity=account_equity,
                cash_available=cash_available,
                current_positions=current_positions,
                open_risk_amount=open_risk_amount,
            )
            if success:
                executed.append(payload)
                current_positions.append({"symbol": symbol})
                open_risk_amount += abs(float(payload["entry"]) - float(payload["stop_loss"])) * float(payload["quantity"])
                break
            skipped.append({"symbol": symbol, "reason": payload.get("reasons", ["rejected"])})

    append_markdown_log(
        SETTINGS.paths.trade_log,
        "Market Open",
        {"executed": executed or "none", "skipped": skipped or "none"},
    )
    append_workflow_snapshot(SETTINGS.paths.research_log, "Open", {"executed": executed, "skipped": skipped})
    return executed
