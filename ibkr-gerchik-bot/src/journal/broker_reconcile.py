"""Best-effort broker reconciliation for the local order journal.

The journal is mostly derived from local files, but open bracket orders can be
edited directly in TWS/IBKR. This module imports the live open-order stop/limit
prices back into ``memory/runtime/state.json`` so the dashboard can show both
the original plan and the current broker-side protection.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from src.brokers.ibkr import IBKRClient
from src.config import LOGGER, MEMORY_DIR, SETTINGS
from src.jobs.session_utils import sync_tracked_positions_with_broker

SYNC_STATUS_PATH = MEMORY_DIR / "runtime" / "broker_order_sync.json"
CLOSED_POSITIONS_PATH = MEMORY_DIR / "runtime" / "closed_positions.json"
SYNC_TTL_SECONDS = float(os.getenv("ORDER_JOURNAL_BROKER_SYNC_TTL_SECONDS", "15") or 15)


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _order_ids(payload: Dict[str, Any]) -> set[str]:
    return {
        str(payload.get(key))
        for key in (
            "market_order_id",
            "order_id",
            "entry_order_id",
            "parent_order_id",
            "stop_order_id",
            "limit_order_id",
            "target_order_id",
        )
        if payload.get(key) not in (None, "", 0, "0")
    }


def _place_requests() -> list[Dict[str, Any]]:
    data = _read_json(MEMORY_DIR / "order_requests.json", {"requests": []})
    requests = data.get("requests") if isinstance(data, dict) else []
    return [
        request
        for request in requests
        if isinstance(request, dict) and request.get("action") == "place"
    ] if isinstance(requests, list) else []


def _execution_matches_request(execution: Dict[str, Any], request: Dict[str, Any]) -> bool:
    if _upper(execution.get("symbol")) != _upper(request.get("symbol")):
        return False
    result = request.get("result") if isinstance(request.get("result"), dict) else {}
    execution_order_id = str(execution.get("order_id") or "")
    if execution_order_id and execution_order_id in _order_ids(result):
        return True
    return False


def _execution_closed_record(
    execution: Dict[str, Any],
    request: Dict[str, Any],
    prior_position: Dict[str, Any] | None,
) -> Dict[str, Any]:
    result = request.get("result") if isinstance(request.get("result"), dict) else {}
    entry_price = (
        (prior_position or {}).get("avg_cost")
        or result.get("avg_fill_price")
        or request.get("entry")
    )
    order_id = execution.get("order_id")
    stop_order_id = result.get("stop_order_id")
    limit_order_id = result.get("limit_order_id")
    if str(order_id) == str(limit_order_id):
        exit_reason = "BRACKET_TARGET_FILLED"
    elif str(order_id) == str(stop_order_id):
        exit_reason = "BRACKET_STOP_FILLED"
    else:
        exit_reason = "BROKER_SELL_EXECUTION"
    return {
        **(prior_position or {}),
        "symbol": _upper(request.get("symbol")),
        "quantity": abs(float(request.get("quantity") or result.get("quantity") or execution.get("shares") or 0)),
        "entry": entry_price,
        "avg_cost": entry_price,
        "opened_at": (prior_position or {}).get("opened_at") or request.get("updated_at") or request.get("created_at"),
        "market_order_id": result.get("market_order_id"),
        "stop_order_id": stop_order_id,
        "limit_order_id": limit_order_id,
        "strategy": request.get("strategy") or result.get("strategy"),
        "source": request.get("source"),
        "closed_at": execution.get("time") or _now(),
        "exit_price": execution.get("price"),
        "exit_quantity": abs(float(execution.get("shares") or 0)),
        "exit_order_id": order_id,
        "exit_perm_id": execution.get("perm_id"),
        "exit_execution_id": execution.get("exec_id"),
        "exit_reason": exit_reason,
        "request_id": str(request.get("id") or ""),
        "entry_price_source": "broker_position" if prior_position else "planned_entry",
    }


def _record_closed_positions(
    before: Dict[str, Dict[str, Any]],
    synced: list[Dict[str, Any]],
    executions: list[Dict[str, Any]] | None = None,
    open_orders: list[Dict[str, Any]] | None = None,
) -> int:
    executions = executions or []
    open_orders = open_orders or []
    if not executions:
        return 0

    data = _read_json(CLOSED_POSITIONS_PATH, {"positions": []})
    if not isinstance(data, dict):
        data = {"positions": []}
    positions = data.get("positions")
    if not isinstance(positions, list):
        positions = []
    changed = False

    added = 0
    # A bracket child can fill after the local tracked position has already
    # disappeared. Match today's broker sell execution back to the original
    # place request by parent/child order id. A missing position snapshot by
    # itself is not closure evidence: an entry can still be working, or an
    # IBKR/API snapshot can be temporarily incomplete.
    for execution in executions:
        if not isinstance(execution, dict) or _upper(execution.get("side")) not in {"SLD", "SELL"}:
            continue
        request = next((item for item in _place_requests() if _execution_matches_request(execution, item)), None)
        symbol = _upper(execution.get("symbol"))
        prior_position = before.get(symbol)
        if request is None and prior_position:
            # Auto-management exits are standalone market orders, so their
            # order id is not one of the entry/bracket ids in order_requests.
            # Recover the originating request through the tracked position.
            prior_ids = _order_ids(prior_position)
            request = next(
                (
                    item
                    for item in _place_requests()
                    if _upper(item.get("symbol")) == symbol
                    and prior_ids.intersection(
                        _order_ids(item.get("result") if isinstance(item.get("result"), dict) else {})
                    )
                ),
                None,
            )
        if request is None and open_orders:
            # If the local position snapshot was already lost, a still-working
            # bracket identifies the originating request without guessing from
            # symbol alone. This covers a market exit that leaves its bracket
            # children visible in TWS until they are cancelled.
            open_ids = {
                str(order.get("order_id"))
                for order in open_orders
                if isinstance(order, dict) and order.get("order_id") not in (None, "", 0, "0")
            }
            request = next(
                (
                    item
                    for item in _place_requests()
                    if _upper(item.get("symbol")) == symbol
                    and open_ids.intersection(
                        _order_ids(item.get("result") if isinstance(item.get("result"), dict) else {})
                    )
                ),
                None,
            )
        if request is None:
            # Last-resort recovery for a bot-generated market exit after both
            # the local position and bracket children have disappeared. Only
            # consider completed bracket requests for the same symbol and use
            # the latest request that predates the execution; this avoids
            # treating an unrelated symbol sale as a journal close.
            execution_time = _parse_dt(execution.get("time"))
            candidates = []
            for item in _place_requests():
                if _upper(item.get("symbol")) != symbol:
                    continue
                result = item.get("result") if isinstance(item.get("result"), dict) else {}
                if not _order_ids(result) or _upper(item.get("status")) not in {"DONE", "SIMULATED"}:
                    continue
                request_time = _parse_dt(item.get("updated_at") or item.get("created_at"))
                if execution_time and request_time and request_time > execution_time:
                    continue
                candidates.append((request_time or datetime.min.replace(tzinfo=timezone.utc), item))
            if candidates:
                request = max(candidates, key=lambda pair: pair[0])[1]
        if request is None:
            continue
        symbol = _upper(request.get("symbol"))
        request_result = request.get("result") if isinstance(request.get("result"), dict) else {}
        request_ids = _order_ids(request_result)
        matching = next(
            (
                item
                for item in positions
                if isinstance(item, dict)
                and _upper(item.get("symbol")) == symbol
                and request_ids.intersection(_order_ids(item))
            ),
            None,
        )
        if matching is None:
            matching = next(
                (
                    item
                    for item in positions
                    if isinstance(item, dict)
                    and _upper(item.get("symbol")) == symbol
                    and str(item.get("exit_execution_id") or "") == str(execution.get("exec_id") or "")
                ),
                None,
            )
        record = _execution_closed_record(execution, request, prior_position or before.get(symbol))
        if matching is None:
            positions.append(record)
            added += 1
            changed = True
        else:
            for key, value in record.items():
                if matching.get(key) in (None, "", 0, "0") and value not in (None, "", 0, "0"):
                    matching[key] = value
                    changed = True

    if changed:
        data["positions"] = positions[-500:]
        _write_json(CLOSED_POSITIONS_PATH, data)
    return added


def _sync_recent_enough() -> bool:
    data = _read_json(SYNC_STATUS_PATH, {})
    try:
        ts = float(data.get("ts", 0) or 0)
    except Exception:
        return False
    return time.time() - ts < SYNC_TTL_SECONDS


def reconcile_ibkr_open_orders(*, force: bool = False) -> Dict[str, Any]:
    """Sync live IBKR open stop/limit prices into local tracked positions.

    Returns a small status payload. Failures are intentionally non-fatal so a
    dashboard refresh does not take down the journal if TWS is closed or the
    client id is temporarily busy.
    """
    if not force and _sync_recent_enough():
        return {"ok": True, "skipped": True, "reason": "fresh"}

    status: Dict[str, Any] = {"ok": False, "updated": 0, "open_orders": 0}
    broker = IBKRClient()
    try:
        client_id = int(os.getenv("IBKR_JOURNAL_SYNC_CLIENT_ID", "91") or 91)
        broker.config = replace(
            broker.config,
            client_id=client_id,
            reconnect_retries=1,
            reconnect_delay_seconds=0,
            market_data_timeout_seconds=min(int(broker.config.market_data_timeout_seconds or 10), 5),
        )
        broker.connect()
        positions = broker.get_positions()
        open_orders = broker.get_open_orders()
        executions = broker.get_executions() if hasattr(broker, "get_executions") else []
        state_path = SETTINGS.paths.state_file
        state = _read_json(state_path, {})
        if not isinstance(state, dict):
            state = {}
        before = {
            str(p.get("symbol", "")).strip().upper(): dict(p)
            for p in state.get("tracked_positions", [])
            if isinstance(p, dict)
        }
        synced = sync_tracked_positions_with_broker(
            state.get("tracked_positions", []),
            positions,
            open_orders,
        )
        closed_observed = _record_closed_positions(before, synced, executions, open_orders)
        state["tracked_positions"] = synced
        _write_json(state_path, state)

        updated = 0
        for item in synced:
            symbol = str(item.get("symbol", "")).strip().upper()
            old = before.get(symbol, {})
            if (
                item.get("current_stop_loss") != old.get("current_stop_loss")
                or item.get("current_target") != old.get("current_target")
                or item.get("stop_loss") != old.get("stop_loss")
                or item.get("target") != old.get("target")
            ):
                updated += 1
        status.update(
            {
                "ok": True,
                "updated": updated,
                "closed_observed": closed_observed,
                "open_orders": len(open_orders),
                "executions": len(executions),
                "positions": len(positions),
                "client_id": client_id,
                "ts": time.time(),
            }
        )
        return status
    except Exception as exc:  # pragma: no cover - requires live TWS/IB Gateway.
        LOGGER.warning("Order journal broker reconciliation failed: %s", exc)
        status.update({"ok": False, "error": str(exc), "ts": time.time()})
        return status
    finally:
        try:
            broker.disconnect()
        except Exception:
            LOGGER.debug("Broker disconnect after journal reconciliation failed.", exc_info=True)
        _write_json(SYNC_STATUS_PATH, status)
