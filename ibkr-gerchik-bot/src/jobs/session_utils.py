"""Shared job helpers for session-aware scanning and position management."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, time as dt_time, timedelta
from typing import Callable, Dict, Iterator, List, Optional
from zoneinfo import ZoneInfo

from src.alerts.slack import SlackAlerter
from src.brokers.ibkr import IBKRClient
from src.config import LOGGER, SETTINGS, append_markdown_log
from src.data.market_data import MarketDataService
from src.data.news import NewsService
from src.data.news_filter import NewsRiskFilter
from src.execution.order_manager import OrderManager
from src.risk.kill_switch import should_trigger_kill_switch
from src.strategy.atr import atr_travel_filter, technical_atr_has_room
from src.strategy.levels import Level
from src.strategy.strategy_router import route_strategies
from src.workflow_log import append_workflow_snapshot, read_latest_workflow_snapshot


NowProvider = Callable[[], datetime]
SleepProvider = Callable[[float], None]

OPEN_SCAN_START = dt_time(hour=9, minute=35)
OPEN_SCAN_END = dt_time(hour=10, minute=30)
NO_NEW_ENTRY_AFTER = dt_time(hour=15, minute=45)
MARKET_CLOSE_TIME = dt_time(
    hour=SETTINGS.trading_hours.market_close_hour,
    minute=SETTINGS.trading_hours.market_close_minute,
)


def session_now() -> datetime:
    """Return timezone-aware market time."""
    return datetime.now(ZoneInfo(SETTINGS.trading_hours.timezone))


def get_scan_interval(current_time: datetime) -> int:
    """Return the scan cadence in seconds for the current market phase."""
    local_time = current_time.timetz().replace(tzinfo=None)
    if dt_time(9, 35) <= local_time < dt_time(10, 30):
        return 300
    if dt_time(10, 30) <= local_time < dt_time(11, 30):
        return 600
    if dt_time(11, 30) <= local_time < dt_time(14, 30):
        return 900
    if dt_time(14, 30) <= local_time < dt_time(15, 45):
        return 600
    if dt_time(15, 45) <= local_time < MARKET_CLOSE_TIME:
        return 600
    return 0


def is_open_entry_window(current_time: datetime) -> bool:
    """Return True during the dedicated open-entry phase."""
    if current_time.weekday() >= 5:
        return False
    local_time = current_time.timetz().replace(tzinfo=None)
    return OPEN_SCAN_START <= local_time < OPEN_SCAN_END


def can_scan_for_new_entries(current_time: datetime) -> bool:
    """Return True while the strategy is allowed to open new positions."""
    if current_time.weekday() >= 5:
        return False
    local_time = current_time.timetz().replace(tzinfo=None)
    return OPEN_SCAN_START <= local_time < NO_NEW_ENTRY_AFTER


def next_scan_time(current_time: datetime, interval_seconds: int) -> datetime:
    """Align to the next exact scan boundary for the configured cadence."""
    if interval_seconds <= 0:
        return current_time
    epoch_seconds = int(current_time.timestamp())
    remainder = epoch_seconds % interval_seconds
    sleep_seconds = interval_seconds if remainder == 0 else interval_seconds - remainder
    return current_time + timedelta(seconds=sleep_seconds)


def sleep_until(next_run: datetime, now_provider: NowProvider, sleep_provider: SleepProvider) -> None:
    """Sleep in bounded chunks until the requested time."""
    while True:
        remaining = (next_run - now_provider()).total_seconds()
        if remaining <= 0:
            return
        sleep_provider(min(remaining, 30.0))


@contextmanager
def job_loop_lock(lock_name: str) -> Iterator[bool]:
    """Prevent overlapping session loops across scheduled invocations."""
    lock_path = SETTINGS.paths.runtime_dir / f"{lock_name}.lock"
    acquired = False
    handle: Optional[int] = None
    try:
        try:
            handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(handle, f"{os.getpid()}|{datetime.now().isoformat()}".encode("utf-8"))
            acquired = True
        except FileExistsError:
            LOGGER.warning("Loop lock already held: %s", lock_path.name)
            acquired = False
        yield acquired
    finally:
        if handle is not None:
            os.close(handle)
        if acquired and lock_path.exists():
            try:
                lock_path.unlink()
            except OSError:
                LOGGER.warning("Failed to remove loop lock: %s", lock_path)


def _default_state() -> Dict[str, object]:
    return {"watchlist": {}, "tracked_positions": [], "weekly_results": []}


def load_runtime_state() -> Dict[str, object]:
    """Read the shared runtime state with log-based recovery fallback."""
    state_path = SETTINGS.paths.state_file
    if not state_path.exists():
        state = _default_state()
    else:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            LOGGER.warning("Failed to read runtime state; falling back to default state.")
            state = _default_state()

    if not state.get("watchlist"):
        premarket = read_latest_workflow_snapshot(SETTINGS.paths.research_log, "Premarket")
        if isinstance(premarket, dict) and isinstance(premarket.get("watchlist"), dict):
            state["watchlist"] = premarket["watchlist"]
    return state


def save_runtime_state(state: Dict[str, object]) -> None:
    """Persist runtime state atomically from within long-running jobs."""
    temp_path = SETTINGS.paths.state_file.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temp_path, SETTINGS.paths.state_file)


def _normalize_skip_reason(reason: object) -> str:
    if isinstance(reason, list):
        return ", ".join(str(item) for item in reason)
    return str(reason)


def run_entry_scan(
    *,
    stage_name: str,
    market_data: MarketDataService,
    order_manager: OrderManager,
    news_filter: NewsRiskFilter,
    watchlist: Dict[str, object],
    account_equity: float,
    cash_available: float,
    current_positions: List[Dict[str, object]],
    open_risk_amount: float,
    scan_time: Optional[datetime] = None,
) -> Dict[str, object]:
    """Run one deterministic entry scan over the prepared watchlist."""
    current_time = scan_time or session_now()
    executed: List[Dict[str, object]] = []
    skipped: List[Dict[str, object]] = []
    signals_detected = 0
    scanned_symbols = 0

    LOGGER.info("%s scan started at %s", stage_name, current_time.isoformat())

    macro_context = news_filter.get_macro_risk_context()
    if macro_context.get("risk_level") == "HIGH":
        LOGGER.warning(
            "%s scan blocked by macro risk providers=%s matches=%s",
            stage_name,
            macro_context.get("provider_hits", []),
            macro_context.get("matched_headlines", []),
        )
        return {
            "executed": executed,
            "skipped": [{"symbol": "*", "reason": "macro_risk", "provider_hits": macro_context.get("provider_hits", [])}],
            "signals_detected": signals_detected,
            "symbols_scanned": scanned_symbols,
            "macro_risk": macro_context,
        }

    for symbol, plan in watchlist.items():
        scanned_symbols += 1
        symbol_news_context = news_filter.get_symbol_risk_context(symbol)
        if symbol_news_context.get("risk_level") == "HIGH":
            reason = {
                "symbol": symbol,
                "reason": "symbol_news_risk",
                "risk_level": symbol_news_context.get("risk_level"),
                "provider_hits": symbol_news_context.get("provider_hits", []),
            }
            skipped.append(reason)
            LOGGER.info("%s skip %s: %s", stage_name, symbol, reason)
            continue

        intraday_bars = market_data.get_intraday_bars(symbol, duration="2 D", bar_size="5 mins")
        quote = market_data.get_quote(symbol)
        if intraday_bars.empty or float(quote.get("last", 0.0) or 0.0) <= 0:
            skipped.append({"symbol": symbol, "reason": "missing_live_data"})
            LOGGER.info("%s skip %s: missing_live_data", stage_name, symbol)
            continue

        levels = [Level(**level) for level in plan.get("levels", [])]
        candidate_signals = route_strategies(symbol, intraday_bars, levels, news_context=symbol_news_context)
        signals_detected += len(candidate_signals)
        if not candidate_signals:
            skipped.append({"symbol": symbol, "reason": "no_signal"})
            LOGGER.info("%s skip %s: no_signal", stage_name, symbol)
            continue

        session_low = float(intraday_bars["low"].min())
        session_high = float(intraday_bars["high"].max())
        for signal in candidate_signals:
            atr_ok = technical_atr_has_room(float(plan.get("technical_atr", 0.0)), signal.entry)
            trend_ok = atr_travel_filter(
                signal.entry,
                session_low,
                session_high,
                float(plan.get("daily_atr", 0.0)),
                False,
            )
            if not atr_ok or not trend_ok:
                skipped.append({"symbol": symbol, "reason": "atr_filter"})
                LOGGER.info("%s skip %s: atr_filter", stage_name, symbol)
                continue

            success, payload = order_manager.execute_trade(
                signal,
                account_equity=account_equity,
                cash_available=cash_available,
                current_positions=current_positions,
                open_risk_amount=open_risk_amount,
            )
            if success:
                executed.append(payload)
                current_positions.append(
                    {
                        "symbol": symbol,
                        "quantity": int(payload.get("quantity", 0)),
                        "entry": float(payload.get("entry", 0.0)),
                        "stop_loss": float(payload.get("stop_loss", 0.0)),
                        "direction": str(payload.get("direction", "long")),
                    }
                )
                open_risk_amount += abs(float(payload["entry"]) - float(payload["stop_loss"])) * float(payload["quantity"])
                LOGGER.info("%s executed %s via %s", stage_name, symbol, payload.get("strategy"))
                break

            skipped.append({"symbol": symbol, "reason": payload.get("reasons", ["rejected"])})
            LOGGER.info("%s skip %s: %s", stage_name, symbol, payload.get("reasons", ["rejected"]))

    LOGGER.info(
        "%s scan finished: scanned=%s signals=%s executed=%s skipped=%s",
        stage_name,
        scanned_symbols,
        signals_detected,
        len(executed),
        len(skipped),
    )
    return {
        "executed": executed,
        "skipped": skipped,
        "signals_detected": signals_detected,
        "symbols_scanned": scanned_symbols,
        "macro_risk": macro_context,
    }


def manage_positions(
    *,
    broker: IBKRClient,
    news_filter: NewsRiskFilter,
    tracked_positions: List[Dict[str, object]],
    account_equity: float,
    daily_realized_pnl: float = 0.0,
    dry_run: bool = False,
) -> Dict[str, object]:
    """Manage open positions, exits, and stop hygiene."""
    actions: List[Dict[str, object]] = []
    closed_symbols: set[str] = set()
    open_orders = broker.get_open_orders()
    stop_symbols = {order.get("symbol") for order in open_orders if order.get("type") == "STP"}
    macro_risk = news_filter.is_macro_risk()
    kill_switch, reasons = should_trigger_kill_switch(
        account_equity=account_equity,
        daily_realized_pnl=daily_realized_pnl,
        connection_healthy=broker.is_connected,
        broker_positions=broker.get_positions(),
        internal_positions=tracked_positions,
        macro_risk=macro_risk,
        stop_integrity_ok=all(position.get("symbol") in stop_symbols for position in tracked_positions),
    )
    if kill_switch:
        for position in tracked_positions:
            quantity = int(position.get("quantity", 0))
            if quantity <= 0:
                continue
            action = "SELL" if position.get("direction") == "long" else "BUY"
            if not dry_run:
                broker.place_market_order(str(position["symbol"]), action, quantity)
            closed_symbols.add(str(position["symbol"]))
        payload = {"event": "kill_switch", "reasons": reasons}
        actions.append(payload)
        tracked_positions[:] = [position for position in tracked_positions if str(position.get("symbol")) not in closed_symbols]
        LOGGER.warning("Intraday kill switch triggered: %s", reasons)
        return {"actions": actions, "macro_risk": macro_risk, "kill_switch": True, "reasons": reasons}

    for position in tracked_positions:
        symbol = str(position["symbol"])
        quote = broker.get_market_price(symbol)
        current_price = float(quote.get("last", 0.0))
        entry = float(position.get("entry", 0.0))
        quantity = int(position.get("quantity", 0))
        direction = str(position.get("direction", "long"))
        pnl_pct = (
            ((current_price - entry) / entry) * 100.0
            if direction == "long" and entry
            else (((entry - current_price) / entry) * 100.0 if entry else 0.0)
        )
        if pnl_pct <= -7.0 and quantity > 0:
            if not dry_run:
                broker.place_market_order(symbol, "SELL" if direction == "long" else "BUY", quantity)
            actions.append({"symbol": symbol, "event": "loss_cut", "pnl_pct": round(pnl_pct, 2)})
            closed_symbols.add(symbol)
            LOGGER.info("Intraday action %s: loss_cut pnl_pct=%s", symbol, round(pnl_pct, 2))
            continue
        if news_filter.has_high_risk_news(symbol) and quantity > 0:
            if not dry_run:
                broker.place_market_order(symbol, "SELL" if direction == "long" else "BUY", quantity)
            actions.append({"symbol": symbol, "event": "thesis_break_news"})
            closed_symbols.add(symbol)
            LOGGER.info("Intraday action %s: thesis_break_news", symbol)
            continue
        if pnl_pct >= 15.0 and quantity > 0:
            trail_percent = 5.0 if pnl_pct >= 20.0 else 7.0
            proposed_stop = (
                current_price * (1 - max(trail_percent, 3.0) / 100.0)
                if direction == "long"
                else current_price * (1 + max(trail_percent, 3.0) / 100.0)
            )
            if not dry_run:
                broker.replace_stop_order(
                    symbol,
                    "SELL" if direction == "long" else "BUY",
                    quantity,
                    proposed_stop,
                    int(position.get("stop_order_id", 0)) or None,
                )
            position["stop_loss"] = round(proposed_stop, 2)
            actions.append({"symbol": symbol, "event": "stop_adjusted", "new_stop": round(proposed_stop, 2)})
            LOGGER.info("Intraday action %s: stop_adjusted new_stop=%s", symbol, round(proposed_stop, 2))

    if closed_symbols:
        tracked_positions[:] = [position for position in tracked_positions if str(position.get("symbol")) not in closed_symbols]

    return {"actions": actions, "macro_risk": macro_risk, "kill_switch": False, "reasons": []}


def persist_tracked_positions(tracked_positions: List[Dict[str, object]]) -> None:
    """Save tracked positions mid-session so long-running loops stay durable."""
    state = load_runtime_state()
    state["tracked_positions"] = tracked_positions
    save_runtime_state(state)


def calculate_open_risk_amount(positions: List[Dict[str, object]]) -> float:
    """Estimate current open risk from tracked positions."""
    total = 0.0
    for position in positions:
        quantity = float(position.get("quantity", 0.0) or 0.0)
        entry = float(position.get("entry", 0.0) or position.get("avg_cost", 0.0) or 0.0)
        stop_loss = float(position.get("stop_loss", 0.0) or 0.0)
        total += abs(entry - stop_loss) * quantity
    return total


def summarize_skip_reasons(skipped: List[Dict[str, object]]) -> Dict[str, int]:
    """Count skip reasons for compact reporting."""
    summary: Dict[str, int] = {}
    for item in skipped:
        reason = _normalize_skip_reason(item.get("reason", "unknown"))
        summary[reason] = summary.get(reason, 0) + 1
    return summary


def serialize_scan_results(results: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Normalize scan summaries into JSON-safe structures for workflow snapshots."""
    serialized: List[Dict[str, object]] = []
    for result in results:
        normalized: Dict[str, object] = {}
        for key, value in result.items():
            if is_dataclass(value):
                normalized[key] = asdict(value)
            else:
                normalized[key] = value
        serialized.append(normalized)
    return serialized


