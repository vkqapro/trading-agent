"""Report resident-worker startup evidence for the Windows launcher.

This is read-only: it waits for the worker heartbeat/lock and never connects
to IBKR or a provider. A blocked worker is reported as alive-but-blocked rather
than being advertised as ready.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "memory" / "runtime"
HEARTBEAT = RUNTIME / "autonomous_stock_worker.json"
LOCK = RUNTIME / "autonomous_stock_worker.lock"


def _age(value: object) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


def _alive(pid: object) -> bool:
    try:
        value = int(pid)
        if value <= 0:
            return False
        os.kill(value, 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only autonomous worker startup evidence")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    deadline = time.monotonic() + max(0.0, args.timeout)
    while time.monotonic() <= deadline:
        try:
            payload = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        age = _age(payload.get("last_heartbeat")) if isinstance(payload, dict) else None
        pid = payload.get("pid") if isinstance(payload, dict) else None
        lock_owner = ""
        try:
            lock_owner = LOCK.read_text(encoding="utf-8").split("|", 1)[0].strip()
        except OSError:
            pass
        if (
            isinstance(payload, dict)
            and payload.get("service") == "autonomous_stock_worker"
            and age is not None
            and age <= 20.0
            and _alive(pid)
            and lock_owner == str(pid)
        ):
            state = str(payload.get("state") or "UNKNOWN").upper()
            session = str(payload.get("current_session") or "UNKNOWN")
            entries = bool(payload.get("autonomous_entry_enabled", False))
            if state in {"READY", "MARKET CLOSED", "DEGRADED"}:
                print(f"[OK] Autonomous Stock Worker heartbeat state={state} session={session} entries={'ENABLED' if entries else 'BLOCKED'}")
                return 0
            print(f"[WARN] Autonomous Stock Worker is alive but state={state} session={session}")
            return 0
        time.sleep(0.25)
    print("[WARN] Autonomous Stock Worker heartbeat not confirmed; dashboard will report its authoritative state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
