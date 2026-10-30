"""Deterministic morning readiness checks for the existing Windows stack.

This module is intentionally independent of Hermes.  Hermes skills invoke the
CLI at the bottom of this file; the facts and state transitions are decided
here so a language model never interprets process output as health.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
# Keep direct ``python ops/trading_platform_ops.py`` invocation aligned with
# module/test execution, where the repository root is already importable.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
RUNTIME = ROOT / "memory" / "runtime"
MARKET_DATA_STATUS_MAX_AGE = 900.0
API_TIMEOUT_SECONDS = 2.0
API_ATTEMPTS = 2
STARTUP_VERIFY_POLLS = 8
STARTUP_VERIFY_INTERVAL_SECONDS = 0.5
BROKER_IDENTITY_TIMEOUT_SECONDS = 8.0


class BrokerIdentityProbeError(RuntimeError):
    """A read-only broker identity probe failed before returning identity."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(detail or reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    command_line: str
    parent_pid: int | None = None
    executable: str | None = None
    start_time: str | None = None


@dataclass
class ServiceSpec:
    key: str
    label: str
    patterns: tuple[str, ...]
    launcher: str | None
    heartbeat: Path | None = None
    heartbeat_max_age: float = 120.0
    port: int | None = None
    required: bool = True
    readiness_url: str | None = None
    detached_launcher: bool = False


@dataclass(frozen=True)
class ProcessSnapshot:
    processes: list[ProcessInfo]
    visibility: str = "available"
    reason: str | None = None


def _json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _process_snapshot_from_cim() -> list[ProcessInfo]:
    """Highest-fidelity Windows process inventory, when CIM is available."""
    ps = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine,Name,CreationDate | "
          "ConvertTo-Json -Compress")
    raw = subprocess.check_output(["powershell", "-NoProfile", "-Command", ps], text=True, timeout=5)
    data = json.loads(raw) if raw.strip() else []
    if isinstance(data, dict):
        data = [data]
    return [
        ProcessInfo(
            int(row["ProcessId"]), str(row.get("CommandLine") or ""),
            int(row["ParentProcessId"]) if row.get("ParentProcessId") else None,
            str(row.get("Name") or ""), str(row.get("CreationDate") or ""),
        )
        for row in data
    ]


def _process_snapshot_from_psutil() -> list[ProcessInfo]:
    """Use psutil if installed; access-denied processes are intentionally skipped."""
    psutil = importlib.import_module("psutil")
    rows: list[ProcessInfo] = []
    for process in psutil.process_iter(["pid", "ppid", "cmdline", "name", "exe", "create_time"]):
        try:
            info = process.info
            command = " ".join(info.get("cmdline") or [])
            rows.append(ProcessInfo(
                int(info["pid"]), command, int(info.get("ppid") or 0) or None,
                str(info.get("exe") or info.get("name") or ""), str(info.get("create_time") or ""),
            ))
        except Exception:  # Access-denied and disappeared processes are expected in a partial inventory.
            continue
    if not rows:
        raise RuntimeError("psutil returned no visible processes")
    return rows


def _process_snapshot_from_get_process() -> list[ProcessInfo]:
    """Fallback inventory with PID/name/path only when CIM command lines are denied."""
    command = (
        "$ErrorActionPreference='SilentlyContinue'; Get-Process | "
        "Select-Object Id,ProcessName,Path,StartTime | ConvertTo-Json -Compress"
    )
    raw = subprocess.check_output(["powershell", "-NoProfile", "-Command", command], text=True, timeout=5)
    data = json.loads(raw) if raw.strip() else []
    if isinstance(data, dict):
        data = [data]
    rows = [
        ProcessInfo(int(row["Id"]), "", None, str(row.get("Path") or row.get("ProcessName") or ""), str(row.get("StartTime") or ""))
        for row in data
        if row.get("Id") is not None
    ]
    if not rows:
        raise RuntimeError("Get-Process returned no visible processes")
    return rows


def _targeted_process_from_powershell(pid: int) -> ProcessInfo | None:
    """Read one PID through an independent PowerShell query.

    Bulk CIM enumeration can be incomplete under Windows security boundaries.
    A targeted query is used only to corroborate a lock/heartbeat PID; failure
    remains incomplete evidence and never becomes a positive ownership claim.
    """
    if int(pid) <= 0:
        return None
    for provider in ("Get-CimInstance", "Get-WmiObject"):
        script = (
            "$p = " + provider + " Win32_Process -Filter 'ProcessId = "
            + str(int(pid))
            + "' -ErrorAction SilentlyContinue; "
            "if ($p) { $p | Select-Object ProcessId,ParentProcessId,CommandLine,Name,CreationDate | ConvertTo-Json -Compress }"
        )
        try:
            raw = subprocess.check_output(["powershell", "-NoProfile", "-Command", script], text=True, timeout=5)
            value = json.loads(raw) if raw.strip() else None
            if isinstance(value, dict) and value.get("ProcessId") is not None:
                return ProcessInfo(
                    int(value["ProcessId"]), str(value.get("CommandLine") or ""),
                    int(value["ParentProcessId"]) if value.get("ParentProcessId") else None,
                    str(value.get("Name") or ""), str(value.get("CreationDate") or ""),
                )
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
            continue
    return None


