"""Order-request queue shared between the dashboard and the bot.

The dashboard is otherwise read-only: instead of touching the broker, it appends
an order/close *request* to ``memory/order_requests.json``. The bot's
``execute_requests`` worker consumes pending requests, runs them through the
existing OrderManager, and writes the result back to the same file, which the
dashboard then reflects in the Trades & Positions tab.

All writes are serialized with a lock and an atomic replace so the two processes
never corrupt the file.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List

from src.config import LOGGER, MEMORY_DIR, ensure_directories

# Bump when the request schema or public API changes so the dashboard reloads a
# cached copy (Streamlit keeps imported modules alive across reruns).
ORDER_REQUESTS_VERSION = 2
ORDER_REQUESTS_PATH = MEMORY_DIR / "order_requests.json"
LOCK_PATH = ORDER_REQUESTS_PATH.with_suffix(".json.lock")
HEARTBEAT_PATH = MEMORY_DIR / "runtime" / "execute_worker.json"
HEARTBEAT_STALE_SECONDS = 30.0
MAX_STORED_REQUESTS = 200
_LOCK_STALE_SECONDS = 30.0
_LOCK_ATTEMPTS = 50
_LOCK_DELAY = 0.1

PENDING = "pending"
PROCESSING = "processing"
DONE = "done"
SIMULATED = "simulated"
REJECTED = "rejected"
ERROR = "error"
OPEN_STATUSES = {PENDING, PROCESSING}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load(path: Path | None = None) -> Dict[str, Any]:
    path = path or ORDER_REQUESTS_PATH
    if not path.exists():
        return {"requests": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"requests": []}
    if not isinstance(data, dict) or not isinstance(data.get("requests"), list):
        return {"requests": []}
    return data


def load() -> Dict[str, Any]:
    """Read the full request store (no lock; readers tolerate eventual consistency)."""
    return _load()


def list_requests() -> List[Dict[str, Any]]:
    return [r for r in _load().get("requests", []) if isinstance(r, dict)]


def pending_requests() -> List[Dict[str, Any]]:
    return [r for r in list_requests() if r.get("status") in OPEN_STATUSES]


def _acquire_lock() -> int:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    last: OSError | None = None
    for attempt in range(_LOCK_ATTEMPTS):
        try:
            handle = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(handle, f"{os.getpid()}|{_now()}".encode("utf-8"))
            return handle
        except FileExistsError as exc:
            last = exc
            try:
                if time.time() - LOCK_PATH.stat().st_mtime > _LOCK_STALE_SECONDS:
                    LOCK_PATH.unlink()
                    continue
            except OSError:
                pass
            time.sleep(_LOCK_DELAY * (attempt + 1) if attempt < 8 else _LOCK_DELAY)
    raise TimeoutError(f"Could not acquire order-request lock: {last}")


def _release_lock(handle: int) -> None:
    os.close(handle)
    try:
        LOCK_PATH.unlink()
    except OSError:
        LOGGER.warning("Failed to remove order-request lock: %s", LOCK_PATH)


def _save(data: Dict[str, Any]) -> None:
    requests = data.get("requests", [])
    if len(requests) > MAX_STORED_REQUESTS:
        data["requests"] = requests[-MAX_STORED_REQUESTS:]
    temp = ORDER_REQUESTS_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, ORDER_REQUESTS_PATH)


def _mutate(mutator: Callable[[Dict[str, Any]], Any]) -> Any:
    """Run ``mutator`` against the loaded store under lock, then persist."""
    ensure_directories()
    handle = _acquire_lock()
    try:
        data = _load()
        result = mutator(data)
        _save(data)
        return result
    finally:
        _release_lock(handle)


def submit_place(setup: Dict[str, Any], *, live: bool = False) -> str:
    """Queue a place-order request from a forecast setup. Returns the request id."""
    request_id = uuid.uuid4().hex[:12]
    record = {
        "id": request_id,
        "action": "place",
        "status": PENDING,
        "created_at": _now(),
        "updated_at": _now(),
        "source": "dashboard",
        "live": bool(live),
        "symbol": str(setup.get("symbol", "")).upper(),
        "signal": str(setup.get("signal", "")).upper(),
        "direction": str(setup.get("direction", "")),
        "entry": setup.get("entry"),
        "stop": setup.get("stop"),
        "target": setup.get("target"),
        "level_price": setup.get("level") if setup.get("level") is not None else setup.get("level_price"),
        "level_type": setup.get("level_type"),
        "strategy": setup.get("strategy"),
        "nearest_upper_level": setup.get("nearest_upper_level"),
        "nearest_lower_level": setup.get("nearest_lower_level"),
        "reward_risk": setup.get("reward_risk"),
        "confidence": setup.get("confidence"),
        # Size shown in the forecast ledger, so the placed order matches what the
        # user reviewed instead of being re-sized from the bot's live config.
        "quantity": setup.get("quantity"),
        "result": None,
        "message": "Queued for the bot worker.",
    }
    _mutate(lambda data: data["requests"].append(record))
    LOGGER.info("Queued place request %s for %s (live=%s)", request_id, record["symbol"], live)
    return request_id


def submit_close(symbol: str, *, live: bool = False) -> str:
    """Queue a close-position request. Returns the request id."""
    request_id = uuid.uuid4().hex[:12]
    record = {
        "id": request_id,
        "action": "close",
        "status": PENDING,
        "created_at": _now(),
        "updated_at": _now(),
        "source": "dashboard",
        "live": bool(live),
        "symbol": str(symbol).upper(),
        "result": None,
        "message": "Queued for the bot worker.",
    }
    _mutate(lambda data: data["requests"].append(record))
    LOGGER.info("Queued close request %s for %s (live=%s)", request_id, record["symbol"], live)
    return request_id


def update_request(request_id: str, **fields: Any) -> None:
    """Patch a request by id (used by the worker to write back results)."""
    def _apply(data: Dict[str, Any]) -> None:
        for record in data.get("requests", []):
            if isinstance(record, dict) and record.get("id") == request_id:
                record.update(fields)
                record["updated_at"] = _now()
                return
    _mutate(_apply)


def write_heartbeat(pid: int | None = None) -> None:
    """Stamp the worker heartbeat (single-writer; dashboard/launcher read it)."""
    payload = {"pid": pid if pid is not None else os.getpid(), "ts": time.time(), "iso": _now()}
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = HEARTBEAT_PATH.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temp, HEARTBEAT_PATH)
    except OSError as exc:  # pragma: no cover - best effort
        LOGGER.warning("Failed to write worker heartbeat: %s", exc)


def read_heartbeat() -> Dict[str, Any] | None:
    if not HEARTBEAT_PATH.exists():
        return None
    try:
        data = json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def worker_age_seconds() -> float | None:
    """Seconds since the worker last beat, or None if no heartbeat exists."""
    heartbeat = read_heartbeat()
    if not heartbeat:
        return None
    return max(0.0, time.time() - float(heartbeat.get("ts", 0) or 0))


def worker_is_alive() -> bool:
    """True if a worker heartbeat exists and is fresh (within the stale window)."""
    age = worker_age_seconds()
    return age is not None and age <= HEARTBEAT_STALE_SECONDS


def claim_pending(limit: int = 25) -> List[Dict[str, Any]]:
    """Atomically mark up to ``limit`` pending requests as PROCESSING and return them.

    Claiming under lock prevents two worker ticks (or two workers) from executing
    the same request twice.
    """
    claimed: List[Dict[str, Any]] = []

    def _apply(data: Dict[str, Any]) -> None:
        for record in data.get("requests", []):
            if len(claimed) >= limit:
                break
            if isinstance(record, dict) and record.get("status") == PENDING:
                record["status"] = PROCESSING
                record["updated_at"] = _now()
                claimed.append(dict(record))

    _mutate(_apply)
    return claimed