def build_job_dependencies(
    broker: IBKRClient,
    alerter: SlackAlerter,
    news_filter: NewsRiskFilter,
    *,
    dry_run: bool,
) -> tuple[MarketDataService, OrderManager]:
    """Construct job-level reusable services from the live broker context."""
    market_data = MarketDataService(broker)
    order_manager = OrderManager(
        broker,
        market_data,
        alerter,
        news_filter,
        dry_run=dry_run,
    )
    return market_data, order_manager


def refresh_watchlist_if_missing(broker: IBKRClient) -> Dict[str, object]:
    """Load the prepared watchlist from state or latest workflow snapshot."""
    state = load_runtime_state()
    watchlist = state.get("watchlist", {})
    if isinstance(watchlist, dict) and watchlist:
        return watchlist

    news_service = NewsService(broker=broker)
    news_filter = NewsRiskFilter(news_service)
    from src.jobs.premarket import run_premarket

    positions = broker.get_positions()
    account_snapshot = {
        "account": broker.get_account_summary(),
        "positions": positions,
        "open_orders": broker.get_open_orders(),
    }
    symbols: List[str] = []
    seen: set[str] = set()
    for position in positions:
        sec_type = str(position.get("sec_type", "")).strip().upper()
        if sec_type and sec_type != "STK":
            continue
        symbol = str(position.get("symbol", "")).strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    for configured in SETTINGS.symbols:
        symbol = configured.strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)

    premarket_summary = run_premarket(
        MarketDataService(broker),
        news_service,
        news_filter,
        account_snapshot,
        symbols,
    )
    state["watchlist"] = premarket_summary.get("watchlist", {})
    save_runtime_state(state)
    return state["watchlist"]
