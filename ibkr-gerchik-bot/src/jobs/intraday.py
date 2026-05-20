"""Intraday scanning and position-management loop."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Callable, Dict, List

from src.alerts.slack import SlackAlerter
from src.brokers.ibkr import IBKRClient
from src.config import LOGGER, SETTINGS, append_markdown_log
from src.data.news_filter import NewsRiskFilter
from src.jobs.session_utils import (
    build_job_dependencies,
    calculate_open_risk_amount,
    can_scan_for_new_entries,
    get_scan_interval,
    intraday_session_active,
    load_runtime_state,
    manage_positions,
    next_scan_time,
    persist_tracked_positions,
    run_entry_scan,
    serialize_scan_results,
    session_now,
    sleep_until,
    summarize_skip_reasons,
    sync_tracked_positions_with_broker,
)
from src.reports.intraday_report import write_intraday_scan_report
from src.workflow_log import append_workflow_snapshot


def run_intraday(
    broker: IBKRClient,
    alerter: SlackAlerter,
    news_filter: NewsRiskFilter,
    tracked_positions: List[Dict[str, object]],
    account_equity: float,
    daily_realized_pnl: float = 0.0,
    dry_run: bool = False,
    *,
    now_provider: Callable[[], datetime] | None = None,
    sleep_provider: Callable[[float], None] | None = None,
) -> List[Dict[str, object]]:
    """Continue scanning for new entries and manage any open positions."""
    now_fn = now_provider or session_now
    sleep_fn = sleep_provider or time.sleep
    market_data, order_manager = build_job_dependencies(broker, alerter, news_filter, dry_run=dry_run)

    current_time = now_fn()
    if not market_data.market_is_open(current_time):
        LOGGER.info("Intraday job skipped because market is closed at %s", current_time.isoformat())
        append_workflow_snapshot(
            SETTINGS.paths.research_log,
            "Intraday",
            {"actions": [], "blocked": True, "reason": "market_closed", "timestamp": current_time.isoformat()},
        )
        return []
    if not intraday_session_active(current_time):
        LOGGER.info("Intraday job skipped because the intraday session is inactive at %s", current_time.isoformat())
        append_workflow_snapshot(
            SETTINGS.paths.research_log,
            "Intraday",
            {"actions": [], "blocked": True, "reason": "intraday_session_inactive", "timestamp": current_time.isoformat()},
        )
        return []

    state = load_runtime_state()
    watchlist = state.get("watchlist", {})
    if not isinstance(watchlist, dict):
        watchlist = {}

    executed_total: List[Dict[str, object]] = []
    skipped_total: List[Dict[str, object]] = []
    manual_candidates_total: List[Dict[str, object]] = []
    actions_total: List[Dict[str, object]] = []
    scans: List[Dict[str, object]] = []

    while True:
        loop_time = now_fn()
        if not market_data.market_is_open(loop_time):
            break
        if not intraday_session_active(loop_time):
            LOGGER.info("Intraday session ended at %s", loop_time.isoformat())
            break

        broker_positions = broker.get_positions()
        open_orders = broker.get_open_orders()
        tracked_positions[:] = sync_tracked_positions_with_broker(
            tracked_positions,
            broker_positions,
            open_orders,
        )

        interval = get_scan_interval(loop_time)
        LOGGER.info("Intraday loop tick at %s interval=%ss", loop_time.isoformat(), interval)
        iteration_executed: List[Dict[str, object]] = []
        iteration_skipped: List[Dict[str, object]] = []
        iteration_manual_candidates: List[Dict[str, object]] = []
        iteration_report_rows: List[Dict[str, object]] = []
        symbols_scanned = 0
        signals_detected = 0
        entries_enabled = can_scan_for_new_entries(loop_time)
        LOGGER.info(
            "Intraday loop state: entries_enabled=%s watchlist_symbols=%s tracked_positions=%s",
            entries_enabled,
            len(watchlist),
            len(tracked_positions),
        )

        if entries_enabled and watchlist:
            scan_result = run_entry_scan(
                stage_name="Intraday",
                market_data=market_data,
                order_manager=order_manager,
                news_filter=news_filter,
                watchlist=watchlist,
                account_equity=account_equity,
                cash_available=account_equity,
                current_positions=tracked_positions,
                open_risk_amount=calculate_open_risk_amount(tracked_positions),
                scan_time=loop_time,
            )
            executed = scan_result["executed"]
            skipped = scan_result["skipped"]
            manual_candidates = scan_result.get("manual_candidates", [])
            report_rows = scan_result.get("report_rows", [])
            iteration_executed = executed
            iteration_skipped = skipped
            if isinstance(manual_candidates, list):
                iteration_manual_candidates = manual_candidates
            if isinstance(report_rows, list):
                iteration_report_rows = report_rows
            symbols_scanned = int(scan_result["symbols_scanned"])
            signals_detected = int(scan_result["signals_detected"])
            executed_total.extend(executed)
            skipped_total.extend(skipped)
            manual_candidates_total.extend(iteration_manual_candidates)
            scans.append(
                {
                    "timestamp": loop_time.isoformat(),
                    "interval_seconds": interval,
                    "symbols_scanned": scan_result["symbols_scanned"],
                    "signals_detected": scan_result["signals_detected"],
                    "executed_count": len(executed),
                    "skipped_count": len(skipped),
                    "manual_candidate_count": len(iteration_manual_candidates),
                }
            )
        elif not entries_enabled:
            LOGGER.info(
                "Intraday entries disabled at %s; managing positions only until next loop.",
                loop_time.isoformat(),
            )
        else:
            LOGGER.info("Intraday watchlist is empty; managing positions only until next loop.")

        management = manage_positions(
            broker=broker,
            news_filter=news_filter,
            tracked_positions=tracked_positions,
            account_equity=account_equity,
            daily_realized_pnl=daily_realized_pnl,
            dry_run=dry_run,
        )
        actions = management["actions"]
        actions_total.extend(actions)
        persist_tracked_positions(tracked_positions)
        alerter.send_intraday_heartbeat(
            {
                "timestamp": loop_time.isoformat(),
                "tracked_symbols": [position.get("symbol") for position in tracked_positions],
                "actions": actions,
                "macro_risk": news_filter.is_macro_risk(),
                "entries_enabled": entries_enabled,
                "interval_seconds": interval,
                "symbols_scanned": symbols_scanned,
                "signals_detected": signals_detected,
                "executed": iteration_executed,
                "skipped": iteration_skipped,
                "manual_candidates": iteration_manual_candidates,
                "skip_reason_summary": summarize_skip_reasons(iteration_skipped),
            }
        )
        if iteration_report_rows:
            report_path = write_intraday_scan_report(
                report_rows=iteration_report_rows,
                scan_time=loop_time,
                report_dir=SETTINGS.paths.reports_dir,
            )
            alerter.upload_file(
                report_path,
                title=f"Intraday Scan {loop_time.strftime('%Y-%m-%d %H:%M')}",
                initial_comment=(
                    "Intraday scan report\n"
                    f"Scanned: {symbols_scanned} | Signals: {signals_detected} | "
                    f"Executed: {len(iteration_executed)} | Skipped: {len(iteration_skipped)}"
                ),
            )

        if management.get("kill_switch"):
            break

        if interval <= 0:
            break

        next_run = next_scan_time(loop_time, interval)
        if not market_data.market_is_open(now_fn()):
            break
        sleep_until(next_run, now_fn, sleep_fn)

    append_markdown_log(
        SETTINGS.paths.trade_log,
        "Intraday Actions",
        {
            "actions": actions_total or "none",
            "executed": executed_total or "none",
            "skipped": skipped_total or "none",
            "manual_candidates": manual_candidates_total or "none",
            "scan_count": len(scans),
            "skip_reason_summary": summarize_skip_reasons(skipped_total) or "none",
        },
    )
    append_workflow_snapshot(
        SETTINGS.paths.research_log,
        "Intraday",
        {
            "actions": actions_total,
            "executed": executed_total,
            "skipped": skipped_total,
            "manual_candidates": manual_candidates_total,
            "tracked_positions": tracked_positions,
            "scans": serialize_scan_results(scans),
        },
    )
    return actions_total
