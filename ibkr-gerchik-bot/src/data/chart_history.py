"""Required chart-history hydration for bot symbols.

The dashboard and forecast tab need higher-timeframe context that is broader
than the live 5-minute collector feed.  A symbol should not become trade-ready
until these chart histories exist locally.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterable, List
from zoneinfo import ZoneInfo

import pandas as pd

from src.config import LOGGER, SETTINGS
from src.data.bar_store import load_bars, save_bars
from src.data.market_data import MarketDataService


@dataclass(frozen=True)
class ChartHistorySpec:
    timeframe: str
    min_rows: int
    fetch_name: str
    refresh_rows: int | None = None
    require_latest_completed_session: bool = False


REQUIRED_CHART_HISTORY: tuple[ChartHistorySpec, ...] = (
    ChartHistorySpec("daily", 60, "daily", 252, True),
    ChartHistorySpec("weekly", 12, "weekly", 52),
    ChartHistorySpec("intraday_4h", 20, "4h", 100),
)


def _row_count(frame: pd.DataFrame) -> int:
    return 0 if frame is None or frame.empty else int(len(frame))


def _last_completed_session_date(now: datetime | None = None) -> object:
    """Return the latest weekday session that should have a completed daily bar."""
    local_now = now or datetime.now(ZoneInfo(SETTINGS.trading_hours.timezone))
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=ZoneInfo(SETTINGS.trading_hours.timezone))
    candidate = local_now.date() - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _latest_calendar_date(frame: pd.DataFrame) -> object | None:
    if frame is None or frame.empty or "date" not in frame.columns:
        return None
    parsed = pd.to_datetime(frame["date"], errors="coerce")
    parsed = parsed.dropna()
    if parsed.empty:
        return None
    try:
        if getattr(parsed.dt, "tz", None) is not None:
            parsed = parsed.dt.tz_convert(SETTINGS.trading_hours.timezone)
    except (AttributeError, TypeError, ValueError):
        pass
    return parsed.max().date()


def _is_stale_for_completed_session(frame: pd.DataFrame) -> bool:
    latest = _latest_calendar_date(frame)
    if latest is None:
        return True
    return latest < _last_completed_session_date()


def daily_bars_from_intraday(intraday_bars: pd.DataFrame) -> pd.DataFrame:
    """Aggregate saved intraday bars into daily OHLCV rows.

    The resulting ``date`` values are plain session dates.  That keeps daily
    files compatible with IBKR daily bars and avoids timezone date shifts when
    the rows are merged with existing daily CSVs.
    """
    columns = ["date", "open", "high", "low", "close", "volume"]
    if intraday_bars is None or intraday_bars.empty or "date" not in intraday_bars.columns:
        return pd.DataFrame(columns=columns)

    frame = intraday_bars.copy()
    raw_dates = frame["date"]
    parsed_samples = []
    for value in raw_dates:
        try:
            parsed_samples.append(pd.Timestamp(value))
        except (TypeError, ValueError):
            continue
    if any(value.tzinfo is not None for value in parsed_samples):
        frame["date"] = pd.to_datetime(raw_dates, errors="coerce", utc=True).dt.tz_convert(
            SETTINGS.trading_hours.timezone
        )
    else:
        frame["date"] = pd.to_datetime(raw_dates, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date")
    if frame.empty:
        return pd.DataFrame(columns=columns)
    if "volume" not in frame:
        frame["volume"] = 0.0
    frame["session_date"] = frame["date"].dt.date
    daily = (
        frame.groupby("session_date", sort=True)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .reset_index()
        .rename(columns={"session_date": "date"})
    )
    daily["date"] = pd.to_datetime(daily["date"].astype(str))
    return daily[columns]


def backfill_daily_from_intraday(symbol: str, session_date: object | None = None) -> Dict[str, object]:
    """Persist missing daily rows for ``symbol`` from saved 5-minute bars."""
    intraday = load_bars(symbol, "intraday_5m")
    derived = daily_bars_from_intraday(intraday)
    if derived.empty:
        return {"symbol": symbol.upper(), "updated": False, "reason": "no_intraday_daily_rows"}

    if session_date is not None:
        requested = pd.Timestamp(session_date).date()
        derived = derived[pd.to_datetime(derived["date"], errors="coerce").dt.date == requested]
        if derived.empty:
            return {
                "symbol": symbol.upper(),
                "updated": False,
                "reason": f"no_intraday_for_{requested}",
            }

    existing = load_bars(symbol, "daily")
    existing_dates = set()
    if not existing.empty and "date" in existing.columns:
        existing_dates = set(pd.to_datetime(existing["date"], errors="coerce").dropna().dt.date)

    missing = derived[
        ~pd.to_datetime(derived["date"], errors="coerce").dt.date.isin(existing_dates)
    ]
    if missing.empty:
        return {"symbol": symbol.upper(), "updated": False, "reason": "already_present"}

    save_path = save_bars(symbol, "daily", missing)
    return {
        "symbol": symbol.upper(),
        "updated": save_path is not None,
        "rows_added": int(len(missing)) if save_path is not None else 0,
        "dates": [str(pd.Timestamp(value).date()) for value in missing["date"]],
    }


def _daily_to_weekly(daily_bars: pd.DataFrame) -> pd.DataFrame:
    if daily_bars is None or daily_bars.empty or "date" not in daily_bars.columns:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    frame = daily_bars.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date")
    if frame.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    if getattr(frame["date"].dt, "tz", None) is not None:
        frame["date"] = frame["date"].dt.tz_convert("America/New_York")
    frame = frame.set_index("date")
    weekly = (
        frame.resample("W-FRI")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    return weekly


def _fetch_required_history(
    market_data: MarketDataService,
    symbol: str,
    spec: ChartHistorySpec,
) -> pd.DataFrame:
    if spec.fetch_name == "daily":
        return market_data.get_daily_bars(symbol, duration=SETTINGS.strategy.chart_daily_duration)
    if spec.fetch_name == "weekly":
        weekly = market_data.get_weekly_bars(symbol, duration=SETTINGS.strategy.chart_weekly_duration)
        if weekly is not None and not weekly.empty:
            return weekly
        # Some IBKR accounts/symbols return empty direct weekly bars while daily
        # bars are available.  Keep the chart usable by deriving W-FRI OHLCV from
        # the stored/fetched daily history.
        daily = load_bars(symbol, "daily")
        if daily.empty:
            daily = market_data.get_daily_bars(symbol, duration=SETTINGS.strategy.chart_daily_duration)
            save_bars(symbol, "daily", daily)
        return _daily_to_weekly(daily)
    if spec.fetch_name == "4h":
        return market_data.get_4h_bars(symbol, duration=SETTINGS.strategy.chart_4h_duration)
    raise ValueError(f"Unknown chart history fetch target: {spec.fetch_name}")


def ensure_required_chart_history(
    market_data: MarketDataService,
    symbol: str,
    *,
    specs: Iterable[ChartHistorySpec] = REQUIRED_CHART_HISTORY,
) -> Dict[str, object]:
    """Fetch and persist required chart timeframes for ``symbol`` if missing.

    Returns a serialisable readiness payload:

    ``{"ready": bool, "timeframes": {...}, "missing": [...]}``
    """
    timeframes: Dict[str, Dict[str, object]] = {}
    missing: List[str] = []

    for spec in specs:
        existing = load_bars(symbol, spec.timeframe)
        existing_rows = _row_count(existing)
        refresh_rows = int(spec.refresh_rows or spec.min_rows)
        stale = spec.require_latest_completed_session and _is_stale_for_completed_session(existing)
        if stale and spec.timeframe == "daily":
            backfill_daily_from_intraday(symbol, _last_completed_session_date())
            existing = load_bars(symbol, spec.timeframe)
            existing_rows = _row_count(existing)
            stale = spec.require_latest_completed_session and _is_stale_for_completed_session(existing)
        if existing_rows >= refresh_rows and not stale:
            timeframes[spec.timeframe] = {"rows": existing_rows, "source": "stored"}
            continue

        try:
            fetched = _fetch_required_history(market_data, symbol, spec)
            save_path = save_bars(symbol, spec.timeframe, fetched)
            stored = load_bars(symbol, spec.timeframe)
            stored_rows = _row_count(stored)
            still_stale = spec.require_latest_completed_session and _is_stale_for_completed_session(stored)
            if still_stale and spec.timeframe == "daily":
                backfill_daily_from_intraday(symbol, _last_completed_session_date())
                stored = load_bars(symbol, spec.timeframe)
                stored_rows = _row_count(stored)
                still_stale = spec.require_latest_completed_session and _is_stale_for_completed_session(stored)
            ready = stored_rows >= spec.min_rows and not still_stale
            if save_path is not None:
                source = "fetched"
            elif ready:
                source = "stored_partial"
            elif still_stale:
                source = "stale"
            else:
                source = "fetch_failed"
            timeframes[spec.timeframe] = {
                "rows": stored_rows,
                "source": source,
                "min_rows": spec.min_rows,
                "refresh_rows": refresh_rows,
                "latest_completed_session": str(_last_completed_session_date())
                if spec.require_latest_completed_session
                else None,
            }
            if not ready:
                missing.append(spec.timeframe)
        except Exception as exc:
            LOGGER.warning("Required chart-history fetch failed for %s %s: %s", symbol, spec.timeframe, exc)
            timeframes[spec.timeframe] = {
                "rows": existing_rows,
                "source": "error",
                "min_rows": spec.min_rows,
                "error": str(exc),
            }
            missing.append(spec.timeframe)

    return {
        "ready": not missing,
        "timeframes": timeframes,
        "missing": missing,
    }
