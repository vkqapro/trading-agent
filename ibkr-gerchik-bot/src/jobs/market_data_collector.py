"""Independent intraday bar collector for dashboard continuity."""

from __future__ import annotations

import json
import os
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


def _write_status_artifact(payload: Dict[str, object]) -> None:
    """Write bounded, read-only collector health for diagnostics."""
    path = SETTINGS.paths.runtime_dir / "market_data_collector_status.json"
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.replace(temp, path)
    except OSError as exc:
        LOGGER.warning("Failed to write market-data status artifact: %s", exc)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def run_market_data_collector(
    market_data: MarketDataService,
    watchlist: Dict[str, object],
    *,
    interval_seconds: int = 300,
    once: bool = False,
    force: bool = False,
    now_provider: Callable[[], datetime] | None = None,
    sleep_provider: Callable[[float], None] | None = None,
) -> Dict[str, object]:
    """Collect bars without account synchronization, signals, or orders."""
    now_fn = now_provider or session_now
    sleep_fn = sleep_provider or time.sleep
    totals = {
        "cycles": 0,
        "symbols_requested": 0,
        "symbols_received": 0,
        "symbols_persisted": 0,
        "symbols_fresh": 0,
        "symbols_advanced": 0,
        "symbols_unchanged_but_fresh": 0,
        "symbols_stale": 0,
        "symbols_no_data": 0,
        "symbols_errors": 0,
        "symbols_chart_history_blocked": 0,
        "failed": [],
        "last_cycle": None,
        "latest_fresh_cycle_at": None,
    }

    while True:
        now = now_fn()
        if not force and not _collector_session_is_open(now):
            if once:
                break
            next_open = _next_collector_open(now)
            LOGGER.info("Market-data collector waiting until %s", next_open.isoformat())
            sleep_until(next_open, now_fn, sleep_fn)
            continue
        if force:
            LOGGER.info("Market-data collector force cycle requested outside normal session window check.")

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
                    current_time=now,
                )
            except Exception as exc:
                LOGGER.warning("Market-data collector reconnect retry failed: %s", exc)
        totals["cycles"] = int(totals["cycles"]) + 1
        for key in (
            "symbols_requested", "symbols_received", "symbols_persisted", "symbols_fresh",
            "symbols_advanced", "symbols_unchanged_but_fresh", "symbols_stale",
            "symbols_no_data", "symbols_errors", "symbols_chart_history_blocked",
        ):
            totals[key] = int(totals[key]) + int(result.get(key, 0) or 0)
        failures = result.get("failed", [])
        if isinstance(failures, list):
            totals["failed"].extend(failures)
        cycle_at = datetime.now().astimezone().isoformat()
        cycle_health = {
            "requested": int(result.get("symbols_requested", 0) or 0),
            "received": int(result.get("symbols_received", 0) or 0),
            "fresh": int(result.get("symbols_fresh", 0) or 0),
            "advanced": int(result.get("symbols_advanced", 0) or 0),
            "unchanged_but_fresh": int(result.get("symbols_unchanged_but_fresh", 0) or 0),
            "stale": int(result.get("symbols_stale", 0) or 0),
            "no_data": int(result.get("symbols_no_data", 0) or 0),
            "errors": int(result.get("symbols_errors", 0) or 0),
            "health": (
                "healthy"
                if not any(result.get(key, 0) for key in ("symbols_stale", "symbols_no_data", "symbols_errors"))
                else "degraded"
            ),
            "stale_symbols": result.get("stale", []),
            "provenance": result.get("symbol_provenance", []),
        }
        totals["last_cycle"] = {"completed_at": cycle_at, **cycle_health}
        if cycle_health["fresh"]:
            totals["latest_fresh_cycle_at"] = cycle_at
        _write_status_artifact({
            "market_data_status": cycle_health["health"],
            "last_cycle_at": cycle_at,
            "latest_fresh_cycle_at": totals["latest_fresh_cycle_at"],
            "cycle": totals["cycles"],
            "health": cycle_health,
        })
        LOGGER.info(
            "Market-data cycle %s requested=%s received=%s fresh=%s advanced=%s fresh_unchanged=%s stale=%s no_data=%s errors=%s stale_symbols=%s",
            totals["cycles"], cycle_health["requested"], cycle_health["received"], cycle_health["fresh"],
            cycle_health["advanced"], cycle_health["unchanged_but_fresh"], cycle_health["stale"],
            cycle_health["no_data"], cycle_health["errors"], cycle_health["stale_symbols"],
        )
        if once:
            break

        next_run = next_scan_time(now, max(60, int(interval_seconds)))
        sleep_until(next_run, now_fn, sleep_fn)

    return totals
