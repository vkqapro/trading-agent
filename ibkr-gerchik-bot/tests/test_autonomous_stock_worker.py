from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.decision.provider_health import ProviderHealthResult
from src.jobs.autonomous_stock_preflight import PreflightResult
from src.jobs.autonomous_stock_worker import AutonomousStockWorker


def _ready(mode: str = "off") -> PreflightResult:
    return PreflightResult(
        True,
        mode,
        "DISABLED" if mode == "off" else "HEALTHY",
        "CONNECTED",
        mode in {"paper_autonomous", "ibkr_paper_autonomous", "live_autonomous"},
        True,
        (),
        "VERIFIED_PAPER",
        "VERIFIED",
        True,
        True,
        True,
    )


def _worker(tmp_path: Path, *, preflight_runner=None, job_runner=None) -> AutonomousStockWorker:
    worker = AutonomousStockWorker(
        runtime_dir=tmp_path,
        now_provider=lambda: datetime(2026, 9, 27, 12, 0),
        preflight_runner=preflight_runner or (lambda: _ready("ibkr_paper_autonomous")),
        job_runner=job_runner,
    )
    # The source refresh path has dedicated tests and must not scan real files
    # in lifecycle unit tests.
    worker._process_decision_lab = lambda phase: None
    return worker


def test_worker_never_invokes_legacy_session_jobs(tmp_path: Path):
    calls: list[tuple[str, dict[str, object]]] = []
    worker = _worker(
        tmp_path,
        job_runner=lambda job, context: calls.append((job, context)) or {"job": job},
    )
    worker.now_provider = iter(
        [
            datetime(2026, 9, 28, 9, 32),
            datetime(2026, 9, 28, 9, 36),
            datetime(2026, 9, 28, 10, 31),
        ]
    ).__next__

    assert worker.run_cycle() == "PREMARKET"
    assert worker.run_cycle() == "OPEN"
    assert worker.run_cycle() == "INTRADAY"
    assert calls == []


def test_worker_processes_decision_lab_while_market_is_closed(tmp_path: Path):
    calls: list[str] = []
    worker = _worker(tmp_path)
    worker._process_decision_lab = lambda phase: calls.append(phase)

    assert worker.run_cycle() == "MARKET_CLOSED"
    assert calls == ["MARKET_CLOSED"]
    payload = json.loads((tmp_path / "autonomous_stock_worker.json").read_text(encoding="utf-8"))
    assert payload["state"] == "MARKET_CLOSED"
    assert payload["current_session"] == "MARKET CLOSED"
    assert payload["broker_status"] == "CONNECTED"
    assert payload["account_status"] == "VERIFIED_PAPER"
    assert payload["allowlist_status"] == "VERIFIED"
    assert payload["broker_positions_readable"] is True
    assert payload["broker_open_orders_readable"] is True
    assert payload["broker_executions_readable"] is True
    assert payload["last_preflight_at"]
    assert payload["blocked_reason"] == "MARKET_CLOSED"
    assert "account_id" not in json.dumps(payload).lower()


def test_worker_heartbeat_exposes_sanitized_manual_queue_state(tmp_path: Path):
    from src.decision.strategy_control import create_run

    create_run(
        analysis_mode="manual",
        strategy_source="bmsb",
        scan_id="scan-1",
        snapshot_timestamp="2026-09-28T14:00:00+00:00",
        runtime_dir=tmp_path,
    )
    worker = _worker(tmp_path)
    worker.run_cycle()
    payload = json.loads((tmp_path / "autonomous_stock_worker.json").read_text(encoding="utf-8"))
    assert payload["manual_queue_depth"] == 1
    assert payload["active_manual_run_id"]
    assert payload["last_manual_run_state"] == "QUEUED"
    assert payload["active_manual_strategy"] == "bmsb"
    assert "DU" not in json.dumps(payload)


def test_failed_preflight_blocks_entries_without_starting_jobs(tmp_path: Path):
    calls: list[str] = []
    worker = _worker(
        tmp_path,
        preflight_runner=lambda: PreflightResult(
            False,
            "ibkr_paper_autonomous",
            "UNHEALTHY",
            "DISCONNECTED",
            False,
            False,
            ("provider_health_stale_or_missing", "broker_unavailable:ConnectionError"),
        ),
        job_runner=lambda job, context: calls.append(job) or {},
    )
    worker.now_provider = lambda: datetime(2026, 9, 28, 10, 31)

    worker.run_cycle()
    assert calls == []
    payload = json.loads((tmp_path / "autonomous_stock_worker.json").read_text(encoding="utf-8"))
    assert payload["state"] == "BLOCKED"
    assert payload["autonomous_entry_enabled"] is False
    assert payload["broker_status"] == "DISCONNECTED"
    assert payload["provider_status"] == "UNAVAILABLE"
    assert payload["blocked_reason"] == "BROKER_DISCONNECTED"


