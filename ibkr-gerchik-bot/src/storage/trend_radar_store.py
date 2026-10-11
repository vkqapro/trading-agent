"""SQLite persistence for deterministic Trend Radar observations."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from src.radar.models import RadarResult


class TrendRadarStore:
    """Persist radar snapshots in the application's existing SQLite database."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS radar_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(symbol, as_of)
                );
                CREATE INDEX IF NOT EXISTS idx_radar_snapshots_as_of ON radar_snapshots(as_of);
                CREATE INDEX IF NOT EXISTS idx_radar_snapshots_trend ON radar_snapshots(symbol, as_of, json_extract(payload_json, '$.trend'));
                CREATE INDEX IF NOT EXISTS idx_radar_snapshots_score ON radar_snapshots(json_extract(payload_json, '$.radar_score'));
                """
            )

    def upsert(self, result: RadarResult) -> None:
        created_at = result.created_at or datetime.now(timezone.utc).isoformat()
        payload = result.to_dict()
        payload["created_at"] = created_at
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO radar_snapshots(symbol, as_of, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol, as_of) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at
                """,
                (result.symbol, result.as_of, json.dumps(payload, sort_keys=True), created_at),
            )

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> RadarResult | None:
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
            return RadarResult.from_mapping(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def previous_trend(self, symbol: str, as_of: str) -> str | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM radar_snapshots WHERE symbol = ? AND as_of < ? ORDER BY as_of DESC LIMIT 1",
                (str(symbol).upper(), as_of),
            ).fetchone()
        result = self._decode(row)
        return result.trend if result else None

    def latest(self, symbol: str) -> RadarResult | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM radar_snapshots WHERE symbol = ? ORDER BY as_of DESC LIMIT 1",
                (str(symbol).upper(),),
            ).fetchone()
        return self._decode(row)

    def latest_results(self, limit: int = 1000) -> list[RadarResult]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT s.payload_json
                  FROM radar_snapshots s
                  JOIN (SELECT symbol, MAX(as_of) AS max_as_of FROM radar_snapshots GROUP BY symbol) latest
                    ON latest.symbol = s.symbol AND latest.max_as_of = s.as_of
                 ORDER BY json_extract(s.payload_json, '$.radar_score') DESC, s.symbol ASC
                 LIMIT ?
                """,
                (max(1, min(int(limit), 5000)),),
            ).fetchall()
        return [decoded for row in rows if (decoded := self._decode(row)) is not None]

    def snapshots(self, symbol: str | None = None, limit: int = 5000) -> list[RadarResult]:
        query = "SELECT payload_json FROM radar_snapshots"
        params: list[Any] = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(str(symbol).upper())
        query += " ORDER BY as_of DESC, symbol ASC LIMIT ?"
        params.append(max(1, min(int(limit), 10_000)))
        with self.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [decoded for row in rows if (decoded := self._decode(row)) is not None]
