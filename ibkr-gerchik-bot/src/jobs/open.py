"""Market open job."""

from __future__ import annotations

from typing import Dict, List

from src.data.market_data import MarketDataService
from src.data.news_filter import NewsRiskFilter
from src.execution.order_manager import OrderManager
from src.strategy.false_breakout import detect_false_breakout
from src.strategy.rebound import detect_rebound
from src.strategy.third_touch import detect_third_touch


def _spread_pct(quote: Dict[str, float]) -> float:
    mid = ((quote.get("bid", 0.0) + quote.get("ask", 0.0)) / 2) or quote.get("last", 0.0)
    if not mid:
        return 1.0
    return abs(quote.get("ask", 0.0) - quote.get("bid", 0.0)) / mid


def run_open(
    market_data: MarketDataService,
    order_manager: OrderManager,
    news_filter: NewsRiskFilter,
    watchlist: Dict[str, object],
    account_equity: float,
    current_positions: List[Dict[str, object]],
    open_risk_amount: float,
) -> List[Dict[str, object]]:
    """Evaluate setups at the open and execute qualifying trades."""
    executed: List[Dict[str, object]] = []
    if news_filter.is_macro_risk():
        return executed

    for symbol, payload in watchlist.items():
        level_map = dict(payload.get("levels", {}))
        if payload.get("news_blocked") or news_filter.has_high_risk_news(symbol):
            continue

        bars = market_data.get_intraday_bars(symbol, duration="2 D", bar_size="5 mins")
        if bars.empty:
            continue
        quote = market_data.get_quote(symbol)
        spread_pct = _spread_pct(quote)
        supports = list(level_map.get("support", []))
        resistances = list(level_map.get("resistance", []))
        candidate_levels = supports + resistances
        for level in candidate_levels[:4]:
            is_support = level in supports
            signals = [
                detect_false_breakout(bars, float(level), "short" if not is_support else "long"),
                detect_rebound(bars, float(level), "long" if is_support else "short"),
                detect_third_touch(bars, float(level), "long" if is_support else "short"),
            ]
            for signal in signals:
                if signal.get("signal") == "NONE":
                    continue
                signal["symbol"] = symbol
                success, trade_payload = order_manager.execute_trade(
                    signal=signal,
                    account_equity=account_equity,
                    current_positions=current_positions,
                    open_risk_amount=open_risk_amount,
                    spread_pct=spread_pct,
                )
                if success:
                    executed.append(trade_payload)
                    current_positions.append({"symbol": symbol})
                    open_risk_amount += abs(
                        float(trade_payload["entry"]) - float(trade_payload["stop_loss"])
                    ) * float(trade_payload["quantity"])
                    break
            if any(item["symbol"] == symbol for item in executed):
                break
    return executed
