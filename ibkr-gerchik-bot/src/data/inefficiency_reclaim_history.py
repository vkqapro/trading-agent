"""Incremental historical-data hydration for IRS timeframes."""

from __future__ import annotations

import time as clock
from dataclasses import dataclass
from typing import Any

from src.config import LOGGER, SETTINGS
from src.data.bar_store import load_bars, save_bars
from src.data.market_data import MarketDataService


@dataclass(frozen=True)
class IRSHistorySpec:
    timeframe: str
    bar_size: str
    full_duration: str
    refresh_duration: str
    minimum_rows: int
    required_for_paper: bool


def history_specs() -> tuple[IRSHistorySpec, ...]:
    settings = SETTINGS.inefficiency_reclaim
    return (
        IRSHistorySpec(
            "daily",
            "1 day",
            f"{settings.daily_history_years} Y",
            "30 D",
            settings.min_daily_history_rows,
            True,
        ),
        IRSHistorySpec(
            "intraday_1h",
            "1 hour",
            f"{settings.hourly_history_years} Y",
            "10 D",
            120,
            True,
        ),
        IRSHistorySpec(
            "intraday_15m",
            "15 mins",
            f"{settings.fifteen_minute_history_years} Y",
            "5 D",
            200,
            True,
        ),
        IRSHistorySpec(
            "intraday_5m",
            "5 mins",
            f"{settings.five_minute_history_months} M",
            "2 D",
            100,
            False,
        ),
        IRSHistorySpec(
            "intraday_1m",
            "1 min",
            f"{settings.one_minute_history_months} M",
            "1 D",
            100,
            False,
        ),
    )


def _terminal_history_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "no security definition",
            "unknown contract",
            "hmds query returned no data",
        )
    )


def _retryable_history_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "timeout",
            "query cancelled",
            "historical data query cancelled",
        )
    )


def _paced_get_bars(
    market_data: MarketDataService,
    symbol: str,
    *,
    duration: str,
    bar_size: str,
    include_current_session: bool,
) -> Any:
    settings = SETTINGS.inefficiency_reclaim
    attempts = max(1, int(settings.history_timeout_retry_count) + 1)
    for attempt in range(1, attempts + 1):
        if settings.history_request_delay_seconds > 0:
            clock.sleep(settings.history_request_delay_seconds)
        try:
            return market_data.get_bars(
                symbol,
                duration=duration,
                bar_size=bar_size,
                include_current_session=include_current_session,
            )
        except Exception as exc:
            if attempt >= attempts or _terminal_history_error(exc) or not _retryable_history_error(exc):
                raise
            delay = settings.history_timeout_retry_delay_seconds * attempt
            LOGGER.warning(
                "IRS history fetch retrying %s %s duration=%s attempt=%s/%s delay=%.1fs: %s",
                symbol,
                bar_size,
                duration,
                attempt + 1,
                attempts,
                delay,
                exc,
            )
            if delay > 0:
                clock.sleep(delay)
    raise RuntimeError("unreachable IRS history retry state")


def _mark_skipped_remaining(
    result: dict[str, Any],
    specs: tuple[IRSHistorySpec, ...],
    start_index: int,
    *,
    reason: str,
) -> None:
    for remaining in specs[start_index:]:
        existing = load_bars(result["symbol"], remaining.timeframe)
        result["timeframes"][remaining.timeframe] = {
            "rows": int(len(existing)),
            "minimum_rows": remaining.minimum_rows,
            "required_for_paper": remaining.required_for_paper,
            "ready": False,
            "skipped": True,
            "error": reason,
        }
        if remaining.required_for_paper:
            result["paper_ready"] = False


def ensure_irs_history(
    market_data: MarketDataService,
    symbol: str,
) -> dict[str, Any]:
    """Refresh each cache incrementally; missing optional data stays explicit."""
    result: dict[str, Any] = {"symbol": symbol.upper(), "timeframes": {}, "paper_ready": True}
    specs = history_specs()
    for index, spec in enumerate(specs):
        existing = load_bars(symbol, spec.timeframe)
        duration = spec.full_duration if len(existing) < spec.minimum_rows else spec.refresh_duration
        try:
            fetched = _paced_get_bars(
                market_data,
                symbol,
                duration=duration,
                bar_size=spec.bar_size,
                include_current_session=spec.timeframe != "daily",
            )
            if fetched is not None and not fetched.empty:
                save_bars(symbol, spec.timeframe, fetched)
            stored = load_bars(symbol, spec.timeframe)
            ready = len(stored) >= spec.minimum_rows
            required_full_history_unavailable = (
                duration == spec.full_duration
                and spec.required_for_paper
                and not ready
            )
            result["timeframes"][spec.timeframe] = {
                "rows": int(len(stored)),
                "minimum_rows": spec.minimum_rows,
                "required_for_paper": spec.required_for_paper,
                "ready": ready,
                "fetch_duration": duration,
            }
            if spec.required_for_paper and not ready:
                result["paper_ready"] = False
            if required_full_history_unavailable:
                reason = f"INSUFFICIENT_{spec.timeframe.upper()}_HISTORY_AFTER_FULL_FETCH"
                LOGGER.warning(
                    "IRS required history unavailable %s %s rows=%s minimum=%s; skipping remaining IRS timeframes.",
                    symbol,
                    spec.timeframe,
                    len(stored),
                    spec.minimum_rows,
                )
                _mark_skipped_remaining(result, specs, index + 1, reason=reason)
                break
        except Exception as exc:
            LOGGER.warning("IRS history fetch failed %s %s: %s", symbol, spec.timeframe, exc)
            result["timeframes"][spec.timeframe] = {
                "rows": int(len(existing)),
                "minimum_rows": spec.minimum_rows,
                "required_for_paper": spec.required_for_paper,
                "ready": False,
                "error": str(exc),
            }
            if spec.required_for_paper:
                result["paper_ready"] = False
            if _terminal_history_error(exc):
                _mark_skipped_remaining(
                    result,
                    specs,
                    index + 1,
                    reason=f"TERMINAL_IBKR_HISTORY_ERROR: {exc}",
                )
                break
    return result
