from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

from ops.trading_platform_ops import ProcessInfo, Supervisor


def _market_data_spec(tmp_path: Path):
    from ops.trading_platform_ops import ServiceSpec

    runtime = tmp_path / "memory" / "runtime"
    runtime.mkdir(parents=True)
    return ServiceSpec("market_data", "Market Data", ("--job market_data",), None, runtime / "market_data_collector_status.json", 900.0)


def _write_market_data_status(spec, *, age_seconds=30, market_data_status="healthy", **quality):
    stamp = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    payload = {
        "market_data_status": market_data_status,
        "last_cycle_at": stamp.isoformat(),
        "latest_fresh_cycle_at": stamp.isoformat(),
        "cycle": 4,
        "health": {"requested": 194, "received": 194, "fresh": 194, "advanced": 194, "stale": 0, "no_data": 0, "errors": 0},
    }
    payload["health"].update(quality)
    spec.heartbeat.write_text(json.dumps(payload), encoding="utf-8")


def _supervisor(monkeypatch, processes, *, broker=None, safety=None, services=None):
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", Path(__file__).parents[1])
    monkeypatch.setattr("ops.trading_platform_ops._specs", lambda: tuple(services or ()))
    monkeypatch.setattr(Supervisor, "_broker", lambda self: broker or {"status": "HEALTHY", "gateway_running": True, "socket_reachable": True, "account_identity_verified": True})
    monkeypatch.setattr(Supervisor, "_safety", lambda self: safety or {"status": "HEALTHY", "paper_trading": True, "dry_run_mode": True, "live_trading_enabled": False})
    monkeypatch.setattr(Supervisor, "_execution_boundary", lambda self: {"safe_to_start": True, "status": "SAFE_TO_START"})
    monkeypatch.setattr(Supervisor, "_api", lambda self, url, label, method="GET": {"service": label, "status": "HEALTHY", "read_only": True})
    return Supervisor(process_provider=lambda: list(processes), sleep=lambda _: None)


