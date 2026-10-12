"""Deterministic morning readiness checks for the existing Windows stack.

This module is intentionally independent of Hermes.  Hermes skills invoke the
CLI at the bottom of this file; the facts and state transitions are decided
here so a language model never interprets process output as health.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "memory" / "runtime"
MARKET_DATA_STATUS_MAX_AGE = 900.0


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


def _json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _processes() -> list[ProcessInfo]:
    """Return Python processes with command lines, without killing anything."""
    ps = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine,Name,CreationDate | "
          "ConvertTo-Json -Compress")
    try:
        raw = subprocess.check_output(["powershell", "-NoProfile", "-Command", ps], text=True, timeout=5)
        data = json.loads(raw) if raw.strip() else []
        if isinstance(data, dict):
            data = [data]
        return [ProcessInfo(int(row["ProcessId"]), str(row.get("CommandLine") or ""),
                            int(row["ParentProcessId"]) if row.get("ParentProcessId") else None,
                            str(row.get("Name") or ""), str(row.get("CreationDate") or "")) for row in data]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return []


def _specs() -> tuple[ServiceSpec, ...]:
    return (
        ServiceSpec("trading_bot_mcp", "Trading Bot MCP", ("src.mcp.trading_bot_server",), "run_trading_bot_mcp.cmd", required=True),
        ServiceSpec("strategy_scanner_mcp", "Strategy Scanner MCP", ("scanner_mcp.server",), "start-strategy-scanner-mcp.cmd", port=8766, required=True),
        ServiceSpec("dashboard", "Dashboard", ("dashboard_react.server:app",), "run_react_dashboard.cmd", port=8550),
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

    last_cycle_age = _age(payload.get("last_cycle_at"))
    latest_fresh_age = _age(payload.get("latest_fresh_cycle_at"))
    cycle_age = last_cycle_age if last_cycle_age is not None else latest_fresh_age
    readable = cycle_age is not None
    health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    quality = {key: payload.get(key, health.get(key)) for key in ("requested", "received", "fresh", "advanced", "stale", "no_data", "errors")}
    fresh = readable and cycle_age <= spec.heartbeat_max_age
    return {
        "available": readable,
        "fresh": fresh,
        "age_seconds": round(cycle_age, 2) if cycle_age is not None else None,
        "last_cycle_age_seconds": round(last_cycle_age, 2) if last_cycle_age is not None else None,
        "latest_fresh_cycle_age_seconds": round(latest_fresh_age, 2) if latest_fresh_age is not None else None,
        "path": str(spec.heartbeat),
        "payload_status": payload.get("market_data_status"),
        "data_quality": str(payload.get("market_data_status") or "UNKNOWN").upper(),
        "quality": quality,
        "status_artifact_readable": readable,
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


def _get_json(url: str, timeout: float = 15.0) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
        return value if isinstance(value, dict) else {"value": value}
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {"status": "UNAVAILABLE", "reason": type(exc).__name__}


def _start(spec: ServiceSpec) -> str | None:
    if not spec.launcher:
        return "no_approved_launcher"
    path = ROOT / spec.launcher
    if not path.exists():
        return f"launcher_missing:{path}"
    try:
        subprocess.Popen(["cmd.exe", "/d", "/c", str(path)], cwd=str(ROOT), creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        return None
    except OSError as exc:
        return f"launcher_failed:{type(exc).__name__}"


class Supervisor:
    def __init__(self, *, process_provider: Callable[[], list[ProcessInfo]] | None = None, sleep: Callable[[float], None] = time.sleep, now: Callable[[], datetime] | None = None):
        self.process_provider = process_provider or _processes
        self.sleep = sleep
        self.now = now or (lambda: datetime.now(timezone.utc))

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
        gateway = [p for p in self.process_provider() if any(x in p.command_line.lower() for x in ("ibgateway", "tws", "jts.exe"))]
        result["gateway_running"] = bool(gateway)
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
            from src.brokers.ibkr import IBKRClient
            # The existing adapter owns its authoritative SETTINGS.broker
            # configuration; do not create a second broker configuration path.
            client = IBKRClient()
            client.connect()
            identity = client.get_account_identity()
            client.disconnect()
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
        except Exception as exc:
            result.update(status="WAITING_FOR_IBKR", reason_code="IBKR_LOGIN_REQUIRED", reason=type(exc).__name__, action_required="Open TWS or IB Gateway and complete login/2FA, then run readiness-recheck.")
        return result

    def _service(self, spec: ServiceSpec, *, start_missing: bool, processes: list[ProcessInfo], actions: list[str]) -> dict[str, Any]:
        matches = _matching(spec, processes)
        roots = _logical_roots(matches, processes)
        row: dict[str, Any] = {
            "service": spec.key, "label": spec.label, "required": spec.required,
            "launcher": spec.launcher, "logical_instances": len(roots),
            "observed_count": len(matches), "pids": [p.pid for p in matches],
            "root_pids": [p.pid for p in roots], "process_running": bool(matches),
            "heartbeat_age_seconds": None, "api_status": None, "action_taken": None,
            "reason_code": None, "safe_auto_repair": bool(spec.launcher),
            "recommended_action": None,
        }
        if len(roots) > 1:
            row.update(status="DUPLICATE_PROCESS", reason_code="DUPLICATE_PROCESS_DETECTED", safe_auto_repair=False,
                        recommended_action="Stop one root only after confirming no active execution state.")
            return row
        if not roots and start_missing:
            if spec.key == "execute_worker":
                boundary = self._execution_boundary()
                if not boundary.get("safe_to_start"):
                    row.update(status="ACTION_REQUIRED", reason_code=boundary.get("reason_code"), action_required=boundary.get("action_required"), recommended_action=boundary.get("action_required"), safe_auto_repair=False, execution_boundary=boundary)
                    return row
            # The aggregate dashboard launcher is safe only when no worker is already present.
            if spec.key == "dashboard" and any(_matching(other, processes) for other in _specs() if other.key != "dashboard"):
                row.update(status="ACTION_REQUIRED", reason_code="DASHBOARD_LAUNCHER_WOULD_COLLIDE", action_required="Dashboard is down while worker services exist; start dashboard through the approved launcher after resolving the partial stack.", recommended_action="Resolve the partial aggregate-launcher state before starting Dashboard.", safe_auto_repair=False)
                return row
            error = _start(spec)
            if error:
                row.update(status="FAILED", reason_code="START_FAILED", reason=error, recommended_action="Inspect the approved launcher and its runtime log.")
            else:
                actions.append(f"Started {spec.label} via {spec.launcher}")
                row["status"] = "STARTED"
                self.sleep(0.25)
                refreshed_all = self.process_provider()
                refreshed = _matching(spec, refreshed_all)
                refreshed_roots = _logical_roots(refreshed, refreshed_all)
                row.update(observed_count=len(refreshed), logical_instances=len(refreshed_roots), root_pids=[p.pid for p in refreshed_roots], pids=[p.pid for p in refreshed], process_running=bool(refreshed))
                if not refreshed_roots:
                    # One bounded retry is allowed only while no logical instance
                    # exists; never retry an ambiguous or live execution state.
                    self.sleep(0.25)
                    retry_error = _start(spec)
                    actions.append(f"Retried {spec.label} via {spec.launcher}")
                    if retry_error:
                        row.update(status="ACTION_REQUIRED", reason_code="START_RETRY_FAILED", reason=retry_error, recommended_action="Inspect the approved launcher and its runtime log.")
                    else:
                        refreshed_all = self.process_provider()
                        refreshed = _matching(spec, refreshed_all)
                        refreshed_roots = _logical_roots(refreshed, refreshed_all)
                        row.update(observed_count=len(refreshed), logical_instances=len(refreshed_roots), root_pids=[p.pid for p in refreshed_roots], pids=[p.pid for p in refreshed], process_running=bool(refreshed))
        hb = _market_data_heartbeat(spec) if spec.key == "market_data" else _heartbeat(spec)
        row["heartbeat"] = hb
        row["heartbeat_age_seconds"] = hb.get("age_seconds")
        row["port"] = _port(spec.port)
        if row["port"].get("checked") and not row["port"].get("reachable"):
            # A launcher can report its PID before the HTTP server has bound.
            # Recheck briefly before classifying the start as unavailable.
            for _ in range(2):
                self.sleep(0.25)
                row["port"] = _port(spec.port)
                if row["port"].get("reachable"):
                    break
        row["api_status"] = None
        row["action_taken"] = actions[-1] if actions and spec.label in actions[-1] else None
        healthy_hb = (
            hb.get("available") and hb.get("fresh")
            if spec.key == "market_data"
            else not hb.get("available") or hb.get("fresh")
        )
        healthy_port = not row["port"].get("checked") or row["port"].get("reachable")
        if row.get("status") not in {"FAILED", "ACTION_REQUIRED", "DUPLICATE_PROCESS"}:
            if not row["process_running"]:
                row["status"] = "ACTION_REQUIRED" if start_missing else "MISSING"
                row["reason_code"] = "START_VERIFICATION_FAILED" if start_missing else "SERVICE_MISSING"
                row["recommended_action"] = "Inspect the approved launcher and its runtime log." if start_missing else "Run morning startup to invoke the approved launcher."
            else:
                if healthy_hb and healthy_port:
                    row["status"] = "DEGRADED" if spec.key == "market_data" and hb.get("data_quality") == "DEGRADED" else "HEALTHY"
                else:
                    row["status"] = "UNAVAILABLE"
                    if spec.key == "market_data" and hb.get("reason_code"):
                        row["reason_code"] = hb["reason_code"]
        if row["status"] == "UNAVAILABLE" and not row.get("recommended_action"):
            row["recommended_action"] = "Inspect the service heartbeat, port, and approved runtime log."
        return row

    def run(self, *, start_missing: bool) -> dict[str, Any]:
        started = self.now().isoformat()
        processes = self.process_provider()
        actions: list[str] = []
        services: dict[str, Any] = {}
        for spec in _specs():
            services[spec.key] = self._service(spec, start_missing=start_missing, processes=processes, actions=actions)
            processes = self.process_provider()
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
        elif any(row.get("status") in {"MISSING", "DUPLICATE_PROCESS", "ACTION_REQUIRED", "FAILED", "UNAVAILABLE"} for row in services.values()):
            overall = "ACTION_REQUIRED"
        elif any(apis[key].get("status") == "UNAVAILABLE" for key in ("trend_radar", "production_scanner")):
            overall = "ACTION_REQUIRED"
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
        for attempt in range(3):
            result = _get_json(url)
            if result.get("status") != "UNAVAILABLE":
                break
            if attempt < 2:
                self.sleep(0.2)
        if result.get("status") == "UNAVAILABLE":
            return {"service": label, "status": "UNAVAILABLE", "reason_code": "API_UNAVAILABLE", "reason": result.get("reason"), "attempts": 3}
        return {"service": label, "status": "HEALTHY", "read_only": result.get("read_only", True), "response": result}

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


def human_report(result: dict[str, Any]) -> str:
    lines = ["TRADING PLATFORM MORNING STARTUP", f"Overall: {result['overall_status']}"]
    lines.append(f"Database            {result.get('database', {}).get('status', 'UNKNOWN')}")
    lines.append(f"IB Gateway          {result.get('broker', {}).get('status', 'UNKNOWN')}")
    for key, label in (("dashboard", "Dashboard"), ("market_data", "Market Data"), ("execute_worker", "Execute Worker"), ("autonomous_worker", "Autonomous Worker"), ("crypto_worker", "Crypto Worker")):
        lines.append(f"{label:<20}{result['services'].get(key, {}).get('status', 'UNKNOWN')}")
    for key, label in (("trend_radar", "Trend Radar"), ("production_scanner", "Production Scanner"), ("daily_trader", "Daily Trader"), ("pre_trade_reviewer", "Pre-Trade Reviewer")):
        lines.append(f"{label:<20}{result['apis'].get(key, {}).get('status', 'UNKNOWN')}")
    lines += [f"Duplicate Workers   {', '.join(result.get('duplicates') or ['NONE'])}", "Actions taken:"]
    lines += [f"- {item}" for item in result.get("actions_taken", [])] or ["- None"]
    if result.get("action_required"):
        lines.append("Action required:")
        lines.extend(f"- {item}" for item in result["action_required"])
    safety = result.get("safety", {})
    lines += [f"Safety: Paper mode: {'enabled' if safety.get('paper_trading') else 'disabled'}; Live trading: {'enabled' if safety.get('live_trading_enabled') else 'disabled'}", "Orders submitted by startup: 0", "Approvals created by startup: 0", "Execution intents created by startup: 0"]
    if result["overall_status"] == "WAITING_FOR_IBKR":
        lines.append("User action: Open TWS/IB Gateway, complete login/2FA, then run readiness-recheck.")
    if result["overall_status"] == "READY_FOR_MARKET_OPEN":
        lines.append("READY FOR MARKET OPEN")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic local trading-platform operations")
    parser.add_argument("command", choices=("health", "morning", "readiness-recheck", "service-status"))
    parser.add_argument("service", nargs="?")
    args = parser.parse_args(argv)
    result = Supervisor().run(start_missing=args.command == "morning")
    if args.command == "service-status" and args.service:
        result["services"] = {args.service: result.get("services", {}).get(args.service, {"service": args.service, "status": "MISSING", "reason_code": "UNKNOWN_SERVICE"})}
    print(json.dumps(result, indent=2, sort_keys=True))
    print(human_report(result))
    return 0 if result["overall_status"] in {"READY_FOR_MARKET_OPEN", "PARTIAL", "WAITING_FOR_IBKR"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
