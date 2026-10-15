from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

from ops.trading_platform_ops import ProcessInfo, Supervisor, human_report


def _market_data_spec(tmp_path: Path):
    from ops.trading_platform_ops import ServiceSpec

    runtime = tmp_path / "memory" / "runtime"
    runtime.mkdir(parents=True)
    return ServiceSpec("market_data", "Market Data", ("--job market_data",), None, runtime / "market_data_collector_status.json", 900.0)


def _write_market_data_status(spec, *, age_seconds=30, market_data_status="healthy", **quality):
    stamp = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    payload = {
        "status": quality.pop("status", "CYCLE_COMPLETE"),
        "state": quality.pop("state", "CYCLE_COMPLETE"),
        "pid": quality.pop("pid", 11),
        "last_heartbeat": quality.pop("last_heartbeat", stamp.isoformat()),
        "current_session": quality.pop("current_session", "ACTIVE_SESSION"),
        "market_data_status": market_data_status,
        "last_cycle_at": stamp.isoformat(),
        "latest_fresh_cycle_at": stamp.isoformat(),
        "cycle": 4,
        "health": {"requested": 194, "received": 194, "fresh": 194, "advanced": 194, "stale": 0, "no_data": 0, "errors": 0},
    }
    payload["health"].update(quality)
    spec.heartbeat.write_text(json.dumps(payload), encoding="utf-8")


def _write_market_data_waiting_status(spec, *, session="PRE_SESSION", heartbeat_age_seconds=30, cycle_age_seconds=3600, pid=11):
    heartbeat = datetime.now(timezone.utc) - timedelta(seconds=heartbeat_age_seconds)
    cycle = datetime.now(timezone.utc) - timedelta(seconds=cycle_age_seconds)
    payload = {
        "status": "WAITING_FOR_SESSION",
        "state": "WAITING_FOR_SESSION",
        "pid": pid,
        "last_heartbeat": heartbeat.isoformat(),
        "current_session": session,
        "market_data_status": "healthy",
        "last_cycle_at": cycle.isoformat(),
        "latest_fresh_cycle_at": None,
        "cycle": 1,
        "health": {"requested": 1, "received": 0, "fresh": 0, "advanced": 1, "stale": 0, "no_data": 0, "errors": 0},
    }
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


def test_market_data_pre_session_waiting_is_healthy_with_fresh_runtime_heartbeat(tmp_path, monkeypatch):
    from ops.trading_platform_ops import Supervisor

    spec = _market_data_spec(tmp_path)
    _write_market_data_waiting_status(spec, session="PRE_SESSION", heartbeat_age_seconds=20, cycle_age_seconds=12 * 3600)
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", tmp_path)
    monkeypatch.setattr("ops.trading_platform_ops.RUNTIME", spec.heartbeat.parent)

    row = Supervisor(sleep=lambda _: None)._service(spec, start_missing=False, processes=[ProcessInfo(11, "--job market_data")], actions=[])

    assert row["status"] == "WAITING_FOR_SESSION"
    assert row["operational_state"] == "HEALTHY"
    assert row["heartbeat"]["session_state"] == "PRE_SESSION"
    assert row["heartbeat"]["runtime_fresh"] is True


def test_market_data_post_session_waiting_is_healthy_with_fresh_runtime_heartbeat(tmp_path, monkeypatch):
    from ops.trading_platform_ops import Supervisor

    spec = _market_data_spec(tmp_path)
    _write_market_data_waiting_status(spec, session="POST_SESSION", heartbeat_age_seconds=20, cycle_age_seconds=3600)
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", tmp_path)
    monkeypatch.setattr("ops.trading_platform_ops.RUNTIME", spec.heartbeat.parent)

    row = Supervisor(sleep=lambda _: None)._service(spec, start_missing=False, processes=[ProcessInfo(11, "--job market_data")], actions=[])

    assert row["status"] == "WAITING_FOR_SESSION"
    assert row["operational_state"] == "HEALTHY"
    assert row["heartbeat"]["session_state"] == "POST_SESSION"