def test_everything_already_healthy_does_not_start(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec

    specs = (ServiceSpec("execute_worker", "Execute Worker", ("execute_requests",), None),)
    processes = [ProcessInfo(11, "python -m src.main --job execute_requests")]
    sup = _supervisor(monkeypatch, processes, services=specs)
    monkeypatch.setattr("ops.trading_platform_ops._start", lambda spec: (_ for _ in ()).throw(AssertionError("started healthy service")))
    result = sup.run(start_missing=True)
    assert result["overall_status"] == "READY_FOR_MARKET_OPEN"
    assert result["services"]["execute_worker"]["pids"] == [11]


def test_duplicate_worker_is_reported_and_not_killed(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec

    specs = (ServiceSpec("execute_worker", "Execute Worker", ("execute_requests",), None),)
    sup = _supervisor(monkeypatch, [ProcessInfo(11, "execute_requests"), ProcessInfo(12, "execute_requests")], services=specs)
    result = sup.run(start_missing=True)
    row = result["services"]["execute_worker"]
    assert row["status"] == "DUPLICATE_PROCESS"
    assert row["pids"] == [11, 12]
    assert result["duplicates"] == ["execute_worker"]


def test_broker_unavailable_blocks_ready(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec

    specs = (ServiceSpec("execute_worker", "Execute Worker", ("execute_requests",), None),)
    sup = _supervisor(monkeypatch, [ProcessInfo(11, "execute_requests")], broker={"status": "ACTION_REQUIRED", "reason_code": "BROKER_SOCKET_UNREACHABLE"}, services=specs)
    result = sup.run(start_missing=False)
    assert result["overall_status"] == "ACTION_REQUIRED"
    assert result["broker"]["reason_code"] == "BROKER_SOCKET_UNREACHABLE"


def test_safety_mismatch_blocks_ready(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec

    specs = (ServiceSpec("execute_worker", "Execute Worker", ("execute_requests",), None),)
    sup = _supervisor(monkeypatch, [ProcessInfo(11, "execute_requests")], safety={"status": "MISMATCH", "paper_trading": False, "dry_run_mode": False, "live_trading_enabled": True}, services=specs)
    result = sup.run(start_missing=False)
    assert result["overall_status"] == "ACTION_REQUIRED"
    assert "SAFETY_CONFIGURATION_MISMATCH" in result["action_required"]


def test_repeat_run_is_idempotent(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec

    specs = (ServiceSpec("execute_worker", "Execute Worker", ("execute_requests",), "run_execute_worker.cmd"),)
    processes = [ProcessInfo(11, "execute_requests")]
    launches = []
    sup = _supervisor(monkeypatch, processes, services=specs)
    monkeypatch.setattr("ops.trading_platform_ops._start", lambda spec: launches.append(spec.key))
    first = sup.run(start_missing=True)
    second = sup.run(start_missing=True)
    assert first["services"]["execute_worker"]["status"] == "HEALTHY"
    assert second["services"]["execute_worker"]["status"] == "HEALTHY"
    assert launches == []


def test_wrapper_and_child_are_one_logical_instance(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec

    specs = (ServiceSpec("execute_worker", "Execute Worker", ("execute_requests",), None),)
    processes = [
        ProcessInfo(100, "cmd.exe /c run_execute_worker.cmd", None, "cmd.exe"),
        ProcessInfo(101, "python -m src.main --job execute_requests", 100, "python.exe"),
    ]
    result = _supervisor(monkeypatch, processes, services=specs).run(start_missing=False)
    row = result["services"]["execute_worker"]
    assert row["logical_instances"] == 1
    assert row["status"] == "HEALTHY"


def test_market_data_uses_fresh_status_artifact_not_old_lock(tmp_path, monkeypatch):
    from ops.trading_platform_ops import Supervisor

    spec = _market_data_spec(tmp_path)
    _write_market_data_status(spec)
    lock = spec.heartbeat.with_name("market_data_collector.lock")
    lock.write_text("11|old", encoding="utf-8")
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", tmp_path)
    row = Supervisor(sleep=lambda _: None)._service(spec, start_missing=False, processes=[ProcessInfo(11, "--job market_data")], actions=[])
    assert row["status"] == "HEALTHY"
    assert row["heartbeat"]["path"].endswith("market_data_collector_status.json")


def test_market_data_degraded_quality_is_operational(tmp_path, monkeypatch):
    from ops.trading_platform_ops import Supervisor

    spec = _market_data_spec(tmp_path)
    _write_market_data_status(spec, market_data_status="degraded", fresh=134, stale=57, no_data=3)
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", tmp_path)
    row = Supervisor(sleep=lambda _: None)._service(spec, start_missing=False, processes=[ProcessInfo(11, "--job market_data")], actions=[])
    assert row["status"] == "DEGRADED"
    assert row["heartbeat"]["data_quality"] == "DEGRADED"
    assert row["heartbeat"]["quality"]["stale"] == 57


def test_market_data_long_cycle_within_window_is_not_unavailable(tmp_path, monkeypatch):
    from ops.trading_platform_ops import Supervisor

    spec = _market_data_spec(tmp_path)
    _write_market_data_status(spec, age_seconds=840)
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", tmp_path)
    row = Supervisor(sleep=lambda _: None)._service(spec, start_missing=False, processes=[ProcessInfo(11, "--job market_data")], actions=[])
    assert row["status"] == "HEALTHY"


def test_market_data_missing_process_is_missing(tmp_path, monkeypatch):
    from ops.trading_platform_ops import Supervisor

    spec = _market_data_spec(tmp_path)
    _write_market_data_status(spec)
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", tmp_path)
    row = Supervisor(sleep=lambda _: None)._service(spec, start_missing=False, processes=[], actions=[])
    assert row["status"] == "MISSING"


def test_market_data_stale_status_is_unavailable(tmp_path, monkeypatch):
    from ops.trading_platform_ops import Supervisor

    spec = _market_data_spec(tmp_path)
    _write_market_data_status(spec, age_seconds=901)
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", tmp_path)
    row = Supervisor(sleep=lambda _: None)._service(spec, start_missing=False, processes=[ProcessInfo(11, "--job market_data")], actions=[])
    assert row["status"] == "UNAVAILABLE"


def test_market_data_malformed_status_fails_closed(tmp_path, monkeypatch):
    from ops.trading_platform_ops import Supervisor

    spec = _market_data_spec(tmp_path)
    spec.heartbeat.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", tmp_path)
    row = Supervisor(sleep=lambda _: None)._service(spec, start_missing=False, processes=[ProcessInfo(11, "--job market_data")], actions=[])
    assert row["status"] == "UNAVAILABLE"
    assert row["reason_code"] == "STATUS_ARTIFACT_UNREADABLE"


def test_market_data_quality_counts_do_not_imply_service_failure(tmp_path, monkeypatch):
    from ops.trading_platform_ops import Supervisor

    spec = _market_data_spec(tmp_path)
    _write_market_data_status(spec, fresh=1, advanced=0, stale=193, no_data=0, errors=0)
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", tmp_path)
    row = Supervisor(sleep=lambda _: None)._service(spec, start_missing=False, processes=[ProcessInfo(11, "--job market_data")], actions=[])
    assert row["status"] == "HEALTHY"
    assert row["heartbeat"]["quality"]["stale"] == 193


def test_waiting_for_ibkr_is_not_ready(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec

    specs = (ServiceSpec("execute_worker", "Execute Worker", ("execute_requests",), None),)
    result = _supervisor(
        monkeypatch,
        [ProcessInfo(11, "execute_requests")],
        broker={"status": "WAITING_FOR_IBKR", "reason_code": "IBKR_LOGIN_REQUIRED"},
        services=specs,
    ).run(start_missing=False)
    assert result["overall_status"] == "WAITING_FOR_IBKR"
    assert result["orders_submitted"] == 0


def test_valid_paper_execution_safety_is_accepted(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec

    specs = (ServiceSpec("execute_worker", "Execute Worker", ("execute_requests",), None),)
    safety = {"status": "SAFE_FOR_PAPER_EXECUTION", "paper_trading": True, "dry_run_mode": False, "live_trading_enabled": False}
    result = _supervisor(monkeypatch, [ProcessInfo(11, "execute_requests")], safety=safety, services=specs).run(start_missing=False)
    assert result["overall_status"] == "READY_FOR_MARKET_OPEN"


def test_active_execution_blocks_missing_execute_worker_start(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec

    specs = (ServiceSpec("execute_worker", "Execute Worker", ("execute_requests",), "run_execute_worker.cmd"),)
    sup = _supervisor(monkeypatch, [], services=specs)
    monkeypatch.setattr(sup, "_execution_boundary", lambda: {
        "safe_to_start": False,
        "status": "ACTION_REQUIRED",
        "reason_code": "ACTIVE_EXECUTION_STATE",
        "action_required": "Inspect durable execution state",
    })
    monkeypatch.setattr("ops.trading_platform_ops._start", lambda spec: (_ for _ in ()).throw(AssertionError("started ambiguous worker")))
    result = sup.run(start_missing=True)
    row = result["services"]["execute_worker"]
    assert row["status"] == "ACTION_REQUIRED"
    assert row["reason_code"] == "ACTIVE_EXECUTION_STATE"


def test_transient_api_failure_is_bounded(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec

    specs = (ServiceSpec("execute_worker", "Execute Worker", ("execute_requests",), None),)
    calls = []
    responses = [
        {"status": "UNAVAILABLE", "reason": "timeout"},
        {"status": "UNAVAILABLE", "reason": "timeout"},
        {"status": "ok", "read_only": True},
    ]
    monkeypatch.setattr("ops.trading_platform_ops._get_json", lambda url: calls.append(url) or responses.pop(0))
    from ops.trading_platform_ops import Supervisor
    sup = Supervisor(sleep=lambda _: None)
    result = sup._api("http://127.0.0.1/health", "health")
    assert result["status"] == "HEALTHY"
    assert len(calls) == 3


def test_production_scanner_health_failure_remains_action_required(monkeypatch):
    from ops.trading_platform_ops import Supervisor

    monkeypatch.setattr(
        "ops.trading_platform_ops._get_json",
        lambda url: {"status": "UNAVAILABLE", "reason": "timeout"},
    )
    result = Supervisor(sleep=lambda _: None)._api(
        "http://127.0.0.1:8550/api/market-screener/health", "Production Scanner"
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["reason_code"] == "API_UNAVAILABLE"


def test_execution_boundary_reads_ambiguous_reservation_without_mutating(tmp_path, monkeypatch):
    import sqlite3
    from ops.trading_platform_ops import Supervisor

    root = tmp_path
    memory = root / "memory"
    memory.mkdir()
    with sqlite3.connect(memory / "decision_lab.db") as conn:
        conn.execute("CREATE TABLE execution_reservations (reservation_id TEXT, state TEXT)")
        conn.execute("INSERT INTO execution_reservations VALUES ('reservation-1', 'SUBMISSION_UNKNOWN')")
        conn.commit()
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", root)
    result = Supervisor()._execution_boundary()
    assert result["safe_to_start"] is False
    assert result["reason_code"] == "ACTIVE_EXECUTION_STATE"

    with sqlite3.connect(memory / "decision_lab.db") as conn:
        conn.execute("UPDATE execution_reservations SET state='SUBMITTING'")
        conn.commit()
    result = Supervisor()._execution_boundary()
    assert result["safe_to_start"] is False
    assert result["reason_code"] == "ACTIVE_EXECUTION_STATE"

