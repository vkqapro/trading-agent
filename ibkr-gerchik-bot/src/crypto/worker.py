"""Long-running OKX candle collector for the crypto dashboard."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from src.crypto.analysis import collect_crypto_bars, run_crypto_analysis
from src.crypto.config import CRYPTO_SETTINGS, ensure_crypto_directories

RUNTIME_DIR = CRYPTO_SETTINGS.memory_dir / "runtime"
HEARTBEAT_PATH = RUNTIME_DIR / "worker.json"
LOCK_PATH = RUNTIME_DIR / "worker.lock"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _write_json(path: Path, payload: dict) -> None:
    ensure_crypto_directories()
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _write_heartbeat(payload: dict) -> None:
    _write_json(HEARTBEAT_PATH, {"updated_at": _utc_now().isoformat(timespec="seconds"), **payload})


def _safe_print(payload: dict) -> None:
    try:
        print(json.dumps(payload, indent=2), flush=True)
    except OSError:
        pass


@contextmanager
def _single_worker_lock(stale_after_seconds: int) -> Iterator[bool]:
    ensure_crypto_directories()
    now = time.time()
    if LOCK_PATH.exists():
        try:
            age = now - LOCK_PATH.stat().st_mtime
            if age > stale_after_seconds:
                LOCK_PATH.unlink()
        except OSError:
            pass
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "created_at": _utc_now().isoformat(timespec="seconds")}))
        acquired = True
    except FileExistsError:
        acquired = False
    try:
        yield acquired
    finally:
        if acquired:
            try:
                LOCK_PATH.unlink()
            except OSError:
                pass


def run_crypto_worker(*, interval_seconds: int | None = None, once: bool = False, validate: bool = False) -> dict:
    """Collect and analyze OKX crypto candles forever, or once for tests."""
    interval = max(60, int(interval_seconds or CRYPTO_SETTINGS.collect_interval_seconds or 300))
    stale_after = max(interval * 3, 900)
    with _single_worker_lock(stale_after) as acquired:
        if not acquired:
            result = {
                "ok": False,
                "blocked": True,
                "reason": "crypto_worker_already_running",
                "heartbeat_path": str(HEARTBEAT_PATH),
            }
            _safe_print(result)
            return result

        cycles = 0
        last_result: dict = {}
        _write_heartbeat({"status": "starting", "interval_seconds": interval, "pid": os.getpid(), "cycles": cycles})
        while True:
            cycle_started = _utc_now()
            cycles += 1
            try:
                collect = collect_crypto_bars(validate=validate)
                analysis = run_crypto_analysis()
                last_result = {
                    "ok": True,
                    "status": "ok",
                    "cycle": cycles,
                    "started_at": cycle_started.isoformat(timespec="seconds"),
                    "finished_at": _utc_now().isoformat(timespec="seconds"),
                    "interval_seconds": interval,
                    "collect": collect,
                    "analysis": {
                        "symbols": len(analysis.get("symbols", [])),
                        "ready": sum(
                            1
                            for row in (analysis.get("watchlist", {}) or {}).values()
                            if isinstance(row, dict) and row.get("ready")
                        ),
                        "errors": analysis.get("errors", []),
                    },
                }
                _safe_print(last_result)
                _write_heartbeat({"status": "ok", "pid": os.getpid(), "cycles": cycles, "last_result": last_result})
            except Exception as exc:  # keep a 24/7 worker alive through transient OKX/network errors
                last_result = {
                    "ok": False,
                    "status": "error",
                    "cycle": cycles,
                    "started_at": cycle_started.isoformat(timespec="seconds"),
                    "finished_at": _utc_now().isoformat(timespec="seconds"),
                    "interval_seconds": interval,
                    "error": str(exc),
                }
                _safe_print(last_result)
                _write_heartbeat({"status": "error", "pid": os.getpid(), "cycles": cycles, "last_result": last_result})

            if once:
                return last_result

            elapsed = max(0.0, (_utc_now() - cycle_started).total_seconds())
            sleep_for = max(5.0, interval - elapsed)
            _write_heartbeat({
                "status": "sleeping",
                "pid": os.getpid(),
                "cycles": cycles,
                "sleep_seconds": round(sleep_for, 1),
                "last_result": last_result,
            })
            time.sleep(sleep_for)
