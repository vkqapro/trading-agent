"""SQLite persistence for account-specific crypto Radar snapshots."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from .models import CryptoRadarResult

class CryptoRadarStore:
    def __init__(self, path: str | Path):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True); self.initialize()
    def initialize(self) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS crypto_radar_snapshots (identity_key TEXT NOT NULL, as_of TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(identity_key, as_of))""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_crypto_radar_score ON crypto_radar_snapshots(json_extract(payload_json, '$.radar_score'))")
    def upsert(self, result: CryptoRadarResult) -> None:
        now = result.created_at or datetime.now(timezone.utc).isoformat()
        payload = result.to_dict(); payload["created_at"] = now
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO crypto_radar_snapshots(identity_key,as_of,payload_json,created_at) VALUES(?,?,?,?) ON CONFLICT(identity_key,as_of) DO UPDATE SET payload_json=excluded.payload_json,created_at=excluded.created_at", (result.contract.identity_key, result.as_of, json.dumps(payload, sort_keys=True), now))
    def latest(self, *, symbol: str | None = None, con_id: int | None = None, provider: str | None = None, limit: int = 1000) -> list[dict]:
        clauses=[]; args=[]
        if symbol: clauses.append("json_extract(payload_json,'$.symbol')=?"); args.append(symbol.upper())
        if con_id is not None: clauses.append("json_extract(payload_json,'$.conId')=?"); args.append(con_id)
        if provider: clauses.append("upper(json_extract(payload_json,'$.provider'))=?"); args.append(provider.upper())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        bounded = max(1, min(int(limit), 5000))
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows=db.execute(f"SELECT payload_json FROM crypto_radar_snapshots{where} ORDER BY as_of DESC, created_at DESC LIMIT 5000", args).fetchall()
        now = datetime.now(timezone.utc)
        latest_by_identity: dict[tuple[object, str], dict] = {}
        for row in rows:
            payload = json.loads(row["payload_json"])
            raw_as_of = payload.get("as_of")
            if raw_as_of:
                try:
                    as_of = datetime.fromisoformat(str(raw_as_of).replace("Z", "+00:00"))
                    if as_of > now:
                        continue
                except ValueError:
                    continue
            identity = (payload.get("instId") or payload.get("conId"), str(payload.get("provider") or "").upper())
            latest_by_identity.setdefault(identity, payload)
        return sorted(latest_by_identity.values(), key=lambda r: (-float(r.get("radar_score") or 0), str(r.get("symbol") or "")))[:bounded]
    def previous_trend(self, identity_key: str, as_of: str) -> str | None:
        with sqlite3.connect(self.path) as db:
            row=db.execute("SELECT json_extract(payload_json,'$.trend') FROM crypto_radar_snapshots WHERE identity_key=? AND as_of<? ORDER BY as_of DESC LIMIT 1", (identity_key,as_of)).fetchone()
        return row[0] if row else None
