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
    default_agent,
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
    allow_new_entries: bool = True,
    stop_event: object | None = None,
    decision_controlled: bool = False,
    manual_run_poll_callback: Callable[[], object] | None = None,
    scan_complete_callback: Callable[..., object] | None = None,
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
        if stop_event is not None and bool(getattr(stop_event, "is_set", lambda: False)()):
            LOGGER.info("Open job stopping at worker shutdown request.")
            break
        scan_time = now_fn()
        if not market_data.market_is_open(scan_time) or not is_open_entry_window(scan_time):
            break

        if manual_run_poll_callback is not None:
            try:
                manual_run_poll_callback()
            except Exception:
                LOGGER.debug("Decision Lab manual-run poll failed", exc_info=True)

        interval = get_scan_interval(scan_time)
        LOGGER.info("Open scan loop tick at %s interval=%ss", scan_time.isoformat(), interval)
        scan_result = (
            run_entry_scan(
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
                decision_controlled=decision_controlled,
            )
            if allow_new_entries
            else {"executed": [], "skipped": [], "manual_candidates": [], "symbols_scanned": 0, "signals_detected": 0}
        )
        default_agent(order_manager).run_paper_safety_cycle(market_data)
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

        if scan_complete_callback is not None:
            try:
                scan_complete_callback(
                    {
                        "scan_id": scan_time.isoformat(),
                        "completed_at": datetime.now(scan_time.tzinfo).isoformat(),
                        "symbols": tuple(watchlist.keys()),
                        "watchlist": watchlist,
                        "scan_result": scan_result,
                        "bars_by_symbol": {},
                    },
                    order_manager=order_manager,
                    market_data=market_data,
                    account_equity=account_equity,
                    cash_available=cash_available,
                    current_positions=current_positions,
                    open_risk_amount=open_risk_amount,
                    market_open=True,
                )
            except Exception:
                LOGGER.exception("Decision Lab completed-scan callback failed; scan remains complete.")

        next_run = next_scan_time(scan_time, interval)

        if not market_data.market_is_open(now_fn()) or not is_open_entry_window(now_fn()) or interval <= 0:
            break

        sleep_until(next_run, now_fn, sleep_fn, stop_event, manual_run_poll_callback)

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
