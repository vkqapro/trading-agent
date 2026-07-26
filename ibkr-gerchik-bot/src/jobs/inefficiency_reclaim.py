"""Connected IRS data-hydration and analysis workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from src.config import LOGGER, SETTINGS
from src.data.inefficiency_reclaim_history import ensure_irs_history
from src.data.market_data import MarketDataService
from src.execution.inefficiency_reclaim import InefficiencyReclaimPaperExecutor
from src.scanners.inefficiency_reclaim import run_inefficiency_reclaim_screener
from src.storage.inefficiency_reclaim_store import InefficiencyReclaimStore


IRS_WORKFLOW_MODES = {
    "PREMARKET_CONTEXT",
    "HOURLY_SETUP_SCAN",
    "FIFTEEN_MIN_CONFIRMATION_SCAN",
    "REALTIME_ENTRY_WATCH",
    "POSITION_MANAGEMENT",
    "EOD_REPORT",
}


def notify_inefficiency_reclaim_scan(
    result: Mapping[str, Any],
    *,
    mode: str,
    alerter: Any | None,
) -> None:
    if alerter is None:
        return
    for item in result.get("newly_armed", ()):
        alerter.send(
            f"PAPER IRS setup armed {item.get('ticker')} {item.get('direction')} "
            f"score={item.get('score')} entry={item.get('planned_entry')}/"
            f"{item.get('entry_limit')} stop={item.get('planned_stop')} "
            f"target={item.get('planned_target')} R={item.get('structural_R')} "
            f"qty={item.get('position_size')} signal={item.get('signal_id')}"
        )
    reason_counts = result.get("reason_counts", {})
    if mode == "PREMARKET_CONTEXT" and isinstance(reason_counts, Mapping):
        fail_closed = int(reason_counts.get("NEWS_STATUS_UNAVAILABLE", 0) or 0)
        if fail_closed:
            alerter.send_error(
                f"PAPER IRS risk data unavailable for {fail_closed} candidate checks; "
                "entries remain blocked."
            )
    if mode == "EOD_REPORT":
        alerter.send(
            f"PAPER IRS EOD summary symbols={result.get('universe_size', 0)} "
            f"display_setups={result.get('signals_found', 0)} "
            f"rejections={len(result.get('rejected', ())) if isinstance(result.get('rejected'), list) else 0} "
            f"expired={result.get('expired_setups', 0)}"
        )


def _summary_value(rows: Sequence[Mapping[str, Any]], tags: set[str]) -> float:
    for row in rows:
        if str(row.get("tag") or "") in tags:
            try:
                return float(row.get("value") or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def run_inefficiency_reclaim_job(
    *,
    market_data: MarketDataService,
    symbols: Sequence[str],
    watchlist: Mapping[str, Any],
    account_summary: Sequence[Mapping[str, Any]],
    positions: Sequence[Mapping[str, Any]],
    open_orders: Sequence[Mapping[str, Any]],
    mode: str = "HOURLY_SETUP_SCAN",
    hydrate: bool = True,
    as_of: datetime | None = None,
    alerter: Any | None = None,
    broker: Any | None = None,
) -> dict[str, Any]:
    resolved_mode = str(mode or "HOURLY_SETUP_SCAN").strip().upper()
    if resolved_mode not in IRS_WORKFLOW_MODES:
        raise ValueError(f"Unsupported IRS workflow mode: {resolved_mode}")
    now = as_of or datetime.now(ZoneInfo(SETTINGS.trading_hours.timezone))
    store = InefficiencyReclaimStore(SETTINGS.inefficiency_reclaim.database_path)
    hydration: dict[str, Any] = {}
    if hydrate:
        for symbol in symbols:
            hydration[symbol] = ensure_irs_history(market_data, symbol)

    account = {
        "equity": _summary_value(account_summary, {"NetLiquidation"}),
        "buying_power": _summary_value(account_summary, {"BuyingPower", "AvailableFunds"}),
        "open_positions": len([item for item in positions if float(item.get("position") or 0) != 0]),
        "existing_symbols": [
            str(item.get("symbol") or "").upper()
            for item in positions
            if float(item.get("position") or 0) != 0
        ],
        "pending_symbols": [
            str(item.get("symbol") or "").upper()
            for item in open_orders
            if str(item.get("status") or "").lower() not in {"cancelled", "filled", "inactive"}
        ],
        "protective_stop_available": True,
        "broker_connected": True,
        "short_available": False,
    }
    result = run_inefficiency_reclaim_screener(
        symbols=symbols,
        watchlist=watchlist,
        as_of=now,
        config=SETTINGS.inefficiency_reclaim.strategy_config(),
        store=store,
        account=account,
        persist=True,
    )
    expired = 0
    cancelled_orders: tuple[str, ...] = ()
    expiry_entry_lock = False
    if resolved_mode == "EOD_REPORT":
        expired = store.expire_due(as_of=now, run_id=result["run_id"])
        if broker is not None:
            cancellation = InefficiencyReclaimPaperExecutor(
                broker,
                store,
                config=SETTINGS.inefficiency_reclaim.strategy_config(),
                alerter=alerter,
            ).cancel_expired_orders(as_of=now, run_id=result["run_id"])
            cancelled_orders = cancellation.cancelled_order_refs
            expiry_entry_lock = cancellation.global_entry_lock
    result["workflow_mode"] = resolved_mode
    result["hydration"] = hydration
    result["expired_setups"] = expired
    result["cancelled_expired_orders"] = list(cancelled_orders)
    result["global_entry_lock"] = expiry_entry_lock
    result["automatic_orders_submitted"] = 0
    result["execution_note"] = (
        "Analysis workflow only. Controlled paper submission uses "
        "InefficiencyReclaimPaperExecutor after explicit fresh risk checks."
    )
    notify_inefficiency_reclaim_scan(result, mode=resolved_mode, alerter=alerter)
    LOGGER.info(
        "IRS workflow mode=%s symbols=%s expired=%s automatic_orders=0",
        resolved_mode,
        len(symbols),
        expired,
    )
    return result
