"""Market data helpers."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict

import pandas as pd

from src.brokers.ibkr import IBKRClient
from src.config import SETTINGS


class MarketDataService:
    """Provide reusable market data retrieval wrappers."""

    def __init__(self, broker: IBKRClient) -> None:
        self.broker = broker

    def get_intraday_bars(self, symbol: str, duration: str = "5 D", bar_size: str = "5 mins") -> pd.DataFrame:
        bars = self.broker.get_historical_bars(symbol=symbol, duration=duration, bar_size=bar_size)
        if bars is None:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        if bars.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        return bars[["date", "open", "high", "low", "close", "volume"]].copy()

    def get_daily_bars(self, symbol: str, duration: str = "30 D") -> pd.DataFrame:
        bars = self.broker.get_historical_bars(symbol=symbol, duration=duration, bar_size="1 day")
        if bars is None:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        if bars.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        return bars[["date", "open", "high", "low", "close", "volume"]].copy()

    def get_quote(self, symbol: str) -> Dict[str, float]:
        return self.broker.get_market_price(symbol)

    def market_is_open(self, current_time: datetime | None = None) -> bool:
        now = current_time or datetime.now(ZoneInfo(SETTINGS.trading_hours.timezone))
        if now.weekday() >= 5:
            return False
        market_open = now.replace(
            hour=SETTINGS.trading_hours.market_open_hour,
            minute=SETTINGS.trading_hours.market_open_minute,
            second=0,
            microsecond=0,
        )
        market_close = now.replace(
            hour=SETTINGS.trading_hours.market_close_hour,
            minute=SETTINGS.trading_hours.market_close_minute,
            second=0,
            microsecond=0,
        )
        return market_open <= now <= market_close

    def unstable_open_window(self, current_time: datetime | None = None) -> bool:
        now = current_time or datetime.now(ZoneInfo(SETTINGS.trading_hours.timezone))
        market_open = now.replace(
            hour=SETTINGS.trading_hours.market_open_hour,
            minute=SETTINGS.trading_hours.market_open_minute,
            second=0,
            microsecond=0,
        )
        return market_open <= now < market_open + timedelta(minutes=SETTINGS.risk.first_unstable_minutes)