def _parse_evidence_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.startswith("/Date(") and text.endswith(")/"):
        try:
            return datetime.fromtimestamp(int(text[6:-2]) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        # Older collector locks were written with datetime.now().isoformat()
        # and therefore contain local wall time without an offset. Treat that
        # legacy form as local time; interpreting it as UTC causes a false PID
        # reuse/lock mismatch on non-UTC Windows hosts.
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    except (TypeError, ValueError, OverflowError):
        return None


def _reconcile_market_data_process(processes: list[ProcessInfo], heartbeat: Mapping[str, Any], lock_owner: int | None, lock_created_at: str | None = None) -> tuple[list[ProcessInfo], str | None, bool]:
    """Augment incomplete inventory with targeted evidence for the collector PID."""
    if lock_owner is None or any(item.pid == lock_owner for item in processes):
        matched = next((item for item in processes if item.pid == lock_owner), None)
        lock_time = _parse_evidence_time(lock_created_at)
        process_time = _parse_evidence_time(matched.start_time if matched else None)
        if lock_time and process_time and process_time > lock_time:
            return processes, "PROCESS_START_TIME_MISMATCH", False
        return processes, None, True
    targeted = _targeted_process_from_powershell(lock_owner)
    if targeted is None:
        return processes, "TARGETED_PROCESS_EVIDENCE_UNAVAILABLE", False
    command = targeted.command_line.lower()
    if "--job market_data" not in command:
        return processes, "TARGETED_PROCESS_IDENTITY_MISMATCH", False
    lock_time = _parse_evidence_time(lock_created_at)
    process_time = _parse_evidence_time(targeted.start_time)
    if lock_time and process_time and process_time > lock_time:
        return processes, "PROCESS_START_TIME_MISMATCH", False
    return [*processes, targeted], "BULK_PROCESS_INVENTORY_INCOMPLETE", True


def _process_snapshot_from_netstat() -> list[ProcessInfo]:
    """Last-resort listener ownership only; insufficient for non-port service recovery."""
    raw = subprocess.check_output(["netstat", "-ano"], text=True, timeout=5, errors="ignore")
    pids: set[int] = set()
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
            try:
                pids.add(int(parts[4]))
            except ValueError:
                continue
    if not pids:
        raise RuntimeError("netstat returned no listener owners")
    return [ProcessInfo(pid, "") for pid in sorted(pids)]


def _process_snapshot() -> ProcessSnapshot:
    """Return independent process evidence without treating one CIM failure as host failure."""
    failures: list[str] = []
    for source, visibility, reader in (
        ("CIM", "available", _process_snapshot_from_cim),
        ("psutil", "partial", _process_snapshot_from_psutil),
        ("Get-Process", "partial", _process_snapshot_from_get_process),
        ("netstat", "port_only", _process_snapshot_from_netstat),
    ):
        try:
            rows = reader()
            reason = None if not failures else "; ".join(failures)
            return ProcessSnapshot(processes=rows, visibility=visibility, reason=reason)
        except (ImportError, OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            failures.append(f"{source}:{type(exc).__name__}")
    return ProcessSnapshot(processes=[], visibility="unavailable", reason="; ".join(failures))


def _processes() -> list[ProcessInfo]:
    return _process_snapshot().processes


def _market_data_now(tz: timezone | None) -> datetime:
    return datetime.now(tz or timezone.utc)


def _specs() -> tuple[ServiceSpec, ...]:
    return (
        ServiceSpec("trading_bot_mcp", "Trading Bot MCP", ("src.mcp.trading_bot_server",), "run_trading_bot_mcp.cmd", port=8765, required=True, readiness_url="http://127.0.0.1:8765/health", detached_launcher=True),
        ServiceSpec("strategy_scanner_mcp", "Strategy Scanner MCP", ("scanner_mcp.server",), "start-strategy-scanner-mcp.cmd", port=8766, required=True, readiness_url="http://127.0.0.1:8766/health", detached_launcher=True),
        ServiceSpec("dashboard", "Dashboard", ("dashboard_react.server:app",), "run_dashboard_api.cmd", port=8550, readiness_url="http://127.0.0.1:8550/api/radar/top?limit=1", detached_launcher=True),
        ServiceSpec(
            "market_data",
            "Market Data",
            ("--job market_data",),
            "run_market_data_collector.cmd",
            RUNTIME / "market_data_collector_status.json",
            MARKET_DATA_STATUS_MAX_AGE,
            required=True,
        ),
        ServiceSpec("execute_worker", "Execute Worker", ("--job execute_requests",), "run_execute_worker.cmd", RUNTIME / "execute_worker.json", 30.0, required=True),
        ServiceSpec("autonomous_worker", "Autonomous Worker", ("src.jobs.autonomous_stock_worker",), "run_autonomous_stock_worker.cmd", RUNTIME / "autonomous_stock_worker.json", 30.0, required=False),
        ServiceSpec("crypto_worker", "Crypto Worker", ("src.crypto.main --job worker", "-m src.crypto.main --job worker"), "run_crypto_worker.cmd", ROOT / "memory" / "crypto" / "runtime" / "worker.json", 900.0, required=False),
    )


def _matching(spec: ServiceSpec, processes: Iterable[ProcessInfo]) -> list[ProcessInfo]:
    return [p for p in processes if any(pattern in p.command_line for pattern in spec.patterns)]


def _logical_roots(matches: list[ProcessInfo], all_processes: Iterable[ProcessInfo]) -> list[ProcessInfo]:
    """Collapse a launcher/wrapper and its matching child into one logical root."""
    matched = {p.pid for p in matches}
    roots = [p for p in matches if p.parent_pid not in matched]
    return roots or matches


def _descendant_pids(root_pid: int, processes: Iterable[ProcessInfo]) -> set[int]:
    children: dict[int, list[int]] = {}
    for process in processes:
        if process.parent_pid is not None:
            children.setdefault(process.parent_pid, []).append(process.pid)
    seen: set[int] = set()
    stack = list(children.get(root_pid, []))
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(children.get(pid, []))
    return seen


def _market_data_session_state(now: datetime | None = None) -> str:
    try:
        from src.config import SETTINGS

        tz = ZoneInfo(SETTINGS.trading_hours.timezone)
        current = (now or datetime.now(tz)).astimezone(tz)
        if current.weekday() >= 5:
            return "WEEKEND_OR_HOLIDAY"
        start = current.replace(
            hour=int(SETTINGS.trading_hours.market_open_hour),
            minute=int(SETTINGS.trading_hours.market_open_minute),
            second=0,
            microsecond=0,
        )
        end = current.replace(
            hour=int(SETTINGS.trading_hours.market_data_collector_end_hour),
            minute=int(SETTINGS.trading_hours.market_data_collector_end_minute),
            second=0,
            microsecond=0,
        )
        if current < start:
            return "PRE_SESSION"
        if current <= end:
            return "ACTIVE_SESSION"
        return "POST_SESSION"
    except Exception:
        return "UNKNOWN"


def _heartbeat(spec: ServiceSpec) -> dict[str, Any]:
    if spec.heartbeat is None:
        return {"available": False}
    if not spec.heartbeat.exists():
        return {"available": False, "path": str(spec.heartbeat)}
    try:
        age = max(0.0, time.time() - spec.heartbeat.stat().st_mtime)
    except OSError:
        return {"available": False, "path": str(spec.heartbeat)}
    payload = _json(spec.heartbeat) or {}
    return {"available": True, "age_seconds": round(age, 2), "fresh": age <= spec.heartbeat_max_age, "path": str(spec.heartbeat), "payload_status": payload.get("status") or payload.get("market_data_status")}


def _market_data_heartbeat(spec: ServiceSpec) -> dict[str, Any]:
    """Read collector cycle status; the lock is only process-ownership evidence.

    The collector can spend several minutes in one IBKR collection/retry cycle,
    so file mtime and the generic worker heartbeat window are not valid liveness
    signals here.  A recent completed cycle is the authoritative operational
    status, while collection quality is surfaced independently.
    """
    if spec.heartbeat is None or not spec.heartbeat.exists():
        return {"available": False, "fresh": False, "path": str(spec.heartbeat), "reason_code": "STATUS_ARTIFACT_MISSING"}
    payload = _json(spec.heartbeat)
    if not payload:
        return {"available": False, "fresh": False, "path": str(spec.heartbeat), "reason_code": "STATUS_ARTIFACT_UNREADABLE"}

    def _age(value: Any) -> float | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return max(0.0, time.time() - stamp.timestamp())
        except (TypeError, ValueError, OverflowError):
            return None

    def _stamp(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return stamp
        except (TypeError, ValueError, OverflowError):
            return None

    def _off_session_recent_close(last_cycle: datetime | None) -> bool:
        if last_cycle is None:
            return False
        try:
            from src.config import SETTINGS

            now_local = _market_data_now(last_cycle.tzinfo)
            if now_local.weekday() >= 5:
                return False
            close_window = now_local.replace(
                hour=int(SETTINGS.trading_hours.market_data_collector_end_hour),
                minute=int(SETTINGS.trading_hours.market_data_collector_end_minute),
                second=0,
                microsecond=0,
            )
            if now_local <= close_window:
                return False
            return (
                last_cycle.astimezone(close_window.tzinfo).date() == close_window.date()
                and last_cycle >= close_window - timedelta(seconds=spec.heartbeat_max_age)
            )
        except Exception:
            return False

    last_heartbeat_at = _stamp(payload.get("last_heartbeat"))
    last_heartbeat_age = _age(payload.get("last_heartbeat"))
    last_cycle_at = _stamp(payload.get("last_cycle_at"))
    latest_fresh_cycle_at = _stamp(payload.get("latest_fresh_cycle_at"))
    last_cycle_age = _age(payload.get("last_cycle_at"))
    latest_fresh_age = _age(payload.get("latest_fresh_cycle_at"))
    cycle_age = last_cycle_age if last_cycle_age is not None else latest_fresh_age
    readable = cycle_age is not None or last_heartbeat_age is not None
    health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    quality = {key: payload.get(key, health.get(key)) for key in ("requested", "received", "fresh", "advanced", "stale", "no_data", "errors")}
    off_session_recent_close = _off_session_recent_close(last_cycle_at or latest_fresh_cycle_at)
    runtime_fresh = last_heartbeat_age is not None and last_heartbeat_age <= spec.heartbeat_max_age
    cycle_fresh = cycle_age is not None and cycle_age <= spec.heartbeat_max_age
    session_state = str(payload.get("current_session") or _market_data_session_state()).upper()
    runtime_state = str(payload.get("state") or payload.get("status") or "UNKNOWN").upper()
    waiting_session = session_state in {"PRE_SESSION", "POST_SESSION", "WEEKEND_OR_HOLIDAY"}
    active_starting = session_state == "ACTIVE_SESSION" and runtime_state in {"STARTING", "COLLECTING"} and cycle_age is None
    fresh = readable and (cycle_fresh or off_session_recent_close or (runtime_fresh and (waiting_session or active_starting)))
    heartbeat_age = last_heartbeat_age if last_heartbeat_age is not None else cycle_age
    return {
        "available": readable,
        "fresh": fresh,
        "age_seconds": round(heartbeat_age, 2) if heartbeat_age is not None else None,
        "last_heartbeat_age_seconds": round(last_heartbeat_age, 2) if last_heartbeat_age is not None else None,
        "last_cycle_age_seconds": round(last_cycle_age, 2) if last_cycle_age is not None else None,
        "latest_fresh_cycle_age_seconds": round(latest_fresh_age, 2) if latest_fresh_age is not None else None,
        "path": str(spec.heartbeat),
        "payload_status": payload.get("market_data_status"),
        "runtime_state": runtime_state,
        "runtime_fresh": runtime_fresh,
        "data_quality": str(payload.get("market_data_status") or "UNKNOWN").upper(),
        "quality": quality,
        "session_state": "OFF_SESSION_RECENT_CLOSE" if off_session_recent_close else session_state,
        "status_artifact_readable": readable,
        "pid": payload.get("pid"),
        **({} if readable else {"reason_code": "STATUS_ARTIFACT_TIMESTAMP_INVALID"}),
    }


def _port(port: int | None) -> dict[str, Any]:
    if not port:
        return {"checked": False}
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return {"checked": True, "reachable": True, "host": "127.0.0.1", "port": port}
    except OSError as exc:
        return {"checked": True, "reachable": False, "host": "127.0.0.1", "port": port, "reason": type(exc).__name__}


def _port_listeners(port: int | None) -> list[dict[str, Any]]:
    if not port:
        return []
    try:
        raw = subprocess.check_output(["netstat", "-ano"], text=True, timeout=5, errors="ignore")
    except (OSError, subprocess.SubprocessError):
        return []
    records: list[dict[str, Any]] = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING" and parts[1].rsplit(":", 1)[-1] == str(port):
            try:
                records.append({"address": parts[1].rsplit(":", 1)[0], "pid": int(parts[4])})
            except ValueError:
                continue
    return records


def _port_owner(port: int | None) -> int | None:
    loopback_owners = {row["pid"] for row in _port_listeners(port) if row.get("address") == "127.0.0.1"}
    return next(iter(loopback_owners)) if len(loopback_owners) == 1 else None


def _get_json(url: str, timeout: float = API_TIMEOUT_SECONDS) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
        return value if isinstance(value, dict) else {"value": value}
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {"status": "UNAVAILABLE", "reason": type(exc).__name__}


def _execute_worker_paths() -> tuple[Path, Path]:
    """Resolve the same durable stores used by the production worker."""
    from src.config import SETTINGS
    from src.execution.order_requests import ORDER_REQUESTS_PATH

    return Path(SETTINGS.decision_agent.database_path), Path(ORDER_REQUESTS_PATH)


def _read_execute_worker_preflight(
    *,
    database_path: Path | None = None,
    queue_path: Path | None = None,
    processes: list[ProcessInfo] | None = None,
    process_visibility: str = "available",
    process_visibility_reason: str | None = None,
) -> dict[str, Any]:
    """Inspect Execute Worker startup inputs without opening a writable store.

    The production worker processes approvals first and then claims pending
    order requests on every poll.  This function deliberately uses SQLite's
    read-only URI and direct JSON parsing so preflight cannot migrate, expire,
    claim, update, or otherwise mutate trading state.
    """
    result: dict[str, Any] = {
        "command": "execute-worker-preflight",
        "verdict": "UNKNOWN",
        "read_only": True,
        "worker_startup_behavior": {
            "approval_phase": "process_approved_once before process_pending_once",
            "approved_unconsumed_behavior": "may revalidate, create an execution intent, enqueue a request, and reach broker processing on the same tick",
            "pending_request_behavior": "claim_pending marks pending requests PROCESSING before broker processing",
        },
        "counts": {
            "approved_unconsumed": None,
            "approved_with_execution_intent": None,
            "approval_jobs_pending": None,
            "approval_jobs_processing": None,
            "pending_actionable_requests": None,
            "processing_actionable_requests": None,
            "actionable_execution_requests": None,
        },
        "evidence": {},
        "errors": [],
        "orders_submitted": 0,
        "approvals_created": 0,
        "execution_intents_created": 0,
    }
    try:
        resolved_db, resolved_queue = _execute_worker_paths()
        db = Path(database_path or resolved_db)
        queue = Path(queue_path or resolved_queue)
    except Exception as exc:
        result["errors"].append({"source": "runtime_configuration", "reason_code": "CONFIGURATION_UNAVAILABLE", "error": type(exc).__name__})
        result["reason_code"] = "PREFLIGHT_EVIDENCE_INCOMPLETE"
        return result

    result["evidence"]["approval_database"] = {"path": str(db), "source": "SETTINGS.decision_agent.database_path"}
    result["evidence"]["execution_queue"] = {"path": str(queue), "source": "src.execution.order_requests.ORDER_REQUESTS_PATH"}

    try:
        import sqlite3

        if not db.exists():
            raise FileNotFoundError(str(db))
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1) as conn:
            conn.row_factory = sqlite3.Row
            tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"trade_approvals", "trade_execution_intents", "trade_approval_jobs"}
            missing = sorted(required - tables)
            if missing:
                raise RuntimeError("missing_tables:" + ",".join(missing))
            approved = int(conn.execute("SELECT COUNT(*) FROM trade_approvals WHERE status='APPROVED'").fetchone()[0])
            with_intent = int(conn.execute(
                "SELECT COUNT(*) FROM trade_approvals a "
                "JOIN trade_execution_intents i ON i.approval_id=a.approval_id "
                "WHERE a.status='APPROVED'"
            ).fetchone()[0])
            jobs_pending = int(conn.execute("SELECT COUNT(*) FROM trade_approval_jobs WHERE status='PENDING'").fetchone()[0])
            jobs_processing = int(conn.execute("SELECT COUNT(*) FROM trade_approval_jobs WHERE status='PROCESSING'").fetchone()[0])
        result["counts"].update(
            approved_unconsumed=max(0, approved - with_intent),
            approved_with_execution_intent=with_intent,
            approval_jobs_pending=jobs_pending,
            approval_jobs_processing=jobs_processing,
        )
        result["evidence"]["approval_database"].update({"readable": True, "required_tables_present": True})
    except Exception as exc:
        result["errors"].append({"source": "approval_database", "reason_code": "DATABASE_ACCESS_FAILED", "error": type(exc).__name__})

    try:
        if not queue.exists():
            records: list[Any] = []
        else:
            payload = json.loads(queue.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("requests"), list):
                raise ValueError("invalid_queue_shape")
            records = payload["requests"]
        pending = [row for row in records if isinstance(row, dict) and row.get("status") == "pending"]
        processing = [row for row in records if isinstance(row, dict) and row.get("status") == "processing"]
        result["counts"].update(
            pending_actionable_requests=len(pending),
            processing_actionable_requests=len(processing),
            actionable_execution_requests=len(pending) + len(processing),
        )
        result["evidence"]["execution_queue"].update({"readable": True, "record_count": len(records), "pending_statuses": ["pending", "processing"]})
    except Exception as exc:
        result["errors"].append({"source": "execution_queue", "reason_code": "QUEUE_ACCESS_FAILED", "error": type(exc).__name__})

    observed = list(processes if processes is not None else _processes())
    matches = _matching(ServiceSpec("execute_worker", "Execute Worker", ("--job execute_requests",), None), observed)
    roots = _logical_roots(matches, observed)
    result["evidence"]["processes"] = {
        "visibility": {"status": process_visibility, "reason": process_visibility_reason},
        "matching_pids": [p.pid for p in matches],
        "logical_root_pids": [p.pid for p in roots],
        "logical_instance_count": len(roots),
    }
    if process_visibility != "available":
        result["errors"].append({"source": "process_inspection", "reason_code": "PROCESS_VISIBILITY_UNAVAILABLE"})
    elif len(roots) > 1:
        result["errors"].append({"source": "process_inspection", "reason_code": "DUPLICATE_EXECUTE_WORKER"})
    elif len(roots) == 1:
        result["errors"].append({"source": "process_inspection", "reason_code": "EXECUTE_WORKER_ALREADY_RUNNING"})

    counts = result["counts"]
    evidence_complete = not result["errors"] or all(
        error["reason_code"] in {"EXECUTE_WORKER_ALREADY_RUNNING", "DUPLICATE_EXECUTE_WORKER"}
        for error in result["errors"]
    )
    startup_order_risk = (
        (counts["approved_unconsumed"] or 0) > 0
        or (counts["approval_jobs_pending"] or 0) > 0
        or (counts["approval_jobs_processing"] or 0) > 0
        or (counts["actionable_execution_requests"] or 0) > 0
    )
    result["could_submit_ibkr_paper_orders_on_startup"] = startup_order_risk
    result["safe_for_paper_execution_is_not_startup_empty"] = True
    if not evidence_complete:
        result["verdict"] = "UNKNOWN"
        result["reason_code"] = "PREFLIGHT_EVIDENCE_INCOMPLETE"
    elif len(roots) == 1:
        result["verdict"] = "BLOCKED"
        result["reason_code"] = "EXECUTE_WORKER_ALREADY_RUNNING"
    elif len(roots) > 1 or startup_order_risk:
        result["verdict"] = "BLOCKED"
        result["reason_code"] = "STARTUP_ORDER_RISK" if startup_order_risk else "DUPLICATE_EXECUTE_WORKER"
    else:
        result["verdict"] = "SAFE_TO_START"
        result["reason_code"] = "NO_APPROVED_OR_ACTIONABLE_WORK"
    return result


def _start(spec: ServiceSpec) -> dict[str, Any]:
    if not spec.launcher:
        return {"ok": False, "reason": "no_approved_launcher"}
    path = ROOT / spec.launcher
    if not path.exists():
        return {"ok": False, "reason": "launcher_missing", "path": str(path)}
    try:
        if spec.detached_launcher:
            completed = subprocess.run(
                ["cmd.exe", "/d", "/c", str(path)],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                timeout=30,
            )
            output = (completed.stdout or "").strip()
            error = (completed.stderr or "").strip()
            return {
                "ok": completed.returncode == 0,
                "exit_code": completed.returncode,
                "stdout_tail": output[-1200:] if output else "",
                "stderr_tail": error[-1200:] if error else "",
                "reason": None if completed.returncode == 0 else "launcher_exit_nonzero",
            }
        subprocess.Popen(
            ["cmd.exe", "/d", "/c", str(path)],
            cwd=str(ROOT),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"ok": True}
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "reason": "launcher_timeout",
            "stdout_tail": (exc.stdout or "")[-1200:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-1200:] if isinstance(exc.stderr, str) else "",
        }
    except OSError as exc:
        return {"ok": False, "reason": f"launcher_failed:{type(exc).__name__}"}