def test_allowlist_mismatch_is_sanitized_in_worker_heartbeat(tmp_path: Path):
    worker = _worker(
        tmp_path,
        preflight_runner=lambda: PreflightResult(
            False,
            "ibkr_paper_autonomous",
            "HEALTHY",
            "PAPER_VERIFIED",
            False,
            True,
            ("paper_account_not_allowlisted",),
            "VERIFIED_PAPER",
            "MISMATCH",
            True,
            True,
            True,
        ),
    )

    worker.run_cycle()
    payload = json.loads((tmp_path / "autonomous_stock_worker.json").read_text(encoding="utf-8"))
    assert payload["allowlist_status"] == "MISMATCH"
    assert payload["blocked_reason"] == "ALLOWLIST_MISMATCH"
    assert "paper_account_not_allowlisted" not in json.dumps(payload)
    assert "account_id" not in json.dumps(payload).lower()


def test_worker_auto_cadence_skips_duplicate_source_fingerprint(tmp_path: Path):
    source = {
        "analysis_snapshot_id": "analysis-current",
        "source_metadata": {"bmsb": {"fingerprint": "same-source"}},
        "sources": {"bmsb": []},
    }
    controller = MagicMock()
    controller.schedule_auto_from_snapshot.return_value = {"run_id": "auto-1"}
    with patch("src.decision.strategy_control.load_control", return_value={"analysis_mode": "auto", "strategy_source": "bmsb"}), \
         patch("src.decision.strategy_sources.refresh_current_source_snapshot", return_value=source), \
         patch("src.decision.strategy_controller.get_strategy_analysis_controller", return_value=controller):
        worker = AutonomousStockWorker(
            runtime_dir=tmp_path,
            now_provider=lambda: datetime(2026, 9, 27, 12, 0),
            preflight_runner=lambda: _ready("ibkr_paper_autonomous"),
        )
        worker._process_decision_lab("MARKET_CLOSED")
        worker._last_auto_provider = None
        worker._process_decision_lab("MARKET_CLOSED")

    assert controller.schedule_auto_from_snapshot.call_count == 1


def test_worker_provider_health_is_separate_from_decision_lab_processing(tmp_path: Path):
    health_calls: list[int] = []

    with patch("src.jobs.autonomous_stock_worker.run_preflight", return_value=_ready("ibkr_paper_autonomous")), \
         patch("src.decision.audit.DecisionAudit"):
        worker = AutonomousStockWorker(
            runtime_dir=tmp_path,
            now_provider=lambda: datetime(2026, 9, 28, 9, 36),
            provider_health_runner=lambda: health_calls.append(1) or ProviderHealthResult(
                status="CONNECTED",
                provider="deepseek",
                model="deepseek-flash",
                checked_at="2026-09-27T23:00:00+00:00",
                latency_ms=15.0,
            ),
        )
        worker._process_decision_lab = lambda phase: None
        worker.provider_health_interval_seconds = 300.0
        worker.run_cycle()
        worker.run_cycle()

    assert health_calls == [1]


def test_stopping_worker_releases_only_its_own_lock(tmp_path: Path):
    worker = _worker(tmp_path)
    worker.max_cycles = 1
    assert worker.run() == 0
    payload = json.loads((tmp_path / "autonomous_stock_worker.json").read_text(encoding="utf-8"))
    assert payload["state"] == "STOPPED"
    assert not (tmp_path / "autonomous_stock_worker.lock").exists()
    assert not (tmp_path / "autonomous_stock_worker.pid").exists()


def test_worker_source_contains_no_legacy_session_job_calls():
    source = Path(AutonomousStockWorker.__module__.replace(".", "/") + ".py")
    text = source.read_text(encoding="utf-8")
    for forbidden in (
        "run_premarket(",
        "run_open(",
        "run_intraday(",
        "run_entry_scan(",
        "market_session.lock",
        "intraday_session.lock",
    ):
        assert forbidden not in text
