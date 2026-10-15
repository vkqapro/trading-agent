"""SQLite persistence for deterministic Trend Radar observations."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from src.radar.models import RadarResult, RadarScannerResult


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
                CREATE TABLE IF NOT EXISTS radar_scanner_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    radar_as_of TEXT NOT NULL,
                    source_scan_id TEXT NOT NULL,
                    scan_timestamp TEXT NOT NULL,
                    scanner_status TEXT NOT NULL,
                    scanner_label TEXT,
                    strategy TEXT,
                    side TEXT,
                    signals_found INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(symbol, radar_as_of)
                );
                CREATE INDEX IF NOT EXISTS idx_radar_scanner_results_as_of ON radar_scanner_results(radar_as_of);
                CREATE INDEX IF NOT EXISTS idx_radar_scanner_results_status ON radar_scanner_results(radar_as_of, scanner_status);
                CREATE INDEX IF NOT EXISTS idx_radar_scanner_results_scan_time ON radar_scanner_results(scan_timestamp);
                CREATE TABLE IF NOT EXISTS daily_trader_scan_jobs (
                    scan_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    request_key TEXT NOT NULL,
                    idempotency_key TEXT,
                    trigger_source TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    radar_as_of TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    processed_count INTEGER NOT NULL DEFAULT 0,
                    persisted_result_count INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_daily_trader_scan_jobs_status ON daily_trader_scan_jobs(status, updated_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_trader_scan_jobs_idempotency
                    ON daily_trader_scan_jobs(idempotency_key)
                    WHERE idempotency_key IS NOT NULL AND idempotency_key <> '';
                CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_trader_scan_jobs_active_request
                    ON daily_trader_scan_jobs(request_key)
                    WHERE status IN ('QUEUED', 'RUNNING');
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

    @staticmethod
    def _decode_scanner(row: sqlite3.Row | None) -> RadarScannerResult | None:
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
            return RadarScannerResult.from_mapping(
                {
                    "symbol": row["symbol"],
                    "radar_as_of": row["radar_as_of"],
                    "source_scan_id": row["source_scan_id"],
                    "scan_timestamp": row["scan_timestamp"],
                    "scanner_status": row["scanner_status"],
                    "scanner_label": row["scanner_label"],
                    "strategy": row["strategy"],
                    "side": row["side"],
                    "signals_found": row["signals_found"],
                    "payload": payload,
                    "error": row["error"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def upsert_scanner_result(self, result: RadarScannerResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        created_at = result.created_at or now
        updated_at = result.updated_at or now
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO radar_scanner_results(
                    symbol, radar_as_of, source_scan_id, scan_timestamp,
                    scanner_status, scanner_label, strategy, side, signals_found,
                    payload_json, error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, radar_as_of) DO UPDATE SET
                    source_scan_id = excluded.source_scan_id,
                    scan_timestamp = excluded.scan_timestamp,
                    scanner_status = excluded.scanner_status,
                    scanner_label = excluded.scanner_label,
                    strategy = excluded.strategy,
                    side = excluded.side,
                    signals_found = excluded.signals_found,
                    payload_json = excluded.payload_json,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                WHERE excluded.scan_timestamp >= radar_scanner_results.scan_timestamp
                """,
                (
                    str(result.symbol).upper(),
                    result.radar_as_of,
                    result.source_scan_id,
                    result.scan_timestamp,
                    str(result.scanner_status or "").upper(),
                    result.scanner_label,
                    result.strategy,
                    result.side,
                    int(result.signals_found or 0),
                    json.dumps(result.payload or {}, sort_keys=True),
                    result.error,
                    created_at,
                    updated_at,
                ),
            )

    def latest_scanner_result(self, symbol: str, radar_as_of: str) -> RadarScannerResult | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM radar_scanner_results
                 WHERE symbol = ? AND radar_as_of = ?
                 ORDER BY scan_timestamp DESC LIMIT 1
                """,
                (str(symbol).upper(), radar_as_of),
            ).fetchone()
        return self._decode_scanner(row)

    def latest_scanner_results(self, radar_as_of: str, symbols: Iterable[str] | None = None) -> dict[str, RadarScannerResult]:
        params: list[Any] = [radar_as_of]
        query = "SELECT * FROM radar_scanner_results WHERE radar_as_of = ?"
        normalized_symbols = sorted({str(symbol).upper() for symbol in (symbols or []) if str(symbol).strip()})
        if normalized_symbols:
            placeholders = ",".join("?" for _ in normalized_symbols)
            query += f" AND symbol IN ({placeholders})"
            params.extend(normalized_symbols)
        with self.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        results: dict[str, RadarScannerResult] = {}
        for row in rows:
            decoded = self._decode_scanner(row)
            if decoded is not None:
                results[decoded.symbol] = decoded
        return results

    @staticmethod
    def _decode_scan_job(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        job = dict(row)
        for key in ("request_json", "result_json", "error_json"):
            value = job.get(key)
            if value:
                try:
                    job[key[:-5] if key.endswith("_json") else key] = json.loads(value)
                except (TypeError, ValueError, json.JSONDecodeError):
                    job[key[:-5] if key.endswith("_json") else key] = None
            else:
                job[key[:-5] if key.endswith("_json") else key] = None
            job.pop(key, None)
        return job

    def reconcile_interrupted_daily_trader_scan_jobs(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        error = {
            "reason_code": "DASHBOARD_RESTART_INTERRUPTED_SCAN",
            "message": "Dashboard process restarted before the scan reached a terminal state.",
        }
        with self.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE daily_trader_scan_jobs
                   SET status = 'FAILED',
                       completed_at = COALESCE(completed_at, ?),
                       updated_at = ?,
                       error_json = COALESCE(error_json, ?)
                 WHERE status IN ('QUEUED', 'RUNNING')
                """,
                (now, now, json.dumps(error, sort_keys=True)),
            )
            return int(cursor.rowcount or 0)

    def create_or_reuse_daily_trader_scan_job(
        self,
        *,
        scan_id: str,
        request_key: str,
        request: dict[str, Any],
        idempotency_key: str | None,
        trigger_source: str,
        radar_as_of: str | None,
    ) -> tuple[dict[str, Any], bool]:
        now = datetime.now(timezone.utc).isoformat()
        normalized_idempotency = str(idempotency_key or "").strip() or None
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if normalized_idempotency:
                existing = connection.execute(
                    "SELECT * FROM daily_trader_scan_jobs WHERE idempotency_key = ? LIMIT 1",
                    (normalized_idempotency,),
                ).fetchone()
                if existing is not None:
                    return self._decode_scan_job(existing) or {}, False
            existing = connection.execute(
                """
                SELECT * FROM daily_trader_scan_jobs
                 WHERE request_key = ? AND status IN ('QUEUED', 'RUNNING')
                 ORDER BY created_at ASC LIMIT 1
                """,
                (request_key,),
            ).fetchone()
            if existing is not None:
                return self._decode_scan_job(existing) or {}, False
            connection.execute(
                """
                INSERT INTO daily_trader_scan_jobs(
                    scan_id, status, request_key, idempotency_key, trigger_source,
                    request_json, radar_as_of, created_at, updated_at
                )
                VALUES (?, 'QUEUED', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_id,
                    request_key,
                    normalized_idempotency,
                    trigger_source,
                    json.dumps(request, sort_keys=True),
                    radar_as_of,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM daily_trader_scan_jobs WHERE scan_id = ?",
                (scan_id,),
            ).fetchone()
            return self._decode_scan_job(row) or {}, True

    def get_daily_trader_scan_job(self, scan_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM daily_trader_scan_jobs WHERE scan_id = ?",
                (str(scan_id),),
            ).fetchone()
        return self._decode_scan_job(row)

    def get_latest_completed_daily_trader_scan_job(self) -> dict[str, Any] | None:
        """Return the newest completed job with a valid persisted result."""
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM daily_trader_scan_jobs
                 WHERE status = 'COMPLETED'
                   AND result_json IS NOT NULL
                 ORDER BY completed_at DESC, updated_at DESC, created_at DESC, scan_id DESC
                """
            ).fetchall()
        for row in rows:
            job = self._decode_scan_job(row)
            result = job.get("result") if isinstance(job, dict) else None
            if (
                isinstance(result, dict)
                and result.get("radar_as_of")
                and isinstance(result.get("active"), list)
                and isinstance(result.get("terminal"), list)
            ):
                return job
        return None

    def mark_daily_trader_scan_job_running(self, scan_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE daily_trader_scan_jobs
                   SET status = 'RUNNING',
                       started_at = COALESCE(started_at, ?),
                       updated_at = ?
                 WHERE scan_id = ? AND status = 'QUEUED'
                """,
                (now, now, scan_id),
            )

    def complete_daily_trader_scan_job(
        self,
        scan_id: str,
        *,
        result: dict[str, Any],
        processed_count: int,
        persisted_result_count: int,
        radar_as_of: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE daily_trader_scan_jobs
                   SET status = 'COMPLETED',
                       completed_at = ?,
                       updated_at = ?,
                       processed_count = ?,
                       persisted_result_count = ?,
                       radar_as_of = COALESCE(?, radar_as_of),
                       result_json = ?,
                       error_json = NULL
                 WHERE scan_id = ?
                """,
                (
                    now,
                    now,
                    int(processed_count),
                    int(persisted_result_count),
                    radar_as_of,
                    json.dumps(result, sort_keys=True),
                    scan_id,
                ),
            )

    def fail_daily_trader_scan_job(
        self,
        scan_id: str,
        *,
        error: dict[str, Any],
        processed_count: int = 0,
        persisted_result_count: int = 0,
        radar_as_of: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE daily_trader_scan_jobs
                   SET status = 'FAILED',
                       completed_at = ?,
                       updated_at = ?,
                       processed_count = ?,
                       persisted_result_count = ?,
                       radar_as_of = COALESCE(?, radar_as_of),
                       error_json = ?
                 WHERE scan_id = ?
                """,
                (
                    now,
                    now,
                    int(processed_count),
                    int(persisted_result_count),
                    radar_as_of,
                    json.dumps(error, sort_keys=True),
                    scan_id,
                ),
            )