class Supervisor:
    def __init__(self, *, process_provider: Callable[[], list[ProcessInfo]] | None = None, sleep: Callable[[float], None] = time.sleep, now: Callable[[], datetime] | None = None):
        self.process_provider = process_provider or _processes
        self._uses_default_process_provider = process_provider is None
        self._last_process_snapshot = ProcessSnapshot(processes=[], visibility="unknown")
        self.sleep = sleep
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _process_snapshot(self) -> ProcessSnapshot:
        if self._uses_default_process_provider:
            if self._last_process_snapshot.visibility == "unavailable":
                return self._last_process_snapshot
            self._last_process_snapshot = _process_snapshot()
        else:
            self._last_process_snapshot = ProcessSnapshot(processes=list(self.process_provider()))
        return self._last_process_snapshot

    def _current_processes(self) -> list[ProcessInfo]:
        return self._process_snapshot().processes

    def execute_worker_preflight(self) -> dict[str, Any]:
        """Return a read-only, fail-closed Execute Worker startup verdict."""
        snapshot = self._process_snapshot()
        return _read_execute_worker_preflight(
            processes=snapshot.processes,
            process_visibility=snapshot.visibility,
            process_visibility_reason=snapshot.reason,
        )

    def _database(self) -> dict[str, Any]:
        db = ROOT / "memory" / "decision_lab.db"
        if not db.exists():
            return {"status": "DEGRADED", "reason_code": "DATABASE_NOT_FOUND", "path": str(db)}
        try:
            import sqlite3
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1) as conn:
                conn.execute("PRAGMA schema_version").fetchone()
            return {"status": "HEALTHY", "path": str(db), "readable": True, "writable": os.access(db, os.W_OK)}
        except Exception as exc:
            return {"status": "FAILED", "reason_code": "DATABASE_UNREADABLE", "reason": type(exc).__name__, "path": str(db)}

    def _execution_boundary(self) -> dict[str, Any]:
        """Read-only gate preventing worker bootstrap during ambiguous execution."""
        db = ROOT / "memory" / "decision_lab.db"
        try:
            import sqlite3
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                active: list[dict[str, Any]] = []
                if "execution_reservations" in tables:
                    rows = conn.execute(
                        "SELECT reservation_id,state FROM execution_reservations "
                        "WHERE state IN ('RESERVED','READY_TO_SUBMIT','SUBMITTING','SUBMITTED','SUBMISSION_UNKNOWN','PAPER_MUTATING','RECONCILIATION_REQUIRED')"
                    ).fetchall()
                    active.extend({"id": row[0], "state": row[1], "source": "execution_reservations"} for row in rows)
                if "trade_approvals" in tables:
                    rows = conn.execute(
                        "SELECT approval_id,status FROM trade_approvals "
                        "WHERE status IN ('SUBMITTING','EXECUTION_READY')"
                    ).fetchall()
                    active.extend({"id": row[0], "state": row[1], "source": "trade_approvals"} for row in rows)
                if "trade_execution_intents" in tables:
                    rows = conn.execute(
                        "SELECT execution_intent_id,status FROM trade_execution_intents "
                        "WHERE status IN ('EXECUTION_READY','SUBMITTING')"
                    ).fetchall()
                    active.extend({"id": row[0], "state": row[1], "source": "trade_execution_intents"} for row in rows)
            if active:
                return {"safe_to_start": False, "status": "ACTION_REQUIRED", "reason_code": "ACTIVE_EXECUTION_STATE", "active_execution": active, "action_required": "Inspect the durable execution state before restarting or starting Execute Worker."}
            return {"safe_to_start": True, "status": "SAFE_TO_START"}
        except Exception as exc:
            return {"safe_to_start": False, "status": "ACTION_REQUIRED", "reason_code": "EXECUTION_STATE_UNKNOWN", "reason": type(exc).__name__, "action_required": "Resolve execution-state visibility before starting Execute Worker."}

    def _broker(self) -> dict[str, Any]:
        result: dict[str, Any] = {"status": "WAITING_FOR_IBKR", "gateway_running": False, "socket_reachable": False, "account_identity_verified": False}
        gateway_snapshot = self._process_snapshot()
        gateway = [p for p in gateway_snapshot.processes if any(x in p.command_line.lower() for x in ("ibgateway", "tws", "jts.exe"))]
        result["gateway_running"] = bool(gateway)
        result["process_visibility"] = {"status": gateway_snapshot.visibility, "reason": gateway_snapshot.reason}
        try:
            from src.config import SETTINGS
            host, port = SETTINGS.broker.host, int(SETTINGS.broker.port)
        except Exception as exc:
            result["reason_code"] = "BROKER_CONFIG_UNAVAILABLE"
            result["reason"] = type(exc).__name__
            return result
        try:
            with socket.create_connection((host, port), timeout=1.5):
                result["socket_reachable"] = True
        except OSError:
            result["reason_code"] = "IBKR_LOGIN_REQUIRED"
            result["action_required"] = "Open TWS or IB Gateway and complete login/2FA, then run readiness-recheck."
            return result
        try:
            identity = self._broker_identity()
            result.update({"account": identity, "account_identity_verified": bool(identity.get("account_id"))})
            configured_accounts = tuple(getattr(getattr(SETTINGS, "decision_agent", None), "ibkr_paper_account_allowlist", ()) or ())
            expected = os.getenv("IBKR_EXPECTED_ACCOUNT", "").strip().upper()
            expected_accounts = {expected} if expected else {str(value).strip().upper() for value in configured_accounts if str(value).strip()}
            result["expected_accounts_configured"] = sorted(expected_accounts)
            if expected_accounts and identity.get("account_id", "").upper() not in expected_accounts:
                result.update(status="ACTION_REQUIRED", reason_code="ACCOUNT_IDENTITY_MISMATCH", action_required="Verify the configured account in TWS/IB Gateway.")
            elif not expected_accounts:
                result.update(status="ACTION_REQUIRED", reason_code="EXPECTED_ACCOUNT_NOT_CONFIGURED", action_required="Configure the expected Paper account allowlist before market open.")
            elif identity.get("environment") != "paper" or not identity.get("paper_verified"):
                result.update(status="ACTION_REQUIRED", reason_code="BROKER_ENVIRONMENT_MISMATCH", action_required="Expected a verified Paper account; no trading action was taken.")
            else:
                result.update(status="HEALTHY", environment="PAPER")
        except BrokerIdentityProbeError as exc:
            result.update(
                status="ACTION_REQUIRED",
                reason_code=exc.reason_code,
                reason=type(exc).__name__,
                action_required=(
                    "Install the approved broker-client dependency in the selected runtime, "
                    "then rerun readiness-recheck."
                    if exc.reason_code == "IBKR_DEPENDENCY_MISSING"
                    else "Inspect the read-only broker identity probe, then rerun readiness-recheck."
                ),
            )
        except Exception as exc:
            result.update(status="WAITING_FOR_IBKR", reason_code="IBKR_LOGIN_REQUIRED", reason=type(exc).__name__, action_required="Open TWS or IB Gateway and complete login/2FA, then run readiness-recheck.")
        return result

    def _broker_identity(self) -> dict[str, Any]:
        code = textwrap.dedent(
            """
            import json
            from src.brokers.ibkr import IBKRClient

            client = IBKRClient()
            try:
                client.connect()
                print(json.dumps(client.get_account_identity()))
            finally:
                try:
                    client.disconnect()
                except Exception:
                    pass
            """
        )
        try:
            completed = subprocess.run(
                [sys.executable, "-B", "-c", code],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                timeout=BROKER_IDENTITY_TIMEOUT_SECONDS,
            )
            if completed.returncode != 0:
                stderr = str(completed.stderr or "")
                if "ib_insync is not installed" in stderr:
                    raise BrokerIdentityProbeError("IBKR_DEPENDENCY_MISSING")
                raise BrokerIdentityProbeError("IBKR_PROBE_EXECUTION_FAILED")
            value = json.loads(str(completed.stdout or "").strip())
            return value if isinstance(value, dict) else {}
        except subprocess.TimeoutExpired as exc:
            raise BrokerIdentityProbeError("IBKR_PROBE_EXECUTION_FAILED") from exc
        except json.JSONDecodeError as exc:
            raise BrokerIdentityProbeError("IBKR_PROBE_EXECUTION_FAILED") from exc

    def _service(self, spec: ServiceSpec, *, start_missing: bool, processes: list[ProcessInfo], actions: list[Any], process_visibility: str = "available", process_visibility_reason: str | None = None) -> dict[str, Any]:
        matches = _matching(spec, processes)
        roots = _logical_roots(matches, processes)
        row: dict[str, Any] = {
            "service": spec.key, "label": spec.label, "required": spec.required,
            "launcher": spec.launcher, "logical_instances": len(roots),
            "observed_count": len(matches), "pids": [p.pid for p in matches],
            "root_pids": [p.pid for p in roots], "process_running": bool(matches),
            "process_visibility": {"status": process_visibility, "reason": process_visibility_reason},
            "heartbeat_age_seconds": None, "api_status": None, "action_taken": None,
            "reason_code": None, "safe_auto_repair": bool(spec.launcher),
            "recommended_action": None, "operational_state": "UNKNOWN",
        }
        if spec.port:
            listeners = _port_listeners(spec.port)
            row["port_listeners"] = listeners
            row["port_conflict"] = len({listener["pid"] for listener in listeners}) > 1
        if len(roots) > 1:
            row.update(status="DUPLICATE_PROCESS", reason_code="DUPLICATE_PROCESS_DETECTED", safe_auto_repair=False,
                        recommended_action="Stop one root only after confirming no active execution state.")
            return row
        if not roots and spec.port:
            pre_start_port = _port(spec.port)
            if pre_start_port.get("reachable"):
                pre_start_readiness = self._api(spec.readiness_url, spec.label) if spec.readiness_url else {"status": "HEALTHY"}
                owner = _port_owner(spec.port)
                row["port"] = pre_start_port
                row["readiness"] = pre_start_readiness
                if pre_start_readiness.get("status") == "HEALTHY" and owner is not None:
                    row.update(observed_count=1, logical_instances=1, root_pids=[owner], pids=[owner], process_running=True, port_owner_pid=owner, port_identity_verified=True)
                elif pre_start_readiness.get("status") == "HEALTHY" and process_visibility == "unavailable":
                    row.update(process_running=True, port_identity_verified=True)
                else:
                    row.update(
                        status="ACTION_REQUIRED" if start_missing else "UNKNOWN",
                        operational_state="UNKNOWN",
                        reason_code="PORT_OWNERSHIP_AMBIGUOUS",
                        port_owner_pid=owner,
                        port_identity_verified=False,
                        recommended_action=f"Inspect PID {owner or 'UNKNOWN'} on port {spec.port} before starting {spec.label}.",
                        safe_auto_repair=False,
                    )
                    return row
        if row.get("process_running"):
            start_missing = False
        if not roots and start_missing and process_visibility == "available":
            if spec.key == "execute_worker":
                boundary = self._execution_boundary()
                if not boundary.get("safe_to_start"):
                    row.update(status="ACTION_REQUIRED", reason_code=boundary.get("reason_code"), action_required=boundary.get("action_required"), recommended_action=boundary.get("action_required"), safe_auto_repair=False, execution_boundary=boundary)
                    return row
            start_result = _start(spec)
            if start_result is None:
                start_result = {"ok": True}
            elif isinstance(start_result, str):
                start_result = {"ok": False, "reason": start_result}
            elif not isinstance(start_result, dict):
                start_result = {"ok": True}
            if not start_result.get("ok"):
                row.update(status="FAILED", reason_code="START_FAILED", launcher_result=start_result, reason=start_result.get("reason"), recommended_action="Inspect the approved launcher and its runtime log.")
            else:
                actions.append({"action": "start", "service": spec.key, "label": spec.label, "launcher": spec.launcher, "result": "STARTED", "launcher_result": start_result})
                row.update(status="STARTING", operational_state="STARTING")
                refreshed_all = processes
                refreshed: list[ProcessInfo] = []
                refreshed_roots: list[ProcessInfo] = []
                for _ in range(STARTUP_VERIFY_POLLS):
                    self.sleep(STARTUP_VERIFY_INTERVAL_SECONDS)
                    refreshed_all = self._current_processes()
                    refreshed = _matching(spec, refreshed_all)
                    refreshed_roots = _logical_roots(refreshed, refreshed_all)
                    if refreshed_roots:
                        break
                row.update(observed_count=len(refreshed), logical_instances=len(refreshed_roots), root_pids=[p.pid for p in refreshed_roots], pids=[p.pid for p in refreshed], process_running=bool(refreshed))
                if not refreshed_roots:
                    # Second and final bounded start attempt. Never retry an
                    # ambiguous or live execution state.
                    retry_result = _start(spec)
                    if retry_result is None:
                        retry_result = {"ok": True}
                    elif isinstance(retry_result, str):
                        retry_result = {"ok": False, "reason": retry_result}
                    elif not isinstance(retry_result, dict):
                        retry_result = {"ok": True}
                    actions.append({"action": "retry_start", "service": spec.key, "label": spec.label, "launcher": spec.launcher, "result": "STARTED" if retry_result.get("ok") else "FAILED", "launcher_result": retry_result})
                    if not retry_result.get("ok"):
                        row.update(status="ACTION_REQUIRED", operational_state="UNHEALTHY", reason_code="START_RETRY_FAILED", launcher_result=retry_result, reason=retry_result.get("reason"), recommended_action="Inspect the approved launcher and its runtime log.")
                    else:
                        for _ in range(STARTUP_VERIFY_POLLS):
                            self.sleep(STARTUP_VERIFY_INTERVAL_SECONDS)
                            refreshed_all = self._current_processes()
                            refreshed = _matching(spec, refreshed_all)
                            refreshed_roots = _logical_roots(refreshed, refreshed_all)
                            if refreshed_roots:
                                break
                        row.update(observed_count=len(refreshed), logical_instances=len(refreshed_roots), root_pids=[p.pid for p in refreshed_roots], pids=[p.pid for p in refreshed], process_running=bool(refreshed))
        hb = _market_data_heartbeat(spec) if spec.key == "market_data" else _heartbeat(spec)
        row["heartbeat"] = hb
        row["heartbeat_age_seconds"] = hb.get("age_seconds")
        if spec.key == "market_data":
            lock_path = (
                spec.heartbeat.with_name("market_data_collector.lock")
                if spec.heartbeat is not None
                else RUNTIME / "market_data_collector.lock"
            )
            lock_owner: int | None = None
            lock_created_at: str | None = None
            if lock_path.exists():
                try:
                    lock_parts = lock_path.read_text(encoding="utf-8", errors="ignore").split("|", 1)
                    owner_text = lock_parts[0].strip()
                    lock_created_at = lock_parts[1].strip() if len(lock_parts) > 1 else None
                    lock_owner = int(owner_text) if owner_text else None
                except (OSError, TypeError, ValueError):
                    lock_owner = None
            processes, evidence_note, ownership_valid = _reconcile_market_data_process(processes, hb, lock_owner, lock_created_at)
            if evidence_note:
                row["process_visibility"] = {"status": "partial", "reason": evidence_note}
            matches = _matching(spec, processes)
            roots = _logical_roots(matches, processes)
            row.update(
                observed_count=len(matches), logical_instances=len(roots),
                root_pids=[p.pid for p in roots], pids=[p.pid for p in matches],
                process_running=bool(matches),
            )
            live_pids = {p.pid for p in matches}
            descendant_pids: set[int] = set()
            for root in roots:
                descendant_pids.update(_descendant_pids(root.pid, processes))
            lock_owner_matches_process = ownership_valid and (lock_owner is None or lock_owner in live_pids or lock_owner in descendant_pids)
            row["lock"] = {
                "path": str(lock_path),
                "owner_pid": lock_owner,
                "owner_matches_process_tree": lock_owner_matches_process,
            }
            if lock_owner is not None and not lock_owner_matches_process:
                row.update(
                    status="UNKNOWN",
                    operational_state="UNKNOWN",
                    reason_code=("PROCESS_EVIDENCE_INCOMPLETE" if evidence_note == "TARGETED_PROCESS_EVIDENCE_UNAVAILABLE" else "MARKET_DATA_LOCK_OWNER_MISMATCH"),
                    recommended_action=("Re-run with complete Windows process visibility before starting or stopping Market Data." if evidence_note == "TARGETED_PROCESS_EVIDENCE_UNAVAILABLE" else "Inspect the collector process tree and lock owner before starting or stopping Market Data."),
                    safe_auto_repair=False,
                )
        row["port"] = _port(spec.port)
        if row["port"].get("checked") and not row["port"].get("reachable"):
            # A launcher can report its PID before the HTTP server has bound.
            # Recheck briefly before classifying the start as unavailable.
            for _ in range(2):
                self.sleep(0.25)
                row["port"] = _port(spec.port)
                if row["port"].get("reachable"):
                    break
        readiness = {"checked": False}
        if spec.readiness_url:
            readiness = self._api(spec.readiness_url, spec.label)
        row["readiness"] = readiness
        if not row["process_running"] and row["port"].get("reachable"):
            owner = _port_owner(spec.port)
            if owner is not None:
                row.update(observed_count=1, logical_instances=1, root_pids=[owner], pids=[owner], process_running=True)
            elif process_visibility == "unavailable":
                row.update(process_running=True)
        row["api_status"] = None
        row["action_taken"] = next((action for action in reversed(actions) if isinstance(action, dict) and action.get("service") == spec.key), None)
        healthy_hb = (
            hb.get("available") and hb.get("fresh")
            if spec.key == "market_data"
            else not hb.get("available") or hb.get("fresh")
        )
        if not row["process_running"] and process_visibility == "unavailable" and hb.get("available") and hb.get("fresh"):
            row.update(process_running=True)
        healthy_port = not row["port"].get("checked") or row["port"].get("reachable")
        healthy_readiness = not readiness.get("checked", True) or readiness.get("status") == "HEALTHY"
        if row.get("status") not in {"FAILED", "ACTION_REQUIRED", "DUPLICATE_PROCESS", "UNKNOWN"}:
            if not row["process_running"]:
                if process_visibility == "unavailable":
                    row["status"] = "UNKNOWN"
                    row["operational_state"] = "UNKNOWN"
                    row["reason_code"] = "PROCESS_VISIBILITY_UNAVAILABLE"
                    row["recommended_action"] = "Re-run from an environment that can read process metadata or use a service-specific heartbeat/port readiness signal."
                else:
                    row["status"] = "ACTION_REQUIRED" if start_missing else "MISSING"
                    row["operational_state"] = "UNHEALTHY" if start_missing else "STOPPED"
                    row["reason_code"] = "START_VERIFICATION_FAILED" if start_missing else "SERVICE_MISSING"
                    row["recommended_action"] = "Inspect the approved launcher and its runtime log." if start_missing else "Run morning startup to invoke the approved launcher."
            else:
                if spec.key == "market_data" and hb.get("session_state") in {"PRE_SESSION", "POST_SESSION", "WEEKEND_OR_HOLIDAY", "OFF_SESSION_RECENT_CLOSE"} and hb.get("runtime_fresh"):
                    row["status"] = "WAITING_FOR_SESSION"
                    row["operational_state"] = "HEALTHY"
                elif healthy_hb and healthy_port and healthy_readiness:
                    row["status"] = "DEGRADED" if spec.key == "market_data" and hb.get("data_quality") == "DEGRADED" else "HEALTHY"
                    row["operational_state"] = "HEALTHY"
                else:
                    row["status"] = "UNAVAILABLE"
                    row["operational_state"] = "UNHEALTHY"
                    if spec.key == "market_data" and hb.get("reason_code"):
                        row["reason_code"] = hb["reason_code"]
                    elif spec.key == "market_data" and hb.get("runtime_fresh") and hb.get("session_state") == "ACTIVE_SESSION":
                        row["reason_code"] = "MARKET_DATA_CYCLE_STALE"
                    elif spec.readiness_url and readiness.get("status") != "HEALTHY":
                        row["reason_code"] = "READINESS_UNAVAILABLE"
        if row.get("status") == "DUPLICATE_PROCESS":
            row["operational_state"] = "UNKNOWN"
        elif row.get("status") in {"FAILED", "ACTION_REQUIRED"} and row.get("operational_state") == "UNKNOWN":
            row["operational_state"] = "UNHEALTHY"
        if row["status"] == "UNAVAILABLE" and not row.get("recommended_action"):
            row["recommended_action"] = "Inspect the service heartbeat, port, and approved runtime log."
        return row

    def run(self, *, start_missing: bool) -> dict[str, Any]:
        started = self.now().isoformat()
        snapshot = self._process_snapshot()
        processes = snapshot.processes
        actions: list[Any] = []
        services: dict[str, Any] = {}
        for spec in _specs():
            services[spec.key] = self._service(spec, start_missing=start_missing, processes=processes, actions=actions, process_visibility=snapshot.visibility, process_visibility_reason=snapshot.reason)
            snapshot = self._process_snapshot()
            processes = snapshot.processes
        broker = self._broker()
        execution_boundary = self._execution_boundary()
        apis = {
            "trend_radar": self._api("http://127.0.0.1:8550/api/radar/top?limit=1", "Radar"),
            "production_scanner": self._api("http://127.0.0.1:8550/api/market-screener/health", "Production Scanner", method="GET"),
            "daily_trader": self._api("http://127.0.0.1:8550/api/daily-trader/health", "Daily Trader"),
            "pre_trade_reviewer": self._api("http://127.0.0.1:8550/api/pre-trade-review/health", "Pre-Trade Reviewer"),
        }
        safety = self._safety()
        issues = [row.get("reason_code") for row in [broker, *services.values()] if row.get("reason_code")]
        if not execution_boundary.get("safe_to_start"):
            issues.append(execution_boundary.get("reason_code", "EXECUTION_STATE_UNKNOWN"))
        issues += [row.get("reason_code") for row in apis.values() if row.get("reason_code")]
        if safety.get("status") not in {"HEALTHY", "SAFE_FOR_PAPER_EXECUTION"}:
            issues.append("SAFETY_CONFIGURATION_MISMATCH")
        if broker.get("status") == "WAITING_FOR_IBKR":
            overall = "WAITING_FOR_IBKR"
        elif not execution_boundary.get("safe_to_start"):
            overall = "ACTION_REQUIRED"
        elif self._database().get("status") != "HEALTHY":
            overall = "ACTION_REQUIRED"
        elif broker.get("status") != "HEALTHY":
            overall = "ACTION_REQUIRED"
        elif any(row.get("required") and row.get("operational_state") in {"UNHEALTHY", "UNKNOWN", "STOPPED", "STARTING"} for row in services.values()):
            overall = "ACTION_REQUIRED"
        elif any(apis[key].get("status") == "UNAVAILABLE" for key in ("trend_radar", "production_scanner")):
            overall = "ACTION_REQUIRED"
        elif any(row.get("operational_state") in {"UNHEALTHY", "UNKNOWN", "STOPPED", "STARTING"} for row in services.values()):
            overall = "PARTIAL"
        elif any(row.get("status") in {"FAILED", "UNAVAILABLE"} for row in apis.values()):
            overall = "PARTIAL"
        elif safety.get("status") not in {"HEALTHY", "SAFE_FOR_PAPER_EXECUTION"}:
            overall = "ACTION_REQUIRED"
        else:
            overall = "READY_FOR_MARKET_OPEN"
        required_service_keys = [spec.key for spec in _specs() if spec.required]
        optional_service_keys = [spec.key for spec in _specs() if not spec.required]
        return {
            "workflow": "trading_morning_startup" if start_missing else "trading_platform_health",
            "started_at": started, "completed_at": self.now().isoformat(), "overall_status": overall,
            "market_status": "PREMARKET_OR_CLOSED_ALLOWED", "database": self._database(), "broker": broker,
            "services": services, "apis": apis, "duplicates": [k for k, v in services.items() if v.get("status") == "DUPLICATE_PROCESS"],
            "readiness_dependencies": {
                "mandatory_services": required_service_keys,
                "optional_services": optional_service_keys,
                "mandatory_apis": ["trend_radar", "production_scanner"],
                "optional_apis": ["daily_trader", "pre_trade_reviewer"],
                "broker": "verified Paper IBKR account",
                "database": "authoritative SQLite database",
                "safety": "SAFE_FOR_PAPER_EXECUTION",
            },
            "safety": safety, "actions_taken": actions, "action_required": issues,
            "execution_boundary": execution_boundary,
            "orders_submitted": 0, "approvals_created": 0, "execution_intents_created": 0,
        }

    def _api(self, url: str, label: str, method: str = "GET") -> dict[str, Any]:
        result = {"status": "UNAVAILABLE", "reason": "not_checked"}
        for attempt in range(API_ATTEMPTS):
            result = _get_json(url)
            if result.get("status") != "UNAVAILABLE":
                break
            if attempt < API_ATTEMPTS - 1:
                self.sleep(0.2)
        if result.get("status") == "UNAVAILABLE":
            return {"service": label, "status": "UNAVAILABLE", "operational_state": "UNHEALTHY", "reason_code": "API_UNAVAILABLE", "reason": result.get("reason"), "attempts": API_ATTEMPTS}
        return {"service": label, "status": "HEALTHY", "operational_state": "HEALTHY", "read_only": result.get("read_only", True), "response": result}

    def _safety(self) -> dict[str, Any]:
        try:
            from src.config import SETTINGS
            paper, dry = bool(SETTINGS.paper_trading), bool(SETTINGS.dry_run_mode)
            decision = SETTINGS.decision_agent
            allow_paper = bool(getattr(decision, "allow_ibkr_paper_trading", False))
            allow_llm_live = bool(getattr(decision, "allow_live_trading", False))
            allow_legacy_live = bool(os.getenv("ALLOW_LIVE_TRADING", "false").strip().lower() in {"1", "true", "yes", "on"})
            allow_live = allow_llm_live or allow_legacy_live
            safe = paper and allow_paper and not allow_live
            status = "SAFE_FOR_PAPER_EXECUTION" if safe else "ACTION_REQUIRED"
            return {"status": status, "paper_trading": paper, "dry_run_mode": dry, "allow_ibkr_paper_trading": allow_paper, "allow_llm_live_trading": allow_llm_live, "allow_live_trading": allow_legacy_live, "live_trading_enabled": allow_live, "reason_code": None if safe else "SAFETY_CONFIGURATION_MISMATCH"}
        except Exception as exc:
            return {"status": "FAILED", "reason_code": "SAFETY_CONFIG_UNAVAILABLE", "reason": type(exc).__name__}


