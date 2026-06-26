"""Independent intraday bar collector for dashboard continuity."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Callable, Dict

from src.config import LOGGER, SETTINGS
from src.data.market_data import MarketDataService
from src.jobs.session_utils import (
    collect_watchlist_intraday_bars,
    next_scan_time,
    session_now,
    sleep_until,
)


def _collector_session_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Return the intraday collection window for the date represented by ``now``."""
    start = now.replace(
        hour=SETTINGS.trading_hours.market_open_hour,
        minute=SETTINGS.trading_hours.market_open_minute,
        second=0,
        microsecond=0,
    )
    end = now.replace(
        hour=SETTINGS.trading_hours.market_data_collector_end_hour,
        minute=SETTINGS.trading_hours.market_data_collector_end_minute,
        second=0,
        microsecond=0,
    )
    return start, end


def _collector_session_is_open(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    start, end = _collector_session_bounds(now)
    return start <= now <= end


def _next_collector_open(now: datetime) -> datetime:
    next_open = now.replace(
        hour=SETTINGS.trading_hours.market_open_hour,
        minute=SETTINGS.trading_hours.market_open_minute,
        second=0,
        microsecond=0,
    )
    if now >= next_open:
        next_open += timedelta(days=1)
    while next_open.weekday() >= 5:
        next_open += timedelta(days=1)
    return next_open


def run_market_data_collector(
    market_data: MarketDataService,
    watchlist: Dict[str, object],
    *,
    interval_seconds: int = 300,
    once: bool = False,
    now_provider: Callable[[], datetime] | None = None,
    sleep_provider: Callable[[float], None] | None = None,
) -> Dict[str, object]:
    """Collect bars without account synchronization, signals, or orders."""
    now_fn = now_provider or session_now
    sleep_fn = sleep_provider or time.sleep
    totals = {
        "cycles": 0,
        "symbols_requested": 0,
        "symbols_persisted": 0,
        "symbols_advanced": 0,
        "symbols_stale": 0,
        "symbols_chart_history_blocked": 0,
        "failed": [],
    }

    while True:
        now = now_fn()
        if not _collector_session_is_open(now):
            if once:
                break
            next_open = _next_collector_open(now)
            LOGGER.info("Market-data collector waiting until %s", next_open.isoformat())
            sleep_until(next_open, now_fn, sleep_fn)
            continue

        result = collect_watchlist_intraday_bars(
            market_data,
            watchlist,
            current_time=now,
        )
        stale_count = int(result.get("symbols_stale", 0) or 0)
        requested_count = int(result.get("symbols_requested", 0) or 0)
        if requested_count and stale_count >= max(1, requested_count // 2):
            LOGGER.warning(
                "Market-data collector received stale bars for %s/%s symbols; reconnecting and retrying once.",
                stale_count,
                requested_count,
            )
            try:
                market_data.reconnect()
                result = collect_watchlist_intraday_bars(
                    market_data,
                    watchlist,
                    current_time=now_fn(),
                )
            except Exception as exc:
                LOGGER.warning("Market-data collector reconnect retry failed: %s", exc)
        totals["cycles"] = int(totals["cycles"]) + 1
        totals["symbols_requested"] = int(totals["symbols_requested"]) + int(
            result.get("symbols_requested", 0)
        )
        totals["symbols_persisted"] = int(totals["symbols_persisted"]) + int(
            result.get("symbols_persisted", 0)
        )
        totals["symbols_advanced"] = int(totals["symbols_advanced"]) + int(
            result.get("symbols_advanced", 0)
        )
        totals["symbols_stale"] = int(totals["symbols_stale"]) + int(
            result.get("symbols_stale", 0)
        )
        totals["symbols_chart_history_blocked"] = int(totals["symbols_chart_history_blocked"]) + int(
            result.get("symbols_chart_history_blocked", 0)
        )
        failures = result.get("failed", [])
        if isinstance(failures, list):
            totals["failed"].extend(failures)

        LOGGER.info(
            "Market-data collector cycle=%s requested=%s persisted=%s advanced=%s stale=%s chart_history_blocked=%s failed=%s",
            totals["cycles"],
            result.get("symbols_requested", 0),
            result.get("symbols_persisted", 0),
            result.get("symbols_advanced", 0),
            result.get("symbols_stale", 0),
            result.get("symbols_chart_history_blocked", 0),
            len(failures) if isinstance(failures, list) else 0,
        )
        if once:
            break

        next_run = next_scan_time(now, max(60, int(interval_seconds)))
        sleep_until(next_run, now_fn, sleep_fn)

    return totals
