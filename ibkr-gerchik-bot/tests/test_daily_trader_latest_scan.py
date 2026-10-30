from __future__ import annotations

import json

from src.radar.models import RadarResult
from src.radar.service import RadarService
from src.storage.trend_radar_store import TrendRadarStore


def _insert_job(store: TrendRadarStore, scan_id: str, status: str, *, completed_at: str, result_json: str | None):
    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO daily_trader_scan_jobs(
                scan_id, status, request_key, trigger_source, request_json,
                completed_at, created_at, updated_at, result_json
            ) VALUES (?, ?, ?, 'manual', '{}', ?, ?, ?, ?)
            """,
            (scan_id, status, scan_id, completed_at, completed_at, completed_at, result_json),
        )


def _result(as_of: str = "2026-10-14") -> str:
    return json.dumps({"radar_as_of": as_of, "active": [], "terminal": []})


def test_latest_completed_scan_uses_valid_payload_and_deterministic_tiebreaker(tmp_path):
    store = TrendRadarStore(tmp_path / "decision.db")
    _insert_job(store, "00000000-0000-0000-0000-000000000001", "RUNNING", completed_at="2026-10-15T00:00:00Z", result_json=None)
    _insert_job(store, "00000000-0000-0000-0000-000000000002", "FAILED", completed_at="2026-10-16T00:00:00Z", result_json=_result())
    _insert_job(store, "00000000-0000-0000-0000-000000000003", "COMPLETED", completed_at="2026-10-17T00:00:00Z", result_json="not-json")
    _insert_job(store, "00000000-0000-0000-0000-000000000004", "COMPLETED", completed_at="2026-10-18T00:00:00Z", result_json=_result())
    _insert_job(store, "00000000-0000-0000-0000-000000000005", "COMPLETED", completed_at="2026-10-18T00:00:00Z", result_json=_result())

    latest = store.get_latest_completed_daily_trader_scan_job()

    assert latest is not None
    assert latest["scan_id"] == "00000000-0000-0000-0000-000000000005"
    assert latest["status"] == "COMPLETED"
    assert latest["result"]["radar_as_of"] == "2026-10-14"


def test_latest_completed_scan_returns_none_when_no_valid_completed_payload(tmp_path):
    store = TrendRadarStore(tmp_path / "decision.db")
    _insert_job(store, "00000000-0000-0000-0000-000000000001", "RUNNING", completed_at="2026-10-15T00:00:00Z", result_json=None)
    _insert_job(store, "00000000-0000-0000-0000-000000000002", "FAILED", completed_at="2026-10-16T00:00:00Z", result_json=_result())
    _insert_job(store, "00000000-0000-0000-0000-000000000003", "COMPLETED", completed_at="2026-10-17T00:00:00Z", result_json=json.dumps({"active": []}))
    _insert_job(store, "00000000-0000-0000-0000-000000000004", "COMPLETED", completed_at="2026-10-18T00:00:00Z", result_json=json.dumps({"radar_as_of": "2026-10-14", "active": []}))

    assert store.get_latest_completed_daily_trader_scan_job() is None
