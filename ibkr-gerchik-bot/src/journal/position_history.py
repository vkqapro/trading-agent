"""Durable last-known broker position costs for execution reconciliation."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

from src.config import MEMORY_DIR


POSITION_HISTORY_PATH = MEMORY_DIR / "runtime" / "position_history.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read() -> Dict[str, Any]:
    try:
        if not POSITION_HISTORY_PATH.exists():
            return {"positions": {}}
        payload = json.loads(POSITION_HISTORY_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {"positions": {}}
    except Exception:
        return {"positions": {}}


def _write(payload: Dict[str, Any]) -> None:
    POSITION_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = POSITION_HISTORY_PATH.with_suffix(POSITION_HISTORY_PATH.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temp, POSITION_HISTORY_PATH)


def load_position_history() -> Dict[str, Dict[str, Any]]:
    positions = _read().get("positions", {})
    if not isinstance(positions, dict):
        return {}
    return {
        str(symbol).strip().upper(): dict(position)
        for symbol, position in positions.items()
        if str(symbol).strip() and isinstance(position, dict)
    }


def archive_positions(positions: Iterable[Dict[str, Any]]) -> None:
    """Merge current positions without deleting symbols that later disappear."""
    payload = _read()
    archived = payload.get("positions")
    if not isinstance(archived, dict):
        archived = {}
    changed = False
    captured_at = _now()
    for position in positions:
        if not isinstance(position, dict):
            continue
        symbol = str(position.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        previous = archived.get(symbol)
        previous = previous if isinstance(previous, dict) else {}
        record = {**previous, **position}
        record["symbol"] = symbol
        opened_at = position.get("opened_at") or previous.get("opened_at")
        if opened_at:
            record["opened_at"] = opened_at
        elif "opened_at" in record:
            record.pop("opened_at")
        record["first_seen_at"] = (
            previous.get("first_seen_at")
            or opened_at
            or captured_at
        )
        record["last_seen_at"] = captured_at
        if archived.get(symbol) != record:
            archived[symbol] = record
            changed = True
    if changed:
        payload["positions"] = archived
        payload["updated_at"] = captured_at
        _write(payload)