def test_market_data_weekend_waiting_is_healthy_with_fresh_runtime_heartbeat(tmp_path, monkeypatch):
    from ops.trading_platform_ops import Supervisor

    spec = _market_data_spec(tmp_path)
    _write_market_data_waiting_status(spec, session="WEEKEND_OR_HOLIDAY", heartbeat_age_seconds=20, cycle_age_seconds=48 * 3600)
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", tmp_path)
    monkeypatch.setattr("ops.trading_platform_ops.RUNTIME", spec.heartbeat.parent)

    row = Supervisor(sleep=lambda _: None)._service(spec, start_missing=False, processes=[ProcessInfo(11, "--job market_data")], actions=[])

    assert row["status"] == "WAITING_FOR_SESSION"
    assert row["operational_state"] == "HEALTHY"
    assert row["heartbeat"]["session_state"] == "WEEKEND_OR_HOLIDAY"


def test_market_data_active_session_with_stale_cycle_and_fresh_runtime_is_unavailable(tmp_path, monkeypatch):
    from ops.trading_platform_ops import Supervisor

    spec = _market_data_spec(tmp_path)
    _write_market_data_status(spec, age_seconds=1200, last_heartbeat=(datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat())
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", tmp_path)
    monkeypatch.setattr("ops.trading_platform_ops.RUNTIME", spec.heartbeat.parent)

    row = Supervisor(sleep=lambda _: None)._service(spec, start_missing=False, processes=[ProcessInfo(11, "--job market_data")], actions=[])

    assert row["status"] == "UNAVAILABLE"
    assert row["operational_state"] == "UNHEALTHY"
    assert row["reason_code"] == "MARKET_DATA_CYCLE_STALE"


def test_market_data_dead_process_with_stale_heartbeat_is_missing(tmp_path, monkeypatch):
    from ops.trading_platform_ops import Supervisor

    spec = _market_data_spec(tmp_path)
    _write_market_data_waiting_status(spec, session="PRE_SESSION", heartbeat_age_seconds=1200, cycle_age_seconds=12 * 3600)
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", tmp_path)
    monkeypatch.setattr("ops.trading_platform_ops.RUNTIME", spec.heartbeat.parent)

    row = Supervisor(sleep=lambda _: None)._service(spec, start_missing=False, processes=[], actions=[])

    assert row["status"] == "MISSING"
    assert row["operational_state"] == "STOPPED"


def test_market_data_lock_owner_child_of_root_is_not_mismatch(tmp_path, monkeypatch):
    from ops.trading_platform_ops import Supervisor

    spec = _market_data_spec(tmp_path)
    _write_market_data_status(spec, pid=43332)
    lock = spec.heartbeat.with_name("market_data_collector.lock")
    lock.write_text("43332|2026-10-12T10:54:14", encoding="utf-8")
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", tmp_path)
    monkeypatch.setattr("ops.trading_platform_ops.RUNTIME", spec.heartbeat.parent)
    processes = [
        ProcessInfo(25624, '"..\\.venv\\Scripts\\python.exe" -m src.main --job market_data --client-id 17', 48176),
        ProcessInfo(43332, '"Python312\\python.exe" -m src.main --job market_data --client-id 17', 25624),
    ]

    row = Supervisor(sleep=lambda _: None)._service(spec, start_missing=False, processes=processes, actions=[])

    assert row["status"] == "HEALTHY"
    assert row["logical_instances"] == 1
    assert row["root_pids"] == [25624]
    assert row["lock"]["owner_pid"] == 43332
    assert row["lock"]["owner_matches_process_tree"] is True


def test_market_data_lock_owner_mismatch_is_unknown(tmp_path, monkeypatch):
    from ops.trading_platform_ops import Supervisor

    spec = _market_data_spec(tmp_path)
    _write_market_data_status(spec)
    lock = spec.heartbeat.with_name("market_data_collector.lock")
    lock.write_text("43332|2026-10-12T10:54:14", encoding="utf-8")
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", tmp_path)
    monkeypatch.setattr("ops.trading_platform_ops.RUNTIME", spec.heartbeat.parent)

    row = Supervisor(sleep=lambda _: None)._service(spec, start_missing=False, processes=[ProcessInfo(25624, "--job market_data")], actions=[])

    assert row["status"] == "UNKNOWN"
    assert row["operational_state"] == "UNKNOWN"
    assert row["reason_code"] == "MARKET_DATA_LOCK_OWNER_MISMATCH"


def test_market_data_repeated_launcher_invocation_does_not_start_duplicate(tmp_path, monkeypatch):
    from ops.trading_platform_ops import Supervisor

    spec = _market_data_spec(tmp_path)
    _write_market_data_waiting_status(spec, session="PRE_SESSION")
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", tmp_path)
    monkeypatch.setattr("ops.trading_platform_ops.RUNTIME", spec.heartbeat.parent)
    launches = []
    monkeypatch.setattr("ops.trading_platform_ops._start", lambda spec: launches.append(spec.key))
    sup = Supervisor(process_provider=lambda: [ProcessInfo(11, "--job market_data")], sleep=lambda _: None)

    first = sup._service(spec, start_missing=True, processes=[ProcessInfo(11, "--job market_data")], actions=[])
    second = sup._service(spec, start_missing=True, processes=[ProcessInfo(11, "--job market_data")], actions=[])

    assert first["status"] == "WAITING_FOR_SESSION"
    assert second["status"] == "WAITING_FOR_SESSION"
    assert launches == []


def test_market_data_after_close_cycle_is_degraded_not_unavailable(tmp_path, monkeypatch):
    from ops.trading_platform_ops import Supervisor

    spec = _market_data_spec(tmp_path)
    stamp = datetime(2026, 10, 12, 17, 26, tzinfo=timezone.utc)
    payload = {
        "market_data_status": "degraded",
        "last_cycle_at": stamp.isoformat(),
        "latest_fresh_cycle_at": stamp.isoformat(),
        "cycle": 80,
        "health": {
            "requested": 194,
            "received": 193,
            "fresh": 0,
            "advanced": 1,
            "stale": 0,
            "no_data": 1,
            "errors": 0,
        },
    }
    spec.heartbeat.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("ops.trading_platform_ops.ROOT", tmp_path)
    monkeypatch.setattr(
        "ops.trading_platform_ops._market_data_now",
        lambda tz: datetime(2026, 10, 12, 20, 30, tzinfo=tz),
    )

    row = Supervisor(sleep=lambda _: None)._service(spec, start_missing=False, processes=[ProcessInfo(11, "--job market_data")], actions=[])

    assert row["status"] == "DEGRADED"
    assert row["operational_state"] == "HEALTHY"
    assert row["heartbeat"]["session_state"] == "OFF_SESSION_RECENT_CLOSE"


def test_strategy_scanner_requires_application_readiness(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec, Supervisor

    spec = ServiceSpec(
        "strategy_scanner_mcp",
        "Strategy Scanner MCP",
        ("scanner_mcp.server",),
        "start-strategy-scanner-mcp.cmd",
        port=8766,
        readiness_url="http://127.0.0.1:8766/health",
    )
    monkeypatch.setattr("ops.trading_platform_ops._port", lambda port: {"checked": True, "reachable": True, "host": "127.0.0.1", "port": port})
    sup = Supervisor(sleep=lambda _: None)
    monkeypatch.setattr(sup, "_api", lambda url, label, method="GET": {"service": label, "status": "UNAVAILABLE", "reason_code": "API_UNAVAILABLE"})

    row = sup._service(spec, start_missing=False, processes=[ProcessInfo(66, "python -m scanner_mcp.server")], actions=[])

    assert row["status"] == "UNAVAILABLE"
    assert row["reason_code"] == "READINESS_UNAVAILABLE"


def test_strategy_scanner_recovers_process_from_verified_port_owner(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec, Supervisor

    spec = ServiceSpec(
        "strategy_scanner_mcp",
        "Strategy Scanner MCP",
        ("scanner_mcp.server",),
        "start-strategy-scanner-mcp.cmd",
        port=8766,
        readiness_url="http://127.0.0.1:8766/health",
    )
    monkeypatch.setattr("ops.trading_platform_ops._port", lambda port: {"checked": True, "reachable": True, "host": "127.0.0.1", "port": port})
    monkeypatch.setattr("ops.trading_platform_ops._port_owner", lambda port: 87660)
    sup = Supervisor(sleep=lambda _: None)
    monkeypatch.setattr(sup, "_api", lambda url, label, method="GET": {"service": label, "status": "HEALTHY", "read_only": True})

    row = sup._service(spec, start_missing=False, processes=[], actions=[])

    assert row["status"] == "HEALTHY"
    assert row["pids"] == [87660]


def test_dashboard_missing_uses_api_only_launcher_while_workers_remain_healthy(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec

    specs = (
        ServiceSpec(
            "dashboard",
            "Dashboard",
            ("dashboard_react.server:app",),
            "run_dashboard_api.cmd",
            port=8550,
            readiness_url="http://127.0.0.1:8550/api/radar/top?limit=1",
            detached_launcher=True,
        ),
        ServiceSpec("execute_worker", "Execute Worker", ("execute_requests",), None),
        ServiceSpec("crypto_worker", "Crypto Worker", ("src.crypto.main --job worker",), None, required=False),
    )
    processes = [
        ProcessInfo(12, "python -m src.main --job execute_requests --client-id 11"),
        ProcessInfo(13, "python -m src.crypto.main --job worker"),
    ]
    launches = []
    sup = _supervisor(monkeypatch, processes, services=specs)
    monkeypatch.setattr(
        "ops.trading_platform_ops._port",
        lambda port: {"checked": False} if port is None else {"checked": True, "reachable": False, "host": "127.0.0.1", "port": port},
    )
    monkeypatch.setattr("ops.trading_platform_ops._start", lambda spec: launches.append(spec.launcher) or {"ok": True})

    result = sup.run(start_missing=True)

    assert launches == ["run_dashboard_api.cmd", "run_dashboard_api.cmd"]
    assert result["services"]["dashboard"]["reason_code"] == "START_VERIFICATION_FAILED"
    assert result["services"]["execute_worker"]["status"] == "HEALTHY"
    assert result["services"]["crypto_worker"]["status"] == "HEALTHY"
    assert result["orders_submitted"] == 0
    assert result["approvals_created"] == 0
    assert result["execution_intents_created"] == 0


def test_dashboard_already_running_is_adopted_from_verified_port_owner(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec, Supervisor

    spec = ServiceSpec(
        "dashboard",
        "Dashboard",
        ("dashboard_react.server:app",),
        "run_dashboard_api.cmd",
        port=8550,
        readiness_url="http://127.0.0.1:8550/api/radar/top?limit=1",
        detached_launcher=True,
    )
    monkeypatch.setattr("ops.trading_platform_ops._port", lambda port: {"checked": True, "reachable": True, "host": "127.0.0.1", "port": port})
    monkeypatch.setattr("ops.trading_platform_ops._port_owner", lambda port: 85500)
    monkeypatch.setattr("ops.trading_platform_ops._start", lambda spec: (_ for _ in ()).throw(AssertionError("started existing dashboard")))
    sup = Supervisor(sleep=lambda _: None)
    monkeypatch.setattr(sup, "_api", lambda url, label, method="GET": {"service": label, "status": "HEALTHY", "read_only": True})

    row = sup._service(spec, start_missing=True, processes=[], actions=[])

    assert row["status"] == "HEALTHY"
    assert row["pids"] == [85500]
    assert row["port_identity_verified"] is True


def test_port_occupied_by_unrelated_process_refuses_recovery(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec, Supervisor

    spec = ServiceSpec(
        "dashboard",
        "Dashboard",
        ("dashboard_react.server:app",),
        "run_dashboard_api.cmd",
        port=8550,
        readiness_url="http://127.0.0.1:8550/api/radar/top?limit=1",
        detached_launcher=True,
    )
    monkeypatch.setattr("ops.trading_platform_ops._port", lambda port: {"checked": True, "reachable": True, "host": "127.0.0.1", "port": port})
    monkeypatch.setattr("ops.trading_platform_ops._port_owner", lambda port: 99999)
    monkeypatch.setattr("ops.trading_platform_ops._start", lambda spec: (_ for _ in ()).throw(AssertionError("started ambiguous port")))
    sup = Supervisor(sleep=lambda _: None)
    monkeypatch.setattr(sup, "_api", lambda url, label, method="GET": {"service": label, "status": "UNAVAILABLE", "reason_code": "API_UNAVAILABLE"})

    row = sup._service(spec, start_missing=True, processes=[], actions=[])

    assert row["status"] == "ACTION_REQUIRED"
    assert row["reason_code"] == "PORT_OWNERSHIP_AMBIGUOUS"
    assert row["safe_auto_repair"] is False


def test_detached_launcher_failure_is_actionable(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec, Supervisor

    spec = ServiceSpec(
        "trading_bot_mcp",
        "Trading Bot MCP",
        ("src.mcp.trading_bot_server",),
        "run_trading_bot_mcp.cmd",
        port=8765,
        readiness_url="http://127.0.0.1:8765/health",
        detached_launcher=True,
    )
    monkeypatch.setattr("ops.trading_platform_ops._port", lambda port: {"checked": True, "reachable": False, "host": "127.0.0.1", "port": port})
    monkeypatch.setattr(
        "ops.trading_platform_ops._start",
        lambda spec: {"ok": False, "reason": "launcher_exit_nonzero", "exit_code": 1, "stderr_tail": "Missing required dependencies"},
    )
    row = Supervisor(sleep=lambda _: None)._service(spec, start_missing=True, processes=[], actions=[])

    assert row["status"] == "FAILED"
    assert row["reason_code"] == "START_FAILED"
    assert row["launcher_result"]["stderr_tail"] == "Missing required dependencies"


def test_successful_bounded_recovery_records_structured_actions(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec

    spec = ServiceSpec(
        "trading_bot_mcp",
        "Trading Bot MCP",
        ("src.mcp.trading_bot_server",),
        "run_trading_bot_mcp.cmd",
        port=8765,
        readiness_url="http://127.0.0.1:8765/health",
        detached_launcher=True,
    )
    process_sets = [[], [ProcessInfo(87650, "python -m src.mcp.trading_bot_server")]]
    sup = _supervisor(monkeypatch, [], services=(spec,))
    sup.process_provider = lambda: process_sets.pop(0) if process_sets else [ProcessInfo(87650, "python -m src.mcp.trading_bot_server")]
    monkeypatch.setattr("ops.trading_platform_ops._port", lambda port: {"checked": True, "reachable": bool(process_sets == []), "host": "127.0.0.1", "port": port})
    monkeypatch.setattr("ops.trading_platform_ops._start", lambda spec: {"ok": True, "stdout_tail": "started"})

    result = sup.run(start_missing=True)

    assert result["services"]["trading_bot_mcp"]["status"] == "HEALTHY"
    assert result["actions_taken"][0]["service"] == "trading_bot_mcp"
    assert result["actions_taken"][0]["launcher"] == "run_trading_bot_mcp.cmd"


def test_api_health_check_failure_after_process_start_is_unavailable(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec, Supervisor

    spec = ServiceSpec(
        "trading_bot_mcp",
        "Trading Bot MCP",
        ("src.mcp.trading_bot_server",),
        "run_trading_bot_mcp.cmd",
        port=8765,
        readiness_url="http://127.0.0.1:8765/health",
        detached_launcher=True,
    )
    monkeypatch.setattr("ops.trading_platform_ops._port", lambda port: {"checked": True, "reachable": True, "host": "127.0.0.1", "port": port})
    sup = Supervisor(sleep=lambda _: None)
    monkeypatch.setattr(sup, "_api", lambda url, label, method="GET": {"service": label, "status": "UNAVAILABLE", "reason_code": "API_UNAVAILABLE"})

    row = sup._service(spec, start_missing=False, processes=[ProcessInfo(87650, "python -m src.mcp.trading_bot_server")], actions=[])

    assert row["status"] == "UNAVAILABLE"
    assert row["reason_code"] == "READINESS_UNAVAILABLE"


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
        {"status": "ok", "read_only": True},
    ]
    monkeypatch.setattr("ops.trading_platform_ops._get_json", lambda url: calls.append(url) or responses.pop(0))
    from ops.trading_platform_ops import Supervisor
    sup = Supervisor(sleep=lambda _: None)
    result = sup._api("http://127.0.0.1/health", "health")
    assert result["status"] == "HEALTHY"
    assert len(calls) == 2


def test_http_service_healthy_with_denied_process_visibility(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec, Supervisor

    spec = ServiceSpec(
        "strategy_scanner_mcp",
        "Strategy Scanner MCP",
        ("scanner_mcp.server",),
        "start-strategy-scanner-mcp.cmd",
        port=8766,
        readiness_url="http://127.0.0.1:8766/health",
    )
    monkeypatch.setattr("ops.trading_platform_ops._port", lambda port: {"checked": True, "reachable": True, "host": "127.0.0.1", "port": port})
    monkeypatch.setattr("ops.trading_platform_ops._port_owner", lambda port: 87660)
    sup = Supervisor(sleep=lambda _: None)
    monkeypatch.setattr(sup, "_api", lambda url, label, method="GET": {"service": label, "status": "HEALTHY", "operational_state": "HEALTHY", "read_only": True})

    row = sup._service(spec, start_missing=True, processes=[], actions=[], process_visibility="unavailable", process_visibility_reason="PermissionDenied")

    assert row["status"] == "HEALTHY"
    assert row["operational_state"] == "HEALTHY"
    assert row["process_visibility"]["status"] == "unavailable"


def test_fresh_worker_heartbeat_is_healthy_with_denied_process_visibility(tmp_path, monkeypatch):
    from ops.trading_platform_ops import ServiceSpec, Supervisor

    heartbeat = tmp_path / "execute_worker.json"
    heartbeat.write_text(json.dumps({"pid": 123, "ts": 1}), encoding="utf-8")
    spec = ServiceSpec("execute_worker", "Execute Worker", ("execute_requests",), "run_execute_worker.cmd", heartbeat, 30.0)
    row = Supervisor(sleep=lambda _: None)._service(
        spec,
        start_missing=True,
        processes=[],
        actions=[],
        process_visibility="unavailable",
        process_visibility_reason="PermissionDenied",
    )

    assert row["status"] == "HEALTHY"
    assert row["operational_state"] == "HEALTHY"
    assert row["process_running"] is True


def test_stale_worker_heartbeat_is_unknown_when_process_visibility_denied(tmp_path):
    from ops.trading_platform_ops import ServiceSpec, Supervisor

    heartbeat = tmp_path / "execute_worker.json"
    heartbeat.write_text(json.dumps({"pid": 123, "ts": 1}), encoding="utf-8")
    old = datetime.now(timezone.utc).timestamp() - 120
    import os
    os.utime(heartbeat, (old, old))
    spec = ServiceSpec("execute_worker", "Execute Worker", ("execute_requests",), "run_execute_worker.cmd", heartbeat, 30.0)
    row = Supervisor(sleep=lambda _: None)._service(
        spec,
        start_missing=True,
        processes=[],
        actions=[],
        process_visibility="unavailable",
        process_visibility_reason="PermissionDenied",
    )

    assert row["status"] == "UNKNOWN"
    assert row["operational_state"] == "UNKNOWN"
    assert row["reason_code"] == "PROCESS_VISIBILITY_UNAVAILABLE"


def test_missing_service_is_stopped_when_process_visibility_available(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec

    specs = (ServiceSpec("execute_worker", "Execute Worker", ("execute_requests",), "run_execute_worker.cmd"),)
    sup = _supervisor(monkeypatch, [], services=specs)
    result = sup.run(start_missing=False)
    row = result["services"]["execute_worker"]
    assert row["status"] == "MISSING"
    assert row["operational_state"] == "STOPPED"


def test_startup_timeout_uses_only_two_launch_attempts(monkeypatch):
    from ops.trading_platform_ops import ServiceSpec

    specs = (ServiceSpec("execute_worker", "Execute Worker", ("execute_requests",), "run_execute_worker.cmd"),)
    launches = []
    sup = _supervisor(monkeypatch, [], services=specs)
    monkeypatch.setattr("ops.trading_platform_ops._start", lambda spec: launches.append(spec.key) or None)

    result = sup.run(start_missing=True)
    row = result["services"]["execute_worker"]

    assert launches == ["execute_worker", "execute_worker"]
    assert row["status"] == "ACTION_REQUIRED"
    assert row["operational_state"] == "UNHEALTHY"
    assert row["reason_code"] == "START_VERIFICATION_FAILED"
    assert result["orders_submitted"] == 0
    assert result["approvals_created"] == 0
    assert result["execution_intents_created"] == 0


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


def test_pre_trade_reviewer_health_uses_dashboard_route(monkeypatch):
    from ops.trading_platform_ops import Supervisor

    calls = []

    def fake_api(self, url, label, method="GET"):
        calls.append((label, url, method))
        return {"service": label, "status": "HEALTHY", "read_only": True}

    monkeypatch.setattr("ops.trading_platform_ops._specs", lambda: ())
    monkeypatch.setattr(Supervisor, "_api", fake_api)
    monkeypatch.setattr(Supervisor, "_broker", lambda self: {"status": "HEALTHY"})
    monkeypatch.setattr(Supervisor, "_database", lambda self: {"status": "HEALTHY"})
    monkeypatch.setattr(Supervisor, "_safety", lambda self: {"status": "SAFE_FOR_PAPER_EXECUTION"})
    monkeypatch.setattr(Supervisor, "_execution_boundary", lambda self: {"safe_to_start": True, "status": "SAFE_TO_START"})

    result = Supervisor(process_provider=lambda: [], sleep=lambda _: None).run(start_missing=False)

    assert result["apis"]["pre_trade_reviewer"]["status"] == "HEALTHY"
    assert (
        "Pre-Trade Reviewer",
        "http://127.0.0.1:8550/api/pre-trade-review/health",
        "GET",
    ) in calls
    assert all("/api/pre-trade-reviewer/health" not in url for _, url, _ in calls)


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


def _preflight_db(path, *, approvals=(), intents=(), jobs=()):
    import sqlite3

    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE trade_approvals (approval_id TEXT PRIMARY KEY, status TEXT NOT NULL);
            CREATE TABLE trade_execution_intents (execution_intent_id TEXT PRIMARY KEY, approval_id TEXT NOT NULL);
            CREATE TABLE trade_approval_jobs (job_id TEXT PRIMARY KEY, approval_id TEXT NOT NULL, status TEXT NOT NULL);
            """
        )
        conn.executemany("INSERT INTO trade_approvals VALUES (?, ?)", approvals)
        conn.executemany("INSERT INTO trade_execution_intents VALUES (?, ?)", intents)
        conn.executemany("INSERT INTO trade_approval_jobs VALUES (?, ?, ?)", jobs)
        conn.commit()


def test_execute_worker_preflight_empty_stores_is_safe_to_start(tmp_path):
    from ops.trading_platform_ops import _read_execute_worker_preflight

    db = tmp_path / "decision_lab.db"
    queue = tmp_path / "order_requests.json"
    _preflight_db(db)
    queue.write_text(json.dumps({"requests": []}), encoding="utf-8")

    result = _read_execute_worker_preflight(database_path=db, queue_path=queue, processes=[])

    assert result["verdict"] == "SAFE_TO_START"
    assert result["could_submit_ibkr_paper_orders_on_startup"] is False
    assert result["counts"]["approved_unconsumed"] == 0
    assert result["counts"]["actionable_execution_requests"] == 0
    assert result["read_only"] is True


def test_execute_worker_preflight_pending_approval_blocks_start(tmp_path):
    from ops.trading_platform_ops import _read_execute_worker_preflight

    db = tmp_path / "decision_lab.db"
    queue = tmp_path / "order_requests.json"
    _preflight_db(db, approvals=[("approval-1", "APPROVED")], jobs=[("job-1", "approval-1", "PENDING")])
    queue.write_text(json.dumps({"requests": []}), encoding="utf-8")

    result = _read_execute_worker_preflight(database_path=db, queue_path=queue, processes=[])

    assert result["verdict"] == "BLOCKED"
    assert result["reason_code"] == "STARTUP_ORDER_RISK"
    assert result["could_submit_ibkr_paper_orders_on_startup"] is True
    assert result["counts"]["approved_unconsumed"] == 1
    assert result["counts"]["approval_jobs_pending"] == 1


def test_execute_worker_preflight_pending_request_blocks_start(tmp_path):
    from ops.trading_platform_ops import _read_execute_worker_preflight

    db = tmp_path / "decision_lab.db"
    queue = tmp_path / "order_requests.json"
    _preflight_db(db)
    queue.write_text(json.dumps({"requests": [{"id": "req-1", "status": "pending", "action": "place"}]}), encoding="utf-8")

    result = _read_execute_worker_preflight(database_path=db, queue_path=queue, processes=[])

    assert result["verdict"] == "BLOCKED"
    assert result["counts"]["pending_actionable_requests"] == 1
    assert result["counts"]["actionable_execution_requests"] == 1


def test_execute_worker_preflight_unknown_on_incomplete_or_unreadable_state(tmp_path):
    from ops.trading_platform_ops import _read_execute_worker_preflight

    queue = tmp_path / "order_requests.json"
    queue.write_text(json.dumps({"requests": []}), encoding="utf-8")
    result = _read_execute_worker_preflight(database_path=tmp_path / "missing.db", queue_path=queue, processes=[])

    assert result["verdict"] == "UNKNOWN"
    assert result["reason_code"] == "PREFLIGHT_EVIDENCE_INCOMPLETE"
    assert any(error["reason_code"] == "DATABASE_ACCESS_FAILED" for error in result["errors"])

    db = tmp_path / "incomplete.db"
    db.touch()
    result = _read_execute_worker_preflight(database_path=db, queue_path=queue, processes=[])
    assert result["verdict"] == "UNKNOWN"
    assert result["reason_code"] == "PREFLIGHT_EVIDENCE_INCOMPLETE"


def test_execute_worker_preflight_detects_duplicate_instances_without_mutating(tmp_path):
    from ops.trading_platform_ops import ProcessInfo, _read_execute_worker_preflight

    db = tmp_path / "decision_lab.db"
    queue = tmp_path / "order_requests.json"
    _preflight_db(db)
    queue.write_text(json.dumps({"requests": []}), encoding="utf-8")
    processes = [
        ProcessInfo(11, "python -m src.main --job execute_requests"),
        ProcessInfo(12, "python -m src.main --job execute_requests"),
    ]

    result = _read_execute_worker_preflight(database_path=db, queue_path=queue, processes=processes)

    assert result["verdict"] == "BLOCKED"
    assert result["reason_code"] == "DUPLICATE_EXECUTE_WORKER"
    assert result["evidence"]["processes"]["logical_instance_count"] == 2

def test_human_report_uses_required_morning_startup_sections():
    result = {
        "overall_status": "WAITING_FOR_IBKR",
        "broker": {"status": "WAITING_FOR_IBKR", "reason_code": "IBKR_LOGIN_REQUIRED"},
        "services": {
            "trading_bot_mcp": {"status": "HEALTHY", "operational_state": "HEALTHY"},
            "strategy_scanner_mcp": {"status": "HEALTHY", "operational_state": "HEALTHY"},
            "dashboard": {"status": "HEALTHY", "operational_state": "HEALTHY"},
            "market_data": {
                "status": "UNKNOWN",
                "operational_state": "UNKNOWN",
                "label": "Market Data",
                "required": True,
                "reason_code": "PROCESS_VISIBILITY_UNAVAILABLE",
                "heartbeat": {
                    "age_seconds": 1200.0,
                    "fresh": False,
                    "path": "memory/runtime/market_data_collector_status.json",
                },
            },
            "execute_worker": {"status": "HEALTHY", "operational_state": "HEALTHY"},
            "autonomous_worker": {"status": "HEALTHY", "operational_state": "HEALTHY"},
            "crypto_worker": {"status": "HEALTHY", "operational_state": "HEALTHY"},
        },
        "apis": {
            "trend_radar": {"service": "Trend Radar", "status": "HEALTHY"},
            "production_scanner": {"service": "Production Scanner", "status": "HEALTHY"},
            "daily_trader": {"service": "Daily Trader", "status": "HEALTHY"},
            "pre_trade_reviewer": {"service": "Pre-Trade Reviewer", "status": "HEALTHY"},
        },
        "safety": {
            "status": "SAFE_FOR_PAPER_EXECUTION",
            "paper_trading": True,
            "dry_run_mode": False,
            "live_trading_enabled": False,
        },
        "execution_boundary": {"safe_to_start": True, "status": "SAFE_TO_START"},
        "orders_submitted": 0,
        "approvals_created": 0,
        "execution_intents_created": 0,
        "action_required": ["PROCESS_VISIBILITY_UNAVAILABLE"],
    }

    report = human_report(result)

    assert report.startswith("TRADING PLATFORM - MORNING STARTUP")
    assert "Overall: WAITING_FOR_IBKR" in report
    assert "Market Data: UNKNOWN" in report
    assert "heartbeat_age_seconds=1200.0" in report
    assert "Trading Actions: orders_submitted=0, approvals_created=0, execution_intents_created=0" in report
    assert "READY_FOR_MARKET_OPEN" not in report


def test_human_report_only_claims_ready_when_overall_is_ready():
    result = {
        "overall_status": "READY_FOR_MARKET_OPEN",
        "broker": {"status": "HEALTHY", "account": {"account_id": "DU123", "environment": "paper"}},
        "services": {
            "trading_bot_mcp": {"status": "HEALTHY", "operational_state": "HEALTHY"},
            "strategy_scanner_mcp": {"status": "HEALTHY", "operational_state": "HEALTHY"},
            "dashboard": {"status": "HEALTHY", "operational_state": "HEALTHY"},
            "market_data": {"status": "HEALTHY", "operational_state": "HEALTHY", "heartbeat": {"age_seconds": 5.0}},
            "execute_worker": {"status": "HEALTHY", "operational_state": "HEALTHY"},
            "autonomous_worker": {"status": "HEALTHY", "operational_state": "HEALTHY"},
            "crypto_worker": {"status": "HEALTHY", "operational_state": "HEALTHY"},
        },
        "apis": {},
        "safety": {
            "status": "SAFE_FOR_PAPER_EXECUTION",
            "paper_trading": True,
            "dry_run_mode": False,
            "live_trading_enabled": False,
        },
        "execution_boundary": {"safe_to_start": True, "status": "SAFE_TO_START"},
        "orders_submitted": 0,
        "approvals_created": 0,
        "execution_intents_created": 0,
        "action_required": [],
    }

    report = human_report(result)

    assert "Issues:\n- None" in report
    assert report.endswith("READY_FOR_MARKET_OPEN")
