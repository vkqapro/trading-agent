"""Persistence for read-only Crypto Screener snapshots and setup universes."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class CryptoScreenerStore:
    """Store completed crypto screener scans without storing execution intent."""

    def __init__(self, path: str | Path, *, max_age_hours: int = 48):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_age_hours = max(1, int(max_age_hours))
        self.initialize()

    def initialize(self) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS crypto_screener_snapshots (
                    scan_id TEXT PRIMARY KEY,
                    scan_timestamp TEXT NOT NULL,
                    universe_source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_crypto_screener_snapshots_latest "
                "ON crypto_screener_snapshots(universe_source, scan_timestamp DESC)"
            )

    def save(self, payload: dict[str, Any], *, universe_source: str = "ALL_UNIVERSE") -> str:
        source = str(universe_source or "ALL_UNIVERSE").upper()
        scan_timestamp = str(payload.get("scan_timestamp") or datetime.now(timezone.utc).isoformat())
        scan_id = f"crypto-screener:{scan_timestamp}:{uuid.uuid4().hex[:12]}"
        stored = dict(payload)
        stored.update({"scan_id": scan_id, "universe_source": source})
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO crypto_screener_snapshots "
                "(scan_id, scan_timestamp, universe_source, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (scan_id, scan_timestamp, source, json.dumps(stored, sort_keys=True), now),
            )
        return scan_id

    def _invalid(self, reason_code: str, reason: str) -> dict[str, Any]:
        return {
            "source": "CRYPTO_SCREENER",
            "available": False,
            "valid": False,
            "reason_code": reason_code,
            "reason": reason,
            "selected_symbols": [],
            "selected": [],
            "orders_submitted": 0,
        }

    def radar_setup_universe(self) -> dict[str, Any]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT scan_id, scan_timestamp, payload_json FROM crypto_screener_snapshots "
                "WHERE universe_source = 'ALL_UNIVERSE'"
            ).fetchall()
        parsed_rows: list[tuple[datetime, str, str]] = []
        for scan_id, scan_timestamp, raw_payload in rows:
            try:
                timestamp = datetime.fromisoformat(str(scan_timestamp).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            parsed_rows.append((timestamp, scan_id, raw_payload))
        if not parsed_rows:
            return self._invalid("CRYPTO_RADAR_EMPTY", "Crypto Screener has no completed ALL UNIVERSE scan.")
        timestamp, scan_id, raw_payload = max(parsed_rows, key=lambda item: item[0])
        scan_timestamp = timestamp.isoformat()
        try:
            payload = json.loads(raw_payload)
        except (TypeError, json.JSONDecodeError):
            return self._invalid("CRYPTO_RADAR_INVALID", "The latest Crypto Screener snapshot is invalid.")
        if datetime.now(timezone.utc) - timestamp > timedelta(hours=self.max_age_hours):
            metadata = self._invalid("CRYPTO_RADAR_STALE", "The latest Crypto Screener ALL UNIVERSE scan is stale.")
            metadata.update({"source_scan_id": scan_id, "snapshot_timestamp": scan_timestamp})
            return metadata

        selected: list[dict[str, Any]] = []
        by_symbol: dict[str, dict[str, Any]] = {}
        for signal in payload.get("signals") or []:
            if not isinstance(signal, dict):
                continue
            symbol = str(signal.get("ticker") or signal.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            item = {
                "symbol": symbol,
                "scanner_status": str(signal.get("status") or "CANDIDATE").upper(),
                "scanner_label": f"{signal.get('strategy') or 'UNKNOWN'} {signal.get('side') or 'UNKNOWN'}",
                "strategy": signal.get("strategy"),
                "side": signal.get("side"),
                "score": signal.get("score"),
                "as_of": payload.get("anchor_date"),
                "created_at": scan_timestamp,
                "scan_timestamp": scan_timestamp,
                "source_scan_id": scan_id,
            }
            existing = by_symbol.get(symbol)
            if existing is None or float(item.get("score") or 0) > float(existing.get("score") or 0):
                by_symbol[symbol] = item
        selected = list(by_symbol.values())
        selected.sort(key=lambda item: (-float(item.get("score") or 0), item["symbol"]))
        if not selected:
            metadata = self._invalid(
                "CRYPTO_RADAR_NO_ELIGIBLE_SETUPS",
                "The latest Crypto Screener scan has no LP/PRB setup signals.",
            )
            metadata.update({"source_scan_id": scan_id, "snapshot_timestamp": scan_timestamp})
            return metadata

        return {
            "source": "CRYPTO_SCREENER",
            "available": True,
            "valid": True,
            "reason_code": None,
            "reason": None,
            "source_scan_id": scan_id,
            "snapshot_as_of": payload.get("anchor_date"),
            "snapshot_timestamp": scan_timestamp,
            "eligible_symbol_count": len(selected),
            "selected_symbols": [item["symbol"] for item in selected],
            "selected": selected,
            "total_snapshot_rows": len(payload.get("signals") or []),
            "orders_submitted": 0,
        }
