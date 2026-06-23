"""Persistence for per-symbol daily trading decisions.

This is the "why we did / didn't trade" journal that backs
``memory/daily_decisions.json`` (and the dashboard's Intraday tab).

Design: **detection stays pure.** Strategy detectors do not write to disk; they
``record`` their decision into a caller-provided *sink* (a list). The caller —
normally the strategy router — collects every decision for a scan and
``persist``s them in a single locked batch. When no sink is provided, recording
is a no-op, so detection functions have no side effects and need no patching in
tests.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.config import LOGGER
from src.strategy.levels import Level

DAILY_DECISIONS_PATH = Path(__file__).resolve().parents[2] / "memory" / "daily_decisions.json"
DAILY_DECISIONS_LOCK_PATH = DAILY_DECISIONS_PATH.with_suffix(".json.lock")
MAX_STORED_ATTEMPTS = 100
DAILY_DECISIONS_WRITE_ATTEMPTS = 8
DAILY_DECISIONS_WRITE_DELAY_SECONDS = 0.1
DAILY_DECISIONS_LOCK_STALE_SECONDS = 30.0

# A sink is a list that detectors append decision records to. ``None`` disables
# recording entirely (pure detection / tests).
DecisionSink = Optional[List[Dict[str, object]]]


def record(
    sink: DecisionSink,
    symbol: str,
    level: Level,
    strategy_name: str,
    result: Dict[str, object],
) -> None:
    """Record a decision into ``sink`` if one is provided; otherwise do nothing."""
    if sink is None:
        return
    sink.append({"symbol": symbol, "level": level, "strategy": strategy_name, "result": result})


def persist(records: List[Dict[str, object]]) -> None:
    """Persist a batch of recorded decisions under a single file lock."""
    if not records:
        return
    lock_handle: int | None = None
    try:
        lock_handle = _acquire_daily_decisions_lock()
        payload = _load_daily_decisions()
        decisions = payload.setdefault("decisions", {})
        for rec in records:
            _apply_to_decisions(decisions, rec["symbol"], rec["level"], rec["strategy"], rec["result"])
        if _save_daily_decisions(payload):
            LOGGER.info("Persisted %d daily decision(s).", len(records))
        else:
            LOGGER.warning("Skipped persisting %d daily decision(s) after write failure.", len(records))
    except TimeoutError:
        LOGGER.warning("Skipped persisting %d daily decision(s) due to lock timeout.", len(records))
    finally:
        if lock_handle is not None:
            _release_daily_decisions_lock(lock_handle)


def persist_decision(symbol: str, level: Level, strategy_name: str, result: Dict[str, object]) -> None:
    """Persist a single decision immediately (convenience around :func:`persist`)."""
    persist([{"symbol": symbol, "level": level, "strategy": strategy_name, "result": result}])


def load() -> Dict[str, object]:
    """Return today's persisted decisions payload as ``{"date", "decisions"}``.

    Readers (e.g. the EOD report) should use this rather than re-reading the
    file, so the on-disk format lives in exactly one place.
    """
    return _load_daily_decisions()


def _decision_rank(result: Dict[str, object]) -> tuple[int, float]:
    router_status = str(result.get("router_status", "") or "").strip().lower()
    if router_status == "accepted" and result.get("signal") in {"BUY", "SELL"}:
        signal_rank = 3
    elif router_status == "rejected":
        signal_rank = 2
    else:
        signal_rank = 1 if result.get("signal") in {"BUY", "SELL"} else 0
    return signal_rank, float(result.get("confidence", 0.0))


def _build_attempt(level: Level, strategy_name: str, result: Dict[str, object]) -> Dict[str, object]:
    attempt: Dict[str, object] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "strategy": strategy_name,
        "level": round(level.price, 2),
        "level_type": level.type,
        "signal": result.get("signal"),
        "entry": result.get("entry"),
        "stop": result.get("stop"),
        "target": result.get("target"),
        "position_modifier": result.get("position_modifier"),
        "confidence": result.get("confidence"),
        "reason": list(result.get("reason", [])),
        "context": dict(result.get("context", {})),
    }
    for optional_key in ("router_status", "raw_signal"):
        if result.get(optional_key) is not None:
            attempt[optional_key] = result.get(optional_key)
    return attempt


def _apply_to_decisions(
    decisions: Dict[str, object],
    symbol: str,
    level: Level,
    strategy_name: str,
    result: Dict[str, object],
) -> None:
    symbol_payload = decisions.setdefault(symbol, {"symbol": symbol, "attempts": [], "best_decision": None})
    attempt = _build_attempt(level, strategy_name, result)
    attempts = symbol_payload.setdefault("attempts", [])
    if isinstance(attempts, list):
        attempts.append(attempt)
        if len(attempts) > MAX_STORED_ATTEMPTS:
            del attempts[:-MAX_STORED_ATTEMPTS]
    best_decision = symbol_payload.get("best_decision")
    if not isinstance(best_decision, dict) or _decision_rank(result) >= _decision_rank(best_decision):
        symbol_payload["best_decision"] = attempt


def _load_daily_decisions() -> Dict[str, object]:
    today = date.today().isoformat()
    default_payload: Dict[str, object] = {"date": today, "decisions": {}}
    if not DAILY_DECISIONS_PATH.exists():
        return default_payload
    try:
        with DAILY_DECISIONS_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError):
        LOGGER.warning("Daily decisions file is invalid: %s", DAILY_DECISIONS_PATH)
        return default_payload
    if str(payload.get("date")) != today:
        return default_payload
    return payload if isinstance(payload, dict) else default_payload


def _lock_is_stale(lock_path: Path) -> bool:
    try:
        modified_at = lock_path.stat().st_mtime
    except OSError:
        return False
    return (time.time() - modified_at) > DAILY_DECISIONS_LOCK_STALE_SECONDS


def _acquire_daily_decisions_lock() -> int:
    DAILY_DECISIONS_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    last_error: OSError | None = None
    for attempt in range(DAILY_DECISIONS_WRITE_ATTEMPTS):
        try:
            handle = os.open(str(DAILY_DECISIONS_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(handle, f"{os.getpid()}|{datetime.now().isoformat()}".encode("utf-8"))
            return handle
        except FileExistsError as exc:
            last_error = exc
            if _lock_is_stale(DAILY_DECISIONS_LOCK_PATH):
                try:
                    DAILY_DECISIONS_LOCK_PATH.unlink()
                    LOGGER.warning("Recovered stale daily decisions lock: %s", DAILY_DECISIONS_LOCK_PATH.name)
                    continue
                except OSError:
                    pass
            time.sleep(DAILY_DECISIONS_WRITE_DELAY_SECONDS * (attempt + 1))
    raise TimeoutError(f"Could not acquire daily decisions lock: {last_error}")


def _release_daily_decisions_lock(handle: int) -> None:
    os.close(handle)
    try:
        DAILY_DECISIONS_LOCK_PATH.unlink()
    except OSError:
        LOGGER.warning("Failed to remove daily decisions lock: %s", DAILY_DECISIONS_LOCK_PATH)


def _save_daily_decisions(payload: Dict[str, object]) -> bool:
    DAILY_DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = DAILY_DECISIONS_PATH.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    for attempt in range(DAILY_DECISIONS_WRITE_ATTEMPTS):
        try:
            os.replace(temp_path, DAILY_DECISIONS_PATH)
            return True
        except PermissionError:
            if attempt == DAILY_DECISIONS_WRITE_ATTEMPTS - 1:
                break
            time.sleep(DAILY_DECISIONS_WRITE_DELAY_SECONDS * (attempt + 1))
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {5, 32} or attempt == DAILY_DECISIONS_WRITE_ATTEMPTS - 1:
                break
            time.sleep(DAILY_DECISIONS_WRITE_DELAY_SECONDS * (attempt + 1))
    LOGGER.warning("Failed to replace daily decisions file after retries: %s", DAILY_DECISIONS_PATH)
    try:
        temp_path.unlink()
    except OSError:
        pass
    return False
