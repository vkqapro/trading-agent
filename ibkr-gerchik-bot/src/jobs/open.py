"""Market open entry-scanning loop."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Callable, Dict, List

from src.config import LOGGER, SETTINGS, append_markdown_log
from src.data.market_data import MarketDataService
from src.data.news_filter import NewsRiskFilter
from src.execution.order_manager import OrderManager
from src.jobs.session_utils import (
    get_scan_interval,
    is_open_entry_window,
    next_scan_time,
    run_entry_scan,
    serialize_scan_results,
    session_now,
    sleep_until,
    summarize_skip_reasons,
)
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
    *,
    now_provider: Callable[[], datetime] | None = None,
    sleep_provider: Callable[[float], None] | None = None,
) -> List[Dict[str, object]]:
    """Scan repeatedly for entry signals during the opening phase."""
    now_fn = now_provider or session_now
    sleep_fn = sleep_provider or time.sleep

    if not watchlist:
        append_workflow_snapshot(SETTINGS.paths.research_log, "Open", {"blocked": True, "reason": "missing_watchlist"})
        return []

    current_time = now_fn()
    if not market_data.market_is_open(current_time) or not is_open_entry_window(current_time):
        LOGGER.info("Open job skipped outside open-entry window at %s", current_time.isoformat())
        append_workflow_snapshot(
            SETTINGS.paths.research_log,
            "Open",
            {"blocked": True, "reason": "outside_open_window", "timestamp": current_time.isoformat()},
        )
        return []

    executed_total: List[Dict[str, object]] = []
    skipped_total: List[Dict[str, object]] = []
    manual_candidates_total: List[Dict[str, object]] = []
    scans: List[Dict[str, object]] = []

    while True:
        scan_time = now_fn()
        if not market_data.market_is_open(scan_time) or not is_open_entry_window(scan_time):
            break

        interval = get_scan_interval(scan_time)
        LOGGER.info("Open scan loop tick at %s interval=%ss", scan_time.isoformat(), interval)
        scan_result = run_entry_scan(
            stage_name="Open",
            market_data=market_data,
            order_manager=order_manager,
            news_filter=news_filter,
            watchlist=watchlist,
            account_equity=account_equity,
            cash_available=cash_available,
            current_positions=current_positions,
            open_risk_amount=open_risk_amount,
            scan_time=scan_time,
        )
        executed = scan_result["executed"]
        skipped = scan_result["skipped"]
        manual_candidates = scan_result.get("manual_candidates", [])
        executed_total.extend(executed)
        skipped_total.extend(skipped)
        if isinstance(manual_candidates, list):
            manual_candidates_total.extend(manual_candidates)
        scans.append(
            {
                "timestamp": scan_time.isoformat(),
                "interval_seconds": interval,
                "symbols_scanned": scan_result["symbols_scanned"],
                "signals_detected": scan_result["signals_detected"],
                "executed_count": len(executed),
                "skipped_count": len(skipped),
                "manual_candidate_count": len(manual_candidates) if isinstance(manual_candidates, list) else 0,
            }
        )
        for payload in executed:
            open_risk_amount += abs(float(payload["entry"]) - float(payload["stop_loss"])) * float(payload["quantity"])

        next_run = next_scan_time(scan_time, interval)

        if not market_data.market_is_open(now_fn()) or not is_open_entry_window(now_fn()) or interval <= 0:
            break

        sleep_until(next_run, now_fn, sleep_fn)

    append_markdown_log(
        SETTINGS.paths.trade_log,
        "Market Open",
        {
            "executed": executed_total or "none",
            "skipped": skipped_total or "none",
            "manual_candidates": manual_candidates_total or "none",
            "scan_count": len(scans),
            "skip_reason_summary": summarize_skip_reasons(skipped_total) or "none",
        },
    )
    append_workflow_snapshot(
        SETTINGS.paths.research_log,
        "Open",
        {
            "executed": executed_total,
            "skipped": skipped_total,
            "manual_candidates": manual_candidates_total,
            "scans": serialize_scan_results(scans),
        },
    )
    return executed_total
