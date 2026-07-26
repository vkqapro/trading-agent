"""Market data helpers."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict

import pandas as pd

from src.brokers.ibkr import IBKRClient
from src.config import SETTINGS
from src.data.nasdaq_data import NasdaqDataClient


class MarketDataService:
    """Provide reusable market data retrieval wrappers."""

    def __init__(self, broker: IBKRClient, nasdaq_provider: NasdaqDataClient | None = None) -> None:
        self.broker = broker
        self.nasdaq_provider = nasdaq_provider or NasdaqDataClient()
        self._delayed_fallback_enabled = False

    def enable_delayed_fallback(self) -> None:
        """Allow delayed data when the account lacks a live subscription."""
        self.broker.request_market_data_type(3)
        self._delayed_fallback_enabled = True

    def reconnect(self) -> None:
        """Refresh the broker session and restore the collector data mode."""
        self.broker.disconnect()
        self.broker.connect()
        if self._delayed_fallback_enabled:
            self.broker.request_market_data_type(3)

    @staticmethod
    def _normalize_bars_frame(bars: pd.DataFrame) -> pd.DataFrame:
        if bars is None or bars.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

        normalized = bars[["date", "open", "high", "low", "close", "volume"]].copy()
        normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
        normalized = (
            normalized.dropna(subset=["date"])
            .sort_values("date")
            .drop_duplicates(subset=["date"], keep="last")
            .reset_index(drop=True)
        )
        return normalized

    def get_intraday_bars(
        self,
        symbol: str,
        duration: str = "5 D",
        bar_size: str = "5 mins",
        *,
        include_current_session: bool = False,
    ) -> pd.DataFrame:
        nasdaq_bars = self.nasdaq_provider.get_intraday_bars(
            symbol,
            duration=duration,
            bar_size=bar_size,
        )
        if nasdaq_bars is not None and not nasdaq_bars.empty:
            return self._normalize_bars_frame(nasdaq_bars)

        bars = self.broker.get_historical_bars(symbol=symbol, duration=duration, bar_size=bar_size)
        frames = [bars] if bars is not None and not bars.empty else []

        # IBKR treats multi-day duration strings as completed trading sessions.
        # During market hours that response can omit the entire current partial
        # session, so stitch in a separate one-day request.
        if include_current_session and duration.strip().upper() != "1 D":
            current_session = self.broker.get_historical_bars(
                symbol=symbol,
                duration="1 D",
                bar_size=bar_size,
            )
            if current_session is not None and not current_session.empty:
                frames.append(current_session)

        if not frames:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        return self._normalize_bars_frame(pd.concat(frames, ignore_index=True))

    def get_daily_bars(self, symbol: str, duration: str | None = None) -> pd.DataFrame:
        resolved_duration = duration or f"{SETTINGS.strategy.premarket_daily_lookback_days} D"
        nasdaq_bars = self.nasdaq_provider.get_daily_bars(symbol, duration=resolved_duration)
        if nasdaq_bars is not None and not nasdaq_bars.empty:
            return self._normalize_bars_frame(nasdaq_bars)

        bars = self.broker.get_historical_bars(symbol=symbol, duration=resolved_duration, bar_size="1 day")
        if bars is None:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        if bars.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        return self._normalize_bars_frame(bars)

    def get_weekly_bars(self, symbol: str, duration: str | None = None) -> pd.DataFrame:
        resolved_duration = duration or SETTINGS.strategy.chart_weekly_duration
        bars = self.broker.get_historical_bars(symbol=symbol, duration=resolved_duration, bar_size="1 week")
        if bars is None or bars.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        return self._normalize_bars_frame(bars)

    def get_4h_bars(self, symbol: str, duration: str | None = None) -> pd.DataFrame:
        resolved_duration = duration or SETTINGS.strategy.chart_4h_duration
        bars = self.broker.get_historical_bars(symbol=symbol, duration=resolved_duration, bar_size="4 hours")
        if bars is None or bars.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        return self._normalize_bars_frame(bars)

    def get_quote(self, symbol: str) -> Dict[str, float]:
        return self.broker.get_market_price(symbol)

    def get_bars(
        self,
        symbol: str,
        *,
        duration: str,
        bar_size: str,
        include_current_session: bool = False,
    ) -> pd.DataFrame:
        """Fetch a timeframe for an incremental strategy cache."""
        if bar_size == "1 day":
            return self.get_daily_bars(symbol, duration=duration)
        return self.get_intraday_bars(
            symbol,
            duration=duration,
            bar_size=bar_size,
            include_current_session=include_current_session,
        )

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
