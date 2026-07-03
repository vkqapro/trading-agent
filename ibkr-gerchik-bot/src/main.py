"""Application entry point."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter
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
from src.execution import order_requests as order_requests_store
from src.jobs.eod import run_eod
from src.jobs.execute_requests import run_execute_requests
from src.jobs.intraday import run_intraday
from src.jobs.market_data_collector import run_market_data_collector
from src.jobs.open import run_open
from src.jobs.premarket import run_premarket
from src.jobs.session_utils import (
    calculate_open_risk_amount,
    can_scan_for_new_entries,
    get_scan_interval,
    job_loop_lock,
    next_scan_time,
    session_now,
    sync_tracked_positions_with_broker,
)
from src.jobs.symbol_onboarding import run_symbol_onboarding, run_symbol_onboarding_from_saved_bars
from src.jobs.weekly import run_weekly
from src.risk.kill_switch import should_trigger_kill_switch
from src.risk.risk_manager import RiskManager
from src.risk.take_profit import reward_risk_ratio
from src.slack_commands import SlackCommandProcessor
from src.strategy.atr import atr_travel_filter, technical_atr_has_room
from src.strategy.levels import Level
from src.strategy.signal_models import TradeSignal
from src.strategy.strategy_router import route_strategies
from src.workflow_log import append_workflow_snapshot, read_latest_workflow_snapshot

BUILD_VERSION_MARKER = "2026-05-21-market-session-lock-v1"
MARKET_SESSION_LOCK_NAME = "market_session"
STATE_SAVE_RETRY_ATTEMPTS = 8
STATE_SAVE_RETRY_DELAY_SECONDS = 0.1


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
    last_error: Optional[OSError] = None
    for attempt in range(STATE_SAVE_RETRY_ATTEMPTS):
        try:
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            os.replace(temp_path, state_path)
            return
        except OSError as exc:
            last_error = exc
            if getattr(exc, "winerror", None) not in {5, 32} or attempt == STATE_SAVE_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(STATE_SAVE_RETRY_DELAY_SECONDS * (attempt + 1))

    if last_error is not None:
        raise last_error


def _merge_onboarded_watchlist(
    state: Dict[str, object],
    onboarding: Dict[str, object],
    latest_state: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    if isinstance(latest_state, dict) and latest_state:
        state.clear()
        state.update(latest_state)
    existing_watchlist = state.get("watchlist", {})
    if not isinstance(existing_watchlist, dict):
        existing_watchlist = {}
    onboarded_watchlist = onboarding.get("watchlist", {})
    if isinstance(onboarded_watchlist, dict):
        existing_watchlist.update(onboarded_watchlist)
    state["watchlist"] = existing_watchlist
    return existing_watchlist


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


def _market_session_busy_message(job_name: str, wait_seconds: int) -> str:
    """Build a non-fatal operator message when the shared IBKR session remains busy."""
    wait_minutes = max(round(wait_seconds / 60), 1)
    if job_name == "intraday":
        return (
            f"Intraday waited {wait_minutes} min for the market/open session to finish, "
            "but IBKR is still busy. The next scheduled intraday run can try again."
        )
    return f"{job_name.title()} skipped because another market session is already using IBKR."


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



def _validate_watchlist_once(
    *,
    watchlist: Dict[str, object],
    market_data: MarketDataService,
    order_manager: OrderManager,
    news_filter: NewsRiskFilter,
    account_equity: float,
    cash_available: float,
    current_positions: List[Dict[str, object]],
    open_risk_amount: float,
    allow_after_hours: bool,
) -> Dict[str, object]:
    """Evaluate whether any current watchlist symbols would pass full order validation."""
    summary = Counter()
    reason_counts = Counter()
    placeable: List[Dict[str, object]] = []
    sample_rejections: List[Dict[str, object]] = []
    macro_context = news_filter.get_macro_risk_context()

    summary["watchlist_count"] = len(watchlist)
    summary["symbols_seen"] = len(watchlist)
    if macro_context.get("risk_level") == "HIGH":
        summary["macro_blocked"] = 1

    working_positions = [dict(item) for item in current_positions]
    working_open_risk_amount = float(open_risk_amount)

    for symbol, plan in watchlist.items():
        symbol_news_context = news_filter.get_symbol_risk_context(symbol)
        if symbol_news_context.get("risk_level") == "HIGH":
            summary["symbol_news_blocked"] += 1
            reason_counts["symbol_news_risk"] += 1
            continue

        intraday_bars = market_data.get_intraday_bars(
            symbol,
            duration=SETTINGS.strategy.intraday_bar_duration,
            bar_size=SETTINGS.strategy.intraday_bar_size,
        )
        quote = market_data.get_quote(symbol)
        if intraday_bars.empty or float(quote.get("last", 0.0) or 0.0) <= 0:
            summary["missing_live_data"] += 1
            reason_counts["missing_live_data"] += 1
            continue

        levels = [Level(**level) for level in plan.get("levels", [])]
        candidate_signals = route_strategies(symbol, intraday_bars, levels, news_context=symbol_news_context, persist=False)
        if not candidate_signals:
            summary["no_signal"] += 1
            reason_counts["no_signal"] += 1
            continue

        summary["symbols_with_signal"] += 1
        session_low = float(intraday_bars["low"].min())
        session_high = float(intraday_bars["high"].max())
        placed_this_symbol = False

        for signal in candidate_signals:
            atr_ok = technical_atr_has_room(float(plan.get("technical_atr", 0.0)), signal.entry)
            trend_ok = atr_travel_filter(
                signal.entry,
                session_low,
                session_high,
                float(plan.get("daily_atr", 0.0)),
                bool(signal.is_new_extreme),
            )
            if not atr_ok or not trend_ok:
                summary["atr_filtered"] += 1
                reason_counts["atr_filter"] += 1
                continue

            success, payload = order_manager.execute_trade(
                signal,
                account_equity=account_equity,
                cash_available=cash_available,
                current_positions=working_positions,
                open_risk_amount=working_open_risk_amount,
                allow_after_hours=allow_after_hours,
                allow_extended_hours_order=allow_after_hours,
                time_in_force="GTC" if allow_after_hours else None,
            )
            if success:
                placed_this_symbol = True
                summary["placeable"] += 1
                placeable.append(
                    {
                        "symbol": symbol,
                        "strategy": payload.get("strategy"),
                        "direction": payload.get("direction"),
                        "entry": payload.get("entry"),
                        "stop_loss": payload.get("stop_loss"),
                        "target": payload.get("target"),
                        "quantity": payload.get("quantity"),
                        "reward_risk": payload.get("reward_risk"),
                        "simulated": bool(payload.get("dry_run", False)),
                    }
                )
                working_positions.append({"symbol": symbol})
                working_open_risk_amount += abs(float(payload["entry"]) - float(payload["stop_loss"])) * float(payload["quantity"])
                break

            summary["candidate_rejected"] += 1
            reasons = [str(item) for item in payload.get("reasons", ["rejected"])]
            for reason in reasons:
                reason_counts[reason] += 1
            if len(sample_rejections) < 12:
                sample_rejections.append(
                    {
                        "symbol": symbol,
                        "strategy": signal.strategy,
                        "reasons": reasons,
                        "reward_risk": payload.get("signal", {}).get("reward_risk"),
                        "quantity": payload.get("signal", {}).get("quantity"),
                    }
                )

        if not placed_this_symbol and candidate_signals:
            summary["symbols_without_placeable_signal"] += 1

    return {
        "watchlist_count": int(summary["watchlist_count"]),
        "macro_risk_level": str(macro_context.get("risk_level", "UNKNOWN")),
        "summary": {
            "symbols_seen": int(summary["symbols_seen"]),
            "macro_blocked": int(summary["macro_blocked"]),
            "symbol_news_blocked": int(summary["symbol_news_blocked"]),
            "missing_live_data": int(summary["missing_live_data"]),
            "no_signal": int(summary["no_signal"]),
            "symbols_with_signal": int(summary["symbols_with_signal"]),
            "atr_filtered": int(summary["atr_filtered"]),
            "candidate_rejected": int(summary["candidate_rejected"]),
            "placeable": int(summary["placeable"]),
            "symbols_without_placeable_signal": int(summary["symbols_without_placeable_signal"]),
        },
        "reason_counts": dict(reason_counts.most_common()),
        "placeable": placeable,
        "sample_rejections": sample_rejections,
    }


def _quote_check_once(symbol: str, market_data: MarketDataService) -> Dict[str, object]:
    """Report quote spread details for a single symbol against the configured limit."""
    def _finite_or_zero(value: object) -> float:
        try:
            numeric = float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return numeric if math.isfinite(numeric) else 0.0

    normalized_symbol = symbol.strip().upper()
    quote = market_data.get_quote(normalized_symbol)
    bid = _finite_or_zero(quote.get("bid", 0.0))
    ask = _finite_or_zero(quote.get("ask", 0.0))
    last = _finite_or_zero(quote.get("last", 0.0))
    mid = ((bid + ask) / 2.0) if bid > 0 and ask > 0 else last
    spread = abs(ask - bid) if bid > 0 and ask > 0 else 0.0
    spread_pct = (spread / mid) if mid > 0 else 1.0
    max_spread_dollars = (mid * SETTINGS.risk.max_spread_pct) if mid > 0 else 0.0
    return {
        "job": "quote_check",
        "symbol": normalized_symbol,
        "bid": bid,
        "ask": ask,
        "last": last,
        "mid": round(mid, 6),
        "spread": round(spread, 6),
        "spread_pct": round(spread_pct, 6),
        "spread_pct_percent": round(spread_pct * 100.0, 4),
        "max_spread_pct": SETTINGS.risk.max_spread_pct,
        "max_spread_pct_percent": round(SETTINGS.risk.max_spread_pct * 100.0, 4),
        "max_spread_dollars": round(max_spread_dollars, 6),
        "passes_spread_filter": spread_pct <= SETTINGS.risk.max_spread_pct,
    }


def _log_job_start(job_name: str, *, dry_run: bool, command_context: Optional[Dict[str, object]] = None) -> None:
    """Write an explicit startup marker so operators can identify the running code generation."""
    context_summary = ""
    if command_context:
        visible_items = ", ".join(
            f"{key}={value}"
            for key, value in sorted(command_context.items())
            if value not in {None, ""}
        )
        if visible_items:
            context_summary = f" context={visible_items}"
    LOGGER.info(
        "Job startup marker: build=%s job=%s dry_run=%s pid=%s%s",
        BUILD_VERSION_MARKER,
        job_name,
        dry_run,
        os.getpid(),
        context_summary,
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
    _log_job_start(
        "manual_watch",
        dry_run=dry_run,
        command_context={
            "symbol": symbol.strip().upper(),
            "execute": execute,
            "allow_after_hours": allow_after_hours,
        },
    )
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
        state["tracked_positions"] = sync_tracked_positions_with_broker(
            state.get("tracked_positions", []),
            positions,
            open_orders,
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
    return run_job_with_context(job_name, dry_run_override=dry_run_override, command_context=None)


def run_job_with_context(
    job_name: str,
    dry_run_override: Optional[bool] = None,
    command_context: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    ensure_directories()
    state_path = SETTINGS.paths.state_file
    state = _hydrate_from_logs(_load_state(state_path))
    alerter = SlackAlerter()
    dry_run = SETTINGS.dry_run_mode if dry_run_override is None else dry_run_override
    _log_job_start(job_name, dry_run=dry_run, command_context=command_context)

    if job_name == "slack":
        result = SlackCommandProcessor(alerter).process(state, run_job_with_context)
        _save_state(state_path, state)
        return {"job": job_name, **result}

    if job_name == "weekly":
        metrics = run_weekly(state.get("weekly_results", []))
        return {"job": job_name, "metrics": metrics, "dry_run": dry_run}

    if job_name == "onboard_symbol":
        symbol = str((command_context or {}).get("symbol", "") or "").strip().upper()
        if not symbol:
            raise ValueError("onboard_symbol requires: symbol")
        saved_onboarding = run_symbol_onboarding_from_saved_bars(symbol=symbol)
        if saved_onboarding.get("watchlist"):
            existing_watchlist = _merge_onboarded_watchlist(
                state,
                saved_onboarding,
                latest_state=_load_state(state_path),
            )
            _save_state(state_path, state)
            return {
                "job": job_name,
                "symbol": saved_onboarding.get("symbol", symbol),
                "ready": saved_onboarding.get("ready"),
                "reason": saved_onboarding.get("reason"),
                "levels": saved_onboarding.get("levels"),
                "raw_levels": saved_onboarding.get("raw_levels"),
                "watchlist_count": len(existing_watchlist),
                "chart_history": saved_onboarding.get("chart_history"),
                "dry_run": dry_run,
            }
        LOGGER.info(
            "Saved bars are not sufficient for %s onboarding; falling back to IBKR fetch. reason=%s",
            symbol,
            saved_onboarding.get("reason"),
        )

    if job_name == "execute_requests" and order_requests_store.worker_is_alive():
        # A fresh heartbeat means another worker already owns the IBKR connection.
        # Refuse instead of starting a second one that would collide and let the
        # stale instance keep winning.
        age = order_requests_store.worker_age_seconds()
        LOGGER.warning("Execute worker already running (heartbeat %.0fs old); not starting another.", age or 0.0)
        return {"job": job_name, "blocked": True, "reasons": ["worker_already_running"], "dry_run": dry_run}

    if job_name == "market_data":
        with job_loop_lock("market_data_collector") as acquired:
            if not acquired:
                LOGGER.info("Skipping market_data because the collector is already active.")
                return {
                    "job": job_name,
                    "blocked": True,
                    "reasons": ["collector_already_running"],
                    "dry_run": True,
                }
            return _run_connected_job(
                job_name,
                state_path,
                state,
                True,
                command_context=command_context,
            )

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

            market_lock_wait_seconds = (
                SETTINGS.broker.market_session_lock_wait_seconds
                if job_name == "intraday"
                else 0
            )
            with job_loop_lock(
                MARKET_SESSION_LOCK_NAME,
                wait_timeout_seconds=market_lock_wait_seconds,
                retry_delay_seconds=SETTINGS.broker.startup_retry_delay_seconds,
            ) as market_acquired:
                if not market_acquired:
                    LOGGER.warning(
                        "Skipping %s job because %s remained active after %ss.",
                        job_name,
                        MARKET_SESSION_LOCK_NAME,
                        market_lock_wait_seconds,
                    )
                    SlackAlerter().send_channel_message(
                        _market_session_busy_message(job_name, market_lock_wait_seconds)
                    )
                    return {
                        "job": job_name,
                        "blocked": True,
                        "reasons": ["market_session_busy"],
                        "dry_run": dry_run,
                    }
                return _run_connected_job(job_name, state_path, state, dry_run, command_context=command_context)

    return _run_connected_job(job_name, state_path, state, dry_run, command_context=command_context)


def _run_connected_job(
    job_name: str,
    state_path: Path,
    state: Dict[str, object],
    dry_run: bool,
    *,
    command_context: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Run broker-connected jobs after state and locking checks have passed."""
    alerter = SlackAlerter()
    broker = _connect_broker_with_startup_retry(job_name)
    try:
        market_data = MarketDataService(broker)
        if job_name == "market_data":
            market_data.enable_delayed_fallback()
            watchlist = state.get("watchlist", {})
            if not isinstance(watchlist, dict) or not watchlist:
                watchlist = {symbol: {} for symbol in SETTINGS.symbols}
            collector = run_market_data_collector(
                market_data,
                watchlist,
                interval_seconds=int((command_context or {}).get("interval_seconds", 300) or 300),
                once=bool((command_context or {}).get("once", False)),
            )
            return {"job": job_name, **collector, "dry_run": True}

        news_service = NewsService(broker=broker)
        news_filter = NewsRiskFilter(news_service)
        order_manager = OrderManager(
            broker,
            market_data,
            alerter,
            news_filter,
            dry_run=dry_run,
            alert_on_manual_candidates=job_name != "validate_watchlist",
        )
        account_summary = broker.get_account_summary()
        account_equity = _account_equity_from_summary(account_summary)
        cash_available = _cash_from_summary(account_summary)
        positions = broker.get_positions()
        open_orders = broker.get_open_orders()
        state["tracked_positions"] = sync_tracked_positions_with_broker(
            state.get("tracked_positions", []),
            positions,
            open_orders,
        )
        research_symbols = _build_research_symbols(positions)
        risk_manager = RiskManager(account_equity)
        repo_root = SETTINGS.paths.trade_log.parents[1]
        account_snapshot = {"account": account_summary, "positions": positions, "open_orders": open_orders}
        _save_state(state_path, state)

        if job_name == "onboard_symbol":
            symbol = str((command_context or {}).get("symbol", "") or "").strip().upper()
            if not symbol:
                raise ValueError("onboard_symbol requires: symbol")
            onboarding = run_symbol_onboarding(
                symbol=symbol,
                market_data=market_data,
                news_service=news_service,
                news_filter=news_filter,
                account_snapshot=account_snapshot,
            )
            existing_watchlist = _merge_onboarded_watchlist(
                state,
                onboarding,
                latest_state=_load_state(state_path),
            )
            _save_state(state_path, state)
            return {
                "job": job_name,
                "symbol": onboarding.get("symbol", symbol),
                "ready": onboarding.get("ready"),
                "watchlist_count": len(existing_watchlist),
                "chart_history": onboarding.get("chart_history"),
                "dry_run": dry_run,
            }

        internal_positions = state.get("tracked_positions", [])
        kill_switch, reasons = should_trigger_kill_switch(
            account_equity=account_equity,
            daily_realized_pnl=0.0,
            connection_healthy=broker.is_connected,
            broker_positions=positions,
            internal_positions=internal_positions,
            macro_risk=news_filter.is_macro_risk(),
        )
        initial_intraday_halt_reasons: List[str] = []
        if kill_switch and job_name == "open":
            alerter.send_error(f"Kill switch engaged: {', '.join(reasons)}")
            return {"job": job_name, "blocked": True, "reasons": reasons, "dry_run": dry_run}
        if kill_switch and job_name == "intraday":
            initial_intraday_halt_reasons = [str(reason) for reason in reasons] or ["kill_switch"]
            alerter.send_error(
                "Kill switch engaged; trading disabled while market-data collection continues: "
                + ", ".join(initial_intraday_halt_reasons)
            )

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
            intraday_options = (
                {"initial_trading_halt_reasons": initial_intraday_halt_reasons}
                if initial_intraday_halt_reasons
                else {}
            )
            actions = run_intraday(
                broker,
                alerter,
                news_filter,
                tracked_positions,
                account_equity=account_equity,
                dry_run=dry_run,
                **intraday_options,
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

        if job_name == "validate_watchlist":
            if not state.get("watchlist"):
                premarket_summary = run_premarket(
                    market_data,
                    news_service,
                    news_filter,
                    account_snapshot,
                    research_symbols,
                )
                state["watchlist"] = premarket_summary.get("watchlist", {})
                _save_state(state_path, state)
            validation = _validate_watchlist_once(
                watchlist=state.get("watchlist", {}),
                market_data=market_data,
                order_manager=order_manager,
                news_filter=news_filter,
                account_equity=account_equity,
                cash_available=cash_available,
                current_positions=positions,
                open_risk_amount=calculate_open_risk_amount(state.get("tracked_positions", [])),
                allow_after_hours=True,
            )
            result = {"job": job_name, "dry_run": True, **validation}
            append_workflow_snapshot(SETTINGS.paths.trade_log, "ValidateWatchlist", result)
            return result

        if job_name == "quote_check":
            symbol = str((command_context or {}).get("symbol", "")).strip().upper()
            if not symbol:
                raise ValueError("quote_check requires a symbol.")
            return _quote_check_once(symbol, market_data)

        if job_name == "execute_requests":
            def _account_provider() -> tuple[float, float]:
                summary = broker.get_account_summary()
                return _account_equity_from_summary(summary), _cash_from_summary(summary)

            poll_seconds = float((command_context or {}).get("poll_seconds", 5.0) or 5.0)
            return run_execute_requests(
                broker,
                order_manager,
                state,
                save_state=lambda payload: _save_state(state_path, payload),
                account_provider=_account_provider,
                poll_seconds=poll_seconds,
            )

        raise ValueError(f"Unsupported job '{job_name}'")
    finally:
        broker.disconnect()


def _connect_broker_with_startup_retry(job_name: str) -> IBKRClient:
    """Create and connect an IBKR client, waiting through temporary startup contention."""
    retry_window = (
        SETTINGS.broker.startup_retry_window_seconds
        if job_name in {"intraday", "execute_requests", "market_data"}
        else 0
    )
    retry_delay = max(1, SETTINGS.broker.startup_retry_delay_seconds)
    deadline = time.monotonic() + max(0, retry_window)
    attempt = 0

    while True:
        attempt += 1
        broker = IBKRClient()
        try:
            broker.connect()
            if attempt > 1:
                LOGGER.info("Connected to IBKR after startup retry job=%s attempt=%s", job_name, attempt)
            return broker
        except ConnectionError as exc:
            try:
                broker.disconnect()
            except Exception:  # pragma: no cover - defensive cleanup for live broker edge cases.
                LOGGER.debug("Broker cleanup after failed startup connection raised.", exc_info=True)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                LOGGER.error(
                    "IBKR startup retry exhausted job=%s attempts=%s window_seconds=%s",
                    job_name,
                    attempt,
                    retry_window,
                )
                raise

            sleep_seconds = min(retry_delay, remaining)
            LOGGER.warning(
                "IBKR unavailable at %s startup; waiting %.0fs before retry attempt %s: %s",
                job_name,
                sleep_seconds,
                attempt + 1,
                exc,
            )
            time.sleep(sleep_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IBKR Gerchik bot job runner.")
    parser.add_argument("--job", required=True, choices=["premarket", "open", "intraday", "market_data", "eod", "weekly", "slack", "manual_watch", "validate_watchlist", "quote_check", "execute_requests", "onboard_symbol"])
    parser.add_argument("--dry-run", action="store_true", help="Simulate trades without placing broker orders.")
    parser.add_argument("--client-id", type=int, help="Override IBKR API client id for this process (avoid collisions).")
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
    parser.add_argument("--once", action="store_true", help="For market_data: collect one cycle and exit.")
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=300,
        help="For market_data: collection cadence while the market is open.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if getattr(args, "client_id", None) is not None:
        # Override the broker client id for this process so a long-running worker
        # never collides with the scanning bot's connection (BrokerConfig is frozen).
        object.__setattr__(SETTINGS.broker, "client_id", int(args.client_id))
        LOGGER.info("Using IBKR client id override=%s", args.client_id)
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
        elif args.job == "quote_check":
            if not args.symbol:
                raise ValueError("quote_check requires: symbol")
            result = run_job_with_context(
                "quote_check",
                dry_run_override=False,
                command_context={"symbol": str(args.symbol)},
            )
        elif args.job == "onboard_symbol":
            if not args.symbol:
                raise ValueError("onboard_symbol requires: symbol")
            result = run_job_with_context(
                "onboard_symbol",
                dry_run_override=True,
                command_context={"symbol": str(args.symbol)},
            )
        elif args.job == "market_data":
            result = run_job_with_context(
                "market_data",
                dry_run_override=True,
                command_context={
                    "once": bool(args.once),
                    "interval_seconds": max(60, int(args.interval_seconds)),
                },
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
