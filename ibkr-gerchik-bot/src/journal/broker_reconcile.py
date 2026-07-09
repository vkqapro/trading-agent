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
from pathlib import Path
from typing import Any, Dict

from src.brokers.ibkr import IBKRClient
from src.config import LOGGER, MEMORY_DIR, SETTINGS
from src.jobs.session_utils import sync_tracked_positions_with_broker

SYNC_STATUS_PATH = MEMORY_DIR / "runtime" / "broker_order_sync.json"
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
                "open_orders": len(open_orders),
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
