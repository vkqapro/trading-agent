from __future__ import annotations

import pytest

from src.decision.strategy_control import (
    create_run,
    list_runs,
    load_snapshot,
    control_payload,
    load_control,
    save_snapshot,
    update_control,
)


def test_control_defaults_to_manual_and_supports_all(tmp_path):
    control = load_control(runtime_dir=tmp_path)
    assert control["analysis_mode"] == "manual"
    assert control["strategy_source"] == "gerchik_router"
    updated = update_control(
        analysis_mode="auto",
        strategy_source="all",
        runtime_dir=tmp_path,
    )
    assert updated["analysis_mode"] == "auto"
    assert updated["strategy_source"] == "all"


def test_control_rejects_unknown_values(tmp_path):
    with pytest.raises(ValueError):
        update_control(analysis_mode="timer", strategy_source="bmsb", runtime_dir=tmp_path)
    with pytest.raises(ValueError):
        update_control(analysis_mode="manual", strategy_source="unknown", runtime_dir=tmp_path)


def test_auto_run_is_idempotent_by_source_and_scan(tmp_path):
    first = create_run(
        analysis_mode="auto", strategy_source="gerchik_router", scan_id="scan-1", runtime_dir=tmp_path
    )
    second = create_run(
        analysis_mode="auto", strategy_source="gerchik_router", scan_id="scan-1", runtime_dir=tmp_path
    )
    assert second["run_id"] == first["run_id"]
    assert len(list_runs(runtime_dir=tmp_path)) == 1


def test_only_one_manual_run_can_be_active(tmp_path):
    create_run(
        analysis_mode="manual",
        strategy_source="bmsb",
        scan_id="scan-1",
        snapshot_timestamp="2026-09-28T14:00:00+00:00",
        runtime_dir=tmp_path,
    )
    with pytest.raises(RuntimeError, match="already active"):
        create_run(
            analysis_mode="manual",
            strategy_source="gaussian",
            scan_id="scan-2",
            snapshot_timestamp="2026-09-28T14:01:00+00:00",
            runtime_dir=tmp_path,
        )


def test_manual_runs_require_snapshot_identity(tmp_path):
    with pytest.raises(ValueError, match="scan_id is required"):
        create_run(analysis_mode="manual", strategy_source="bmsb", scan_id=None, runtime_dir=tmp_path)
    with pytest.raises(ValueError, match="snapshot_timestamp is required"):
        create_run(analysis_mode="manual", strategy_source="bmsb", scan_id="scan-1", runtime_dir=tmp_path)


def test_old_orphaned_queue_is_failed_closed(tmp_path):
    from datetime import datetime, timedelta, timezone
    import json

    runs_path = tmp_path / "decision_lab_runs.json"
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    runs_path.write_text(
        json.dumps([{
            "run_id": "orphan",
            "analysis_mode": "manual",
            "strategy_selection": "bmsb",
            "status": "QUEUED",
            "scan_id": "scan-old",
            "requested_at": old,
        }]),
        encoding="utf-8",
    )
    runs = list_runs(runtime_dir=tmp_path)
    assert runs[0]["status"] == "FAILED"
    assert runs[0]["error"] == "STALE_QUEUE_REQUEST"


def test_completed_snapshots_are_versioned_for_manual_run_races(tmp_path):
    save_snapshot(
        {"scan_id": "scan-1", "completed_at": "now", "sources": {"bmsb": []}},
        runtime_dir=tmp_path,
    )
    save_snapshot(
        {"scan_id": "scan-2", "completed_at": "later", "sources": {"bmsb": []}},
        runtime_dir=tmp_path,
    )
    assert load_snapshot(runtime_dir=tmp_path)["scan_id"] == "scan-2"
    assert load_snapshot(runtime_dir=tmp_path, scan_id="scan-1")["scan_id"] == "scan-1"


def test_control_resolves_selected_source_snapshot_and_zero_candidates(tmp_path):
    save_snapshot(
        {
            "scan_id": "scan-source",
            "completed_at": "2026-09-28T14:00:00+00:00",
            "source_counts": {"bmsb": 0, "gaussian": 1},
            "source_status": {"bmsb": "NO_CANDIDATES", "gaussian": "READY"},
            "sources": {"bmsb": [], "gaussian": [{"symbol": "AAPL"}]},
        },
        runtime_dir=tmp_path,
    )
    update_control(analysis_mode="manual", strategy_source="bmsb", runtime_dir=tmp_path)
    bmsb = control_payload(runtime_dir=tmp_path)
    assert bmsb["selected_source_snapshot"]["available"] is True
    assert bmsb["selected_source_snapshot"]["candidate_count"] == 0
    update_control(analysis_mode="manual", strategy_source="gaussian", runtime_dir=tmp_path)
    gaussian = control_payload(runtime_dir=tmp_path)
    assert gaussian["selected_source_snapshot"]["available"] is True
    assert gaussian["selected_source_snapshot"]["candidate_count"] == 1


def test_active_run_remains_source_immutable_when_selection_changes(tmp_path):
    from src.decision.strategy_control import control_payload, update_run

    bmsb = create_run(
        analysis_mode="manual",
        strategy_source="bmsb",
        scan_id="scan-bmsb",
        snapshot_timestamp="2026-09-28T14:00:00+00:00",
        runtime_dir=tmp_path,
    )
    update_run(bmsb["run_id"], runtime_dir=tmp_path, status="RUNNING", started_at="2026-09-28T14:01:00+00:00")
    update_control(analysis_mode="manual", strategy_source="gaussian", runtime_dir=tmp_path)
    payload = control_payload(runtime_dir=tmp_path)
    assert payload["active_run"]["strategy_selection"] == "bmsb"
    assert payload["strategy_source"] == "gaussian"
    assert payload["last_run"] is None