def _status_text(row: dict[str, Any] | None) -> str:
    if not row:
        return "UNKNOWN"
    status = str(row.get("status") or "UNKNOWN")
    operational_state = row.get("operational_state")
    if operational_state and operational_state != status:
        return f"{status} ({operational_state})"
    return status


def _issue_lines(result: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    broker = result.get("broker", {})
    if broker.get("status") not in {None, "HEALTHY"}:
        parts = ["Broker", str(broker.get("status", "UNKNOWN"))]
        if broker.get("reason_code"):
            parts.append(str(broker["reason_code"]))
        lines.append(": ".join(parts))
    for row in result.get("services", {}).values():
        if row.get("status") in {"HEALTHY", "DEGRADED"}:
            continue
        if not row.get("required") and row.get("operational_state") in {None, "HEALTHY"}:
            continue
        issue = f"{row.get('label', row.get('service', 'Service'))}: {_status_text(row)}"
        if row.get("reason_code"):
            issue += f" - {row['reason_code']}"
        heartbeat = row.get("heartbeat") if isinstance(row.get("heartbeat"), dict) else {}
        if heartbeat.get("path") and (row.get("operational_state") == "UNKNOWN" or heartbeat.get("fresh") is False):
            issue += f"; heartbeat_age_seconds={heartbeat.get('age_seconds')}; artifact={heartbeat.get('path')}"
        lines.append(issue)
    for key in ("trend_radar", "production_scanner", "daily_trader", "pre_trade_reviewer"):
        row = result.get("apis", {}).get(key, {})
        if row.get("status") == "UNAVAILABLE":
            label = row.get("service", key)
            issue = f"{label}: UNAVAILABLE"
            if row.get("reason_code"):
                issue += f" - {row['reason_code']}"
            lines.append(issue)
    safety = result.get("safety", {})
    if safety.get("status") not in {None, "HEALTHY", "SAFE_FOR_PAPER_EXECUTION"}:
        lines.append(f"Safety: {safety.get('status', 'UNKNOWN')} - {safety.get('reason_code', 'SAFETY_CONFIGURATION_MISMATCH')}")
    boundary = result.get("execution_boundary", {})
    if boundary and not boundary.get("safe_to_start", True):
        lines.append(f"Execution boundary: {boundary.get('status', 'ACTION_REQUIRED')} - {boundary.get('reason_code', 'EXECUTION_STATE_UNKNOWN')}")
    return lines


def _required_action(result: dict[str, Any]) -> str:
    broker = result.get("broker", {})
    if result.get("overall_status") == "WAITING_FOR_IBKR" or broker.get("status") == "WAITING_FOR_IBKR":
        return str(broker.get("action_required") or "Open TWS or IB Gateway and complete login/2FA, then run readiness-recheck.")
    boundary = result.get("execution_boundary", {})
    if boundary and not boundary.get("safe_to_start", True):
        return str(boundary.get("action_required") or "Escalate execution-state ambiguity to Codex before startup or recovery.")
    for row in result.get("services", {}).values():
        if row.get("recommended_action"):
            return str(row["recommended_action"])
        if row.get("action_required"):
            return str(row["action_required"])
    if result.get("overall_status") == "READY_FOR_MARKET_OPEN":
        return "None"
    if result.get("action_required"):
        return "Escalate unresolved operational conditions to Codex."
    return "None"


def human_report(result: dict[str, Any]) -> str:
    services = result.get("services", {})
    apis = result.get("apis", {})
    broker = result.get("broker", {})
    safety = result.get("safety", {})
    market = services.get("market_data", {})
    market_heartbeat = market.get("heartbeat") if isinstance(market.get("heartbeat"), dict) else {}
    other = []
    for key, label in (("execute_worker", "Execute Worker"), ("autonomous_worker", "Autonomous Worker"), ("crypto_worker", "Crypto Worker")):
        other.append(f"{label}={_status_text(services.get(key))}")
    trading_actions = (
        f"orders_submitted={result.get('orders_submitted', 0)}, "
        f"approvals_created={result.get('approvals_created', 0)}, "
        f"execution_intents_created={result.get('execution_intents_created', 0)}"
    )
    safety_bits = [
        str(safety.get("status", "UNKNOWN")),
        f"paper_trading={safety.get('paper_trading', 'UNKNOWN')}",
        f"dry_run_mode={safety.get('dry_run_mode', 'UNKNOWN')}",
        f"live_trading_enabled={safety.get('live_trading_enabled', 'UNKNOWN')}",
    ]
    if result.get("execution_boundary"):
        safety_bits.append(f"execution_boundary={result['execution_boundary'].get('status', 'UNKNOWN')}")
    broker_line = _status_text(broker)
    account = broker.get("account") if isinstance(broker.get("account"), dict) else {}
    if account.get("account_id"):
        broker_line += f"; account={account['account_id']}; environment={account.get('environment', broker.get('environment', 'UNKNOWN'))}"
    market_line = _status_text(market)
    if market_heartbeat.get("age_seconds") is not None:
        market_line += f"; freshness_age_seconds={market_heartbeat['age_seconds']}"
    if market_heartbeat.get("path"):
        market_line += f"; artifact={market_heartbeat['path']}"
    issues = _issue_lines(result)
    lines = [
        "TRADING PLATFORM - MORNING STARTUP",
        "",
        f"Overall: {result.get('overall_status', 'UNKNOWN')}",
        f"Broker: {broker_line}",
        f"Market Data: {market_line}",
        f"Scanner MCP: {_status_text(services.get('strategy_scanner_mcp'))}",
        f"Trading Bot MCP: {_status_text(services.get('trading_bot_mcp'))}",
        f"Dashboard: {_status_text(services.get('dashboard'))}",
        f"Other Services: {'; '.join(other) if other else 'None'}",
        f"Safety: {'; '.join(safety_bits)}",
        f"Trading Actions: {trading_actions}",
        "",
        "Issues:",
    ]
    lines.extend([f"- {item}" for item in issues] or ["- None"])
    lines.extend(["", "Required Action:", _required_action(result)])
    if result.get("overall_status") == "READY_FOR_MARKET_OPEN":
        lines.extend(["", "READY_FOR_MARKET_OPEN"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic local trading-platform operations")
    parser.add_argument("command", choices=("health", "morning", "readiness-recheck", "service-status", "execute-worker-preflight"))
    parser.add_argument("service", nargs="?")
    args = parser.parse_args(argv)
    if args.command == "execute-worker-preflight":
        result = Supervisor().execute_worker_preflight()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["verdict"] == "SAFE_TO_START" else 2
    result = Supervisor().run(start_missing=args.command == "morning")
    if args.command == "service-status" and args.service:
        result["services"] = {args.service: result.get("services", {}).get(args.service, {"service": args.service, "status": "MISSING", "reason_code": "UNKNOWN_SERVICE"})}
    print(json.dumps(result, indent=2, sort_keys=True))
    print(human_report(result))
    return 0 if result["overall_status"] in {"READY_FOR_MARKET_OPEN", "PARTIAL", "WAITING_FOR_IBKR"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
