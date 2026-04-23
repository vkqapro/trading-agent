"""Market data helpers."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from src.brokers.ibkr import IBKRClient


class MarketDataService:
    """Provide reusable market data retrieval wrappers."""

    def __init__(self, broker: IBKRClient) -> None:
        self.broker = broker

    def get_intraday_bars(self, symbol: str, duration: str = "5 D", bar_size: str = "5 mins") -> pd.DataFrame:
        bars = self.broker.get_historical_bars(symbol=symbol, duration=duration, bar_size=bar_size)
        if bars.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        return bars[["date", "open", "high", "low", "close", "volume"]].copy()

    def get_quote(self, symbol: str) -> Dict[str, float]:
        return self.broker.get_market_price(symbol)
