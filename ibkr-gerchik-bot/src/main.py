"""Application entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.alerts.slack import SlackAlerter
from src.brokers.ibkr import IBKRClient
from src.config import LOGGER, SETTINGS, ensure_directories
from src.data.market_data import MarketDataService
from src.data.news import NewsService
from src.data.news_filter import NewsRiskFilter
from src.execution.order_manager import OrderManager
from src.git_workflow import maybe_commit_and_push
from src.jobs.eod import run_eod
from src.jobs.intraday import run_intraday
from src.jobs.open import run_open
from src.jobs.premarket import run_premarket
from src.jobs.session_utils import (
    calculate_open_risk_amount,
    can_scan_for_new_entries,
    get_scan_interval,
    job_loop_lock,
    next_scan_time,
    session_now,
)
from src.jobs.weekly import run_weekly
from src.risk.kill_switch import should_trigger_kill_switch
from src.risk.risk_manager import RiskManager
from src.risk.take_profit import reward_risk_ratio
from src.slack_commands import SlackCommandProcessor
from src.strategy.signal_models import TradeSignal
from src.workflow_log import append_workflow_snapshot, read_latest_workflow_snapshot


def _build_research_symbols(positions: List[Dict[str, object]]) -> List[str]:
    merged: List[str] = []
    seen: set[str] = set()

    for item in positions:
        sec_type = str(item.get("sec_type", "")).strip().upper()
        symbol = str(item.get("symbol", "")).strip().upper()
        if not symbol:
            continue
        normalized = symbol
        if sec_type == "CASH" and symbol != SETTINGS.account_currency.upper():
            normalized = f"{symbol}.{SETTINGS.account_currency.upper()}"
        elif sec_type and sec_type != "STK":
            continue
        if normalized not in seen:
            seen.add(normalized)
            merged.append(normalized)

    for symbol in SETTINGS.symbols:
        normalized = symbol.strip().upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            merged.append(normalized)

    return merged


def _sync_tracked_positions_with_broker(
    tracked_positions: List[Dict[str, object]],
    broker_positions: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    existing_by_symbol: Dict[str, Dict[str, object]] = {}
    for position in tracked_positions:
        symbol = str(position.get("symbol", "")).strip().upper()
        if symbol:
            existing_by_symbol[symbol] = dict(position)

    synced: List[Dict[str, object]] = []
    seen: set[str] = set()
    for broker_position in broker_positions:
        sec_type = str(broker_position.get("sec_type", "")).strip().upper()
        if sec_type != "STK":
            continue

        symbol = str(broker_position.get("symbol", "")).strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)

        quantity = abs(int(float(broker_position.get("position", 0.0) or 0.0)))
        avg_cost = float(broker_position.get("avg_cost", 0.0) or 0.0)
        direction = "long" if float(broker_position.get("position", 0.0) or 0.0) >= 0 else "short"

        base_position = existing_by_symbol.get(symbol, {})
        merged_position = dict(base_position)
        merged_position.update(
            {
                "symbol": symbol,
                "quantity": quantity,
                "entry": float(base_position.get("entry", avg_cost) or avg_cost),
                "avg_cost": avg_cost,
                "direction": str(base_position.get("direction", direction) or direction),
                "sec_type": sec_type,
            }
        )
        synced.append(merged_position)

    return synced


def _account_equity_from_summary(summary: List[Dict[str, object]]) -> float:
    for item in summary:
        if item.get("tag") == "NetLiquidation":
            return float(item.get("value", 0.0))
    return 100_000.0


def _cash_from_summary(summary: List[Dict[str, object]]) -> float:
    for item in summary:
        if item.get("tag") in {"AvailableFunds", "TotalCashValue"}:
            return float(item.get("value", 0.0))
    return _account_equity_from_summary(summary)


def _load_state(state_path: Path) -> Dict[str, object]:
    if not state_path.exists():
        return {"watchlist": {}, "tracked_positions": [], "weekly_results": []}
    try:
        with state_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError:
        LOGGER.warning("State file is invalid or temporarily empty: %s. Falling back to default state.", state_path)
        return {"watchlist": {}, "tracked_positions": [], "weekly_results": []}


def _save_state(state_path: Path, payload: Dict[str, object]) -> None:
    temp_path = state_path.with_suffix(f"{state_path.suffix}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(temp_path, state_path)


def _hydrate_from_logs(state: Dict[str, object]) -> Dict[str, object]:
    """Recover workflow context from the research log if the state file is incomplete."""
    hydrated = dict(state)
    if not hydrated.get("watchlist"):
        premarket = read_latest_workflow_snapshot(SETTINGS.paths.research_log, "Premarket")
        if premarket and isinstance(premarket.get("watchlist"), dict):
            hydrated["watchlist"] = premarket["watchlist"]
    if not hydrated.get("tracked_positions"):
        intraday = read_latest_workflow_snapshot(SETTINGS.paths.research_log, "Intraday")
        if intraday and isinstance(intraday.get("tracked_positions"), list):
            hydrated["tracked_positions"] = intraday["tracked_positions"]
        else:
            open_snapshot = read_latest_workflow_snapshot(SETTINGS.paths.research_log, "Open")
            if open_snapshot and isinstance(open_snapshot.get("executed"), list):
                hydrated["tracked_positions"] = open_snapshot["executed"]
    return hydrated


def _duplicate_session_message(job_name: str) -> str:
    """Build a short Slack/operator message for blocked duplicate session jobs."""
    now = session_now()
    interval = get_scan_interval(now)
    if job_name == "open":
        if interval > 0:
            next_run = next_scan_time(now, interval)
            return f"{job_name.title()} already running; next scan at ~{next_run.strftime('%I:%M %p ET')}."
        return f"{job_name.title()} already running."

    if job_name == "intraday":
        if can_scan_for_new_entries(now) and interval > 0:
            next_run = next_scan_time(now, interval)
            return f"Intraday already running; next scheduled scan at ~{next_run.strftime('%I:%M %p ET')}."
        return "Intraday already running; new entries are currently disabled, position management remains active."

    return f"{job_name.title()} already running."


def _infer_manual_watch_signal(entry: float, stop: float, target: float) -> str:
    if target > entry and stop < entry:
        return "BUY"
    if target < entry and stop > entry:
        return "SELL"
    raise ValueError("Unable to infer signal direction from entry/stop/target. Pass --signal explicitly.")


def _build_manual_watch_trade_signal(
    *,
    symbol: str,
    entry: float,
    stop: float,
    target: float,
    signal: Optional[str],
    level_price: Optional[float],
    level_type: str,
    strategy: str,
) -> TradeSignal:
    normalized_signal = (signal or _infer_manual_watch_signal(entry, stop, target)).strip().upper()
    if normalized_signal not in {"BUY", "SELL"}:
        raise ValueError("Signal must be BUY or SELL.")
    direction = "long" if normalized_signal == "BUY" else "short"
    resolved_level_price = float(level_price) if level_price is not None else float(stop)
    return TradeSignal(
        symbol=symbol.strip().upper(),
        strategy=strategy,
        signal=normalized_signal,
        direction=direction,
        entry=float(entry),
        stop=float(stop),
        target=float(target),
        level_price=resolved_level_price,
        level_type=level_type,
        nearest_upper_level=float(target) if normalized_signal == "BUY" else None,
        nearest_lower_level=float(target) if normalized_signal == "SELL" else None,
        reward_risk=round(reward_risk_ratio(float(entry), float(stop), float(target)), 2),
        partial_targets=[{"qty_pct": 1.0, "price": float(target)}],
        notes=["manual_watch_job"],
    )


def run_manual_watch(
    *,
    symbol: str,
    entry: float,
    stop: float,
    target: float,
    signal: Optional[str] = None,
    level_price: Optional[float] = None,
    level_type: str = "manual_watch",
    strategy: str = "manual_watch",
    execute: bool = False,
    allow_after_hours: bool = False,
    auto_cancel_seconds: int = 0,
) -> Dict[str, object]:
    ensure_directories()
    state_path = SETTINGS.paths.state_file
    state = _hydrate_from_logs(_load_state(state_path))
    alerter = SlackAlerter()
    dry_run = not execute
    if allow_after_hours and not SETTINGS.paper_trading:
        raise ValueError("--allow-after-hours is supported only when PAPER_TRADING=true.")
    broker = IBKRClient()
    try:
        broker.connect()
        news_service = NewsService(broker=broker)
        news_filter = NewsRiskFilter(news_service)
        market_data = MarketDataService(broker)
        order_manager = OrderManager(broker, market_data, alerter, news_filter, dry_run=dry_run)
        account_summary = broker.get_account_summary()
        account_equity = _account_equity_from_summary(account_summary)
        cash_available = _cash_from_summary(account_summary)
        positions = broker.get_positions()
        open_orders = broker.get_open_orders()
        state["tracked_positions"] = _sync_tracked_positions_with_broker(
            state.get("tracked_positions", []),
            positions,
        )
        account_snapshot = {"account": account_summary, "positions": positions, "open_orders": open_orders}
        _save_state(state_path, state)

        trade_signal = _build_manual_watch_trade_signal(
            symbol=symbol,
            entry=entry,
            stop=stop,
            target=target,
            signal=signal,
            level_price=level_price,
            level_type=level_type,
            strategy=strategy,
        )
        success, payload = order_manager.execute_trade(
            trade_signal,
            account_equity=account_equity,
            cash_available=cash_available,
            current_positions=positions,
            open_risk_amount=calculate_open_risk_amount(state.get("tracked_positions", [])),
            allow_after_hours=allow_after_hours,
            allow_extended_hours_order=allow_after_hours,
            time_in_force="GTC" if allow_after_hours else None,
        )

        if success:
            tracked_positions = state.get("tracked_positions", [])
            if not payload.get("dry_run", False):
                tracked_positions.append(
                    {
                        "symbol": trade_signal.symbol,
                        "quantity": int(payload.get("quantity", 0) or 0),
                        "entry": float(payload.get("entry", 0.0) or 0.0),
                        "avg_cost": float(payload.get("entry", 0.0) or 0.0),
                        "direction": trade_signal.direction,
                        "sec_type": "STK",
                        "stop_order_id": int(payload.get("stop_order_id", 0) or 0),
                    }
                )
                state["tracked_positions"] = tracked_positions
                _save_state(state_path, state)

                if auto_cancel_seconds > 0:
                    time.sleep(auto_cancel_seconds)
                    cancel_ids = [
                        int(payload.get("limit_order_id", 0) or 0),
                        int(payload.get("stop_order_id", 0) or 0),
                        int(payload.get("market_order_id", 0) or 0),
                    ]
                    cancelled_order_ids: List[int] = []
                    for order_id in cancel_ids:
                        if order_id <= 0:
                            continue
                        if broker.cancel_order(order_id):
                            cancelled_order_ids.append(order_id)
                    if allow_after_hours and int(payload.get("market_order_id", 0) or 0) in cancelled_order_ids:
                        state["tracked_positions"] = [
                            item for item in state.get("tracked_positions", []) if str(item.get("symbol", "")).upper() != trade_signal.symbol
                        ]
                        _save_state(state_path, state)
                    payload["auto_cancel"] = {
                        "requested_after_seconds": int(auto_cancel_seconds),
                        "cancelled_order_ids": cancelled_order_ids,
                    }

        result = {
            "job": "manual_watch",
            "symbol": trade_signal.symbol,
            "dry_run": dry_run,
            "success": success,
            "account_snapshot": account_snapshot,
            "request": {
                "symbol": trade_signal.symbol,
                "signal": trade_signal.signal,
                "entry": trade_signal.entry,
                "stop": trade_signal.stop,
                "target": trade_signal.target,
                "level_price": trade_signal.level_price,
                "level_type": trade_signal.level_type,
                "strategy": trade_signal.strategy,
                "allow_after_hours": allow_after_hours,
                "auto_cancel_seconds": int(auto_cancel_seconds),
            },
            "result": payload,
        }
        append_workflow_snapshot(SETTINGS.paths.trade_log, "ManualWatch", result)
        return result
    finally:
        broker.disconnect()


def run_job(job_name: str, dry_run_override: Optional[bool] = None) -> Dict[str, object]:
    ensure_directories()
    state_path = SETTINGS.paths.state_file
    state = _hydrate_from_logs(_load_state(state_path))
    alerter = SlackAlerter()
    dry_run = SETTINGS.dry_run_mode if dry_run_override is None else dry_run_override

    if job_name == "slack":
        result = SlackCommandProcessor(alerter).process(state, run_job)
        _save_state(state_path, state)
        return {"job": job_name, **result}

    if job_name == "weekly":
        metrics = run_weekly(state.get("weekly_results", []))
        return {"job": job_name, "metrics": metrics, "dry_run": dry_run}

    preconnect_lock_name = None
    if job_name == "open":
        preconnect_lock_name = "open_session"
    elif job_name == "intraday":
        preconnect_lock_name = "intraday_session"

    if preconnect_lock_name is not None:
        with job_loop_lock(preconnect_lock_name) as acquired:
            if not acquired:
                LOGGER.info("Skipping %s job because %s is already active.", job_name, preconnect_lock_name)
                SlackAlerter().send_channel_message(_duplicate_session_message(job_name))
                return {"job": job_name, "blocked": True, "reasons": ["session_already_running"], "dry_run": dry_run}
            return _run_connected_job(job_name, state_path, state, dry_run)

    return _run_connected_job(job_name, state_path, state, dry_run)


def _run_connected_job(
    job_name: str,
    state_path: Path,
    state: Dict[str, object],
    dry_run: bool,
) -> Dict[str, object]:
    """Run broker-connected jobs after state and locking checks have passed."""
    alerter = SlackAlerter()
    broker = IBKRClient()
    try:
        broker.connect()
        news_service = NewsService(broker=broker)
        news_filter = NewsRiskFilter(news_service)
        market_data = MarketDataService(broker)
        order_manager = OrderManager(broker, market_data, alerter, news_filter, dry_run=dry_run)
        account_summary = broker.get_account_summary()
        account_equity = _account_equity_from_summary(account_summary)
        cash_available = _cash_from_summary(account_summary)
        positions = broker.get_positions()
        open_orders = broker.get_open_orders()
        state["tracked_positions"] = _sync_tracked_positions_with_broker(
            state.get("tracked_positions", []),
            positions,
        )
        research_symbols = _build_research_symbols(positions)
        risk_manager = RiskManager(account_equity)
        repo_root = SETTINGS.paths.trade_log.parents[1]
        account_snapshot = {"account": account_summary, "positions": positions, "open_orders": open_orders}
        _save_state(state_path, state)

        internal_positions = state.get("tracked_positions", [])
        kill_switch, reasons = should_trigger_kill_switch(
            account_equity=account_equity,
            daily_realized_pnl=0.0,
            connection_healthy=broker.is_connected,
            broker_positions=positions,
            internal_positions=internal_positions,
            macro_risk=news_filter.is_macro_risk(),
        )
        if kill_switch and job_name in {"open", "intraday"}:
            alerter.send_error(f"Kill switch engaged: {', '.join(reasons)}")
            return {"job": job_name, "blocked": True, "reasons": reasons, "dry_run": dry_run}

        if job_name == "premarket":
            premarket_summary = run_premarket(
                market_data,
                news_service,
                news_filter,
                account_snapshot,
                research_symbols,
            )
            watchlist = premarket_summary.get("watchlist", {})
            state["watchlist"] = watchlist
            _save_state(state_path, state)
            alerter.send_premarket_summary(premarket_summary)
            report_path = premarket_summary.get("report_path")
            if isinstance(report_path, str) and report_path:
                uploaded = alerter.upload_file(
                    Path(report_path),
                    title="Premarket Strong Levels",
                    initial_comment="Daily premarket levels report (strength_score > 7).",
                )
                if not uploaded:
                    alerter.send(f"Premarket levels report generated: {Path(report_path).name}")
            maybe_commit_and_push(
                repo_root,
                [SETTINGS.paths.research_log, SETTINGS.paths.state_file],
                "workflow: premarket research update",
            )
            return {"job": job_name, "watchlist_count": len(watchlist), "dry_run": dry_run}

        if job_name == "open":
            if not state.get("watchlist"):
                premarket_summary = run_premarket(
                    market_data,
                    news_service,
                    news_filter,
                    account_snapshot,
                    research_symbols,
                )
                watchlist = premarket_summary.get("watchlist", {})
                state["watchlist"] = watchlist
            else:
                watchlist = state.get("watchlist", {})
            tracked_positions = state.get("tracked_positions", [])
            risk_manager.update_open_risk(tracked_positions)
            executed = run_open(
                market_data=market_data,
                order_manager=order_manager,
                news_filter=news_filter,
                watchlist=watchlist,
                account_equity=account_equity,
                cash_available=cash_available,
                current_positions=positions,
                open_risk_amount=float(risk_manager.get_state()["open_risk_amount"]),
            )
            tracked_positions.extend(executed)
            state["tracked_positions"] = tracked_positions
            _save_state(state_path, state)
            if executed:
                maybe_commit_and_push(
                    repo_root,
                    [SETTINGS.paths.trade_log, SETTINGS.paths.research_log, SETTINGS.paths.state_file],
                    "workflow: market open trades",
                )
            return {"job": job_name, "executed": executed, "dry_run": dry_run}

        if job_name == "intraday":
            tracked_positions = state.get("tracked_positions", [])
            actions = run_intraday(
                broker,
                alerter,
                news_filter,
                tracked_positions,
                account_equity=account_equity,
                dry_run=dry_run,
            )
            state["tracked_positions"] = tracked_positions
            _save_state(state_path, state)
            if actions:
                maybe_commit_and_push(
                    repo_root,
                    [SETTINGS.paths.trade_log, SETTINGS.paths.research_log, SETTINGS.paths.state_file],
                    "workflow: intraday adjustments",
                )
            return {"job": job_name, "actions": actions, "dry_run": dry_run}

        if job_name == "eod":
            summary = run_eod(risk_manager, account_snapshot, state.get("tracked_positions", []), daily_pnl=0.0, alerter=alerter)
            maybe_commit_and_push(
                repo_root,
                [SETTINGS.paths.trade_log, SETTINGS.paths.state_file],
                "workflow: end of day snapshot",
            )
            return {"job": job_name, "summary": summary, "dry_run": dry_run}

        raise ValueError(f"Unsupported job '{job_name}'")
    finally:
        broker.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IBKR Gerchik bot job runner.")
    parser.add_argument("--job", required=True, choices=["premarket", "open", "intraday", "eod", "weekly", "slack", "manual_watch"])
    parser.add_argument("--dry-run", action="store_true", help="Simulate trades without placing broker orders.")
    parser.add_argument("--symbol", help="Ticker symbol for manual_watch jobs.")
    parser.add_argument("--entry", type=float, help="Entry price for manual_watch jobs.")
    parser.add_argument("--stop", type=float, help="Stop price for manual_watch jobs.")
    parser.add_argument("--target", type=float, help="Target price for manual_watch jobs.")
    parser.add_argument("--signal", choices=["BUY", "SELL"], help="Optional explicit side for manual_watch jobs.")
    parser.add_argument("--level-price", type=float, help="Optional reference level price for manual_watch jobs.")
    parser.add_argument("--level-type", default="manual_watch", help="Reference level type for manual_watch jobs.")
    parser.add_argument("--strategy", default="manual_watch", help="Strategy label for manual_watch jobs.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="For manual_watch only: actually submit the order. Without this flag the job runs in simulation mode.",
    )
    parser.add_argument(
        "--allow-after-hours",
        action="store_true",
        help="For manual_watch only: allow paper-order submission outside regular market hours.",
    )
    parser.add_argument(
        "--auto-cancel-seconds",
        type=int,
        default=0,
        help="For manual_watch only: cancel submitted bracket orders after the given number of seconds.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.job == "manual_watch":
            missing = [
                name
                for name, value in {
                    "symbol": args.symbol,
                    "entry": args.entry,
                    "stop": args.stop,
                    "target": args.target,
                }.items()
                if value is None
            ]
            if missing:
                raise ValueError(f"manual_watch requires: {', '.join(missing)}")
            result = run_manual_watch(
                symbol=str(args.symbol),
                entry=float(args.entry),
                stop=float(args.stop),
                target=float(args.target),
                signal=args.signal,
                level_price=args.level_price,
                level_type=str(args.level_type),
                strategy=str(args.strategy),
                execute=bool(args.execute),
                allow_after_hours=bool(args.allow_after_hours),
                auto_cancel_seconds=max(int(args.auto_cancel_seconds or 0), 0),
            )
        else:
            result = run_job(args.job, dry_run_override=True if args.dry_run else None)
        LOGGER.info("Job completed: %s", result)
        return 0
    except Exception as exc:  # pragma: no cover - top-level safety net.
        LOGGER.exception("Fatal error while running job '%s'.", args.job)
        try:
            SlackAlerter().send_error(str(exc))
        except Exception:
            LOGGER.exception("Slack error notification failed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
