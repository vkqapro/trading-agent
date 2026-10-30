"""Market data helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Dict

import pandas as pd

from src.brokers.ibkr import IBKRClient
from src.config import SETTINGS
from src.data.nasdaq_data import NasdaqDataClient


# Must remain aligned with the canonical live engine's 15-minute safety gate.
INTRADAY_FRESHNESS_SLA_SECONDS = 900.0


@dataclass
class IntradayDataResult:
    symbol: str
    source: str
    bars: pd.DataFrame
    request_started_at: str
    request_completed_at: str
    latest_bar_timestamp: str | None
    bar_count: int
    freshness_seconds: float | None
    freshness_status: str
    advanced_since_previous: bool | None = None
    attempted_sources: tuple[str, ...] = ()
    warning: str | None = None
    selection_reason: str | None = None


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

    def _get_ibkr_intraday_bars(
        self,
        symbol: str,
        duration: str,
        bar_size: str,
        include_current_session: bool,
    ) -> pd.DataFrame:
        bars = self.broker.get_historical_bars(symbol=symbol, duration=duration, bar_size=bar_size)
        frames = [bars] if bars is not None and not bars.empty else []
        # IBKR can omit the current partial session from multi-day requests.
        if include_current_session and duration.strip().upper() != "1 D":
            current_session = self.broker.get_historical_bars(
                symbol=symbol, duration="1 D", bar_size=bar_size
            )
            if current_session is not None and not current_session.empty:
                frames.append(current_session)
        if not frames:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        return self._normalize_bars_frame(pd.concat(frames, ignore_index=True))

    @staticmethod
    def _latest_and_age(bars: pd.DataFrame, reference_time: datetime) -> tuple[pd.Timestamp | None, float | None]:
        if bars is None or bars.empty or "date" not in bars:
            return None, None
        latest_values = pd.to_datetime(bars["date"], errors="coerce")
        latest_local = latest_values.max()
        if pd.isna(latest_local):
            return None, None
        latest = pd.Timestamp(latest_local)
        if latest.tzinfo is None:
            latest = latest.tz_localize("America/New_York")
        reference = pd.Timestamp(reference_time)
        if reference.tzinfo is None:
            reference = reference.tz_localize("America/New_York")
        age = (reference.tz_convert("UTC") - latest.tz_convert("UTC")).total_seconds()
        # Future-dated bars are invalid evidence, never fresh evidence.
        if age < 0:
            return latest, None
        return latest, round(age, 3)

    def get_intraday_bars_result(
        self,
        symbol: str,
        duration: str = "5 D",
        bar_size: str = "5 mins",
        *,
        reference_time: datetime | None = None,
        include_current_session: bool = False,
    ) -> IntradayDataResult:
        enforce_freshness = reference_time is not None
        reference = reference_time or datetime.now(ZoneInfo(SETTINGS.trading_hours.timezone))
        started = datetime.now(timezone.utc)
        attempts: list[str] = []
        candidates: list[tuple[str, pd.DataFrame, float | None, str | None]] = []
        errors: list[str] = []

        def evaluate(source: str, bars: pd.DataFrame) -> None:
            normalized = self._normalize_bars_frame(bars)
            latest, age = self._latest_and_age(normalized, reference)
            candidates.append((source, normalized, age, latest.isoformat() if latest is not None else None))

        attempts.append("NASDAQ")
        try:
            evaluate("NASDAQ", self.nasdaq_provider.get_intraday_bars(symbol, duration=duration, bar_size=bar_size))
        except Exception as exc:
            errors.append(f"NASDAQ: {exc}")
            evaluate("NASDAQ", pd.DataFrame())

        primary = candidates[-1]
        primary_fresh = bool(primary[2] is not None and primary[2] <= INTRADAY_FRESHNESS_SLA_SECONDS) if enforce_freshness else bool(not primary[1].empty)
        if not primary_fresh:
            attempts.append("IBKR")
            try:
                evaluate("IBKR", self._get_ibkr_intraday_bars(symbol, duration, bar_size, include_current_session))
            except Exception as exc:
                errors.append(f"IBKR: {exc}")
                evaluate("IBKR", pd.DataFrame())

        available = [item for item in candidates if not item[1].empty]
        if not available:
            status = "ERROR" if errors else "NO_DATA"
            reason = "all sources failed" if errors else "no bars returned"
            selected_source, selected_bars, age, latest_text = "NONE", pd.DataFrame(), None, None
        else:
            fresh = [item for item in available if item[2] is not None and item[2] <= INTRADAY_FRESHNESS_SLA_SECONDS]
            pool = fresh or available
            # Prefer the primary source on a freshness tie; otherwise newest bar wins.
            def candidate_key(item: tuple[str, pd.DataFrame, float | None, str | None]) -> tuple[bool, float, bool]:
                latest_epoch = pd.Timestamp(item[3]).timestamp() if item[3] else float("-inf")
                return (
                    item[2] is not None and item[2] <= INTRADAY_FRESHNESS_SLA_SECONDS,
                    latest_epoch,
                    item[0] == "NASDAQ",
                )

            selected_source, selected_bars, age, latest_text = max(pool, key=candidate_key)
            status = "FRESH" if fresh else "STALE"
            reason = "fresh primary" if selected_source == "NASDAQ" and primary_fresh else "freshest available source"

        completed = datetime.now(timezone.utc)
        warning = "; ".join(errors) if errors else None
        return IntradayDataResult(
            symbol=symbol,
            source=selected_source,
            bars=selected_bars,
            request_started_at=started.isoformat(),
            request_completed_at=completed.isoformat(),
            latest_bar_timestamp=latest_text,
            bar_count=len(selected_bars),
            freshness_seconds=age,
            freshness_status=status,
            attempted_sources=tuple(attempts),
            warning=warning,
            selection_reason=reason,
        )

    def get_intraday_bars(
        self,
        symbol: str,
        duration: str = "5 D",
        bar_size: str = "5 mins",
        *,
        include_current_session: bool = False,
    ) -> pd.DataFrame:
        return self.get_intraday_bars_result(
            symbol,
            duration=duration,
            bar_size=bar_size,
            include_current_session=include_current_session,
        ).bars

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

    def get_quote(self, symbol: str) -> Dict[str, object]:
        quote = dict(self.broker.get_market_price(symbol))
        # The broker snapshot has no portable age field. Stamp the point at
        # which this process received it so autonomous callers can distinguish
        # a fresh quote from missing freshness evidence.
        quote["quote_timestamp"] = datetime.now(timezone.utc).isoformat()
        quote["market_data_type"] = (
            "delayed" if self._delayed_fallback_enabled else "live"
        )
        return quote

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
