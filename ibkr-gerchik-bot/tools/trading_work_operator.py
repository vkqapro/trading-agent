"""Loopback-only workflow client for an interactive ChatGPT Work operator.

The client does not call a model, construct orders, create approvals, or start
services.  It provides the deterministic local side of a Work session: inspect
health, submit one explicitly authorized market scan, read its persisted payload,
and publish a GPT-authored review through the existing AI-only endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:8550"
REQUEST_TIMEOUT_SECONDS = 15.0
POLL_INTERVAL_SECONDS = 2.0
READINESS_REVALIDATION_ATTEMPTS = 3
READINESS_REVALIDATION_DELAY_SECONDS = 0.25
MARKET_SCAN_TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED"}
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ANALYTICS_RECOVERY_KEYS = ("dashboard", "strategy_scanner_mcp", "market_data")
REQUIRED_REVIEW_FIELDS = (
    "proposal_id",
    "source_scan_id",
    "symbol",
    "strategy",
    "ai_reasoning",
    "strengths",
    "weaknesses",
    "risk_observations",
    "review_result",
    "review_priority",
    "model_provider",
    "review_timestamp",
)


class OperatorError(RuntimeError):
    """A bounded, user-reportable operator failure."""


@dataclass(frozen=True)
class AnalyticsRecoverySpec:
    """One explicitly non-execution service the operator may start when authorized."""

    key: str
    label: str
    launcher: str
    readiness_url: str | None
    required_for_new_scan: bool
    detached: bool


RECOVERY_SPECS = {
    "dashboard": AnalyticsRecoverySpec(
        "dashboard", "Dashboard API", "run_dashboard_api.cmd",
        "http://127.0.0.1:8550/api/services", True, True,
    ),
    "strategy_scanner_mcp": AnalyticsRecoverySpec(
        "strategy_scanner_mcp", "Strategy Scanner MCP", "start-strategy-scanner-mcp.cmd",
        "http://127.0.0.1:8766/health", True, True,
    ),
    "market_data": AnalyticsRecoverySpec(
        "market_data", "Market Data Collector", "run_market_data_collector.cmd",
        None, True, False,
    ),
}


def _base_url(value: str | None = None) -> str:
    base_url = str(value or os.getenv("TRADING_WORK_OPERATOR_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Trading Work operator must use an HTTP loopback URL")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("TRADING_WORK_OPERATOR_BASE_URL must contain only a loopback origin")
    return base_url


def _assert_safe_result(result: Mapping[str, Any]) -> None:
    for field in ("orders_submitted", "approvals_created", "execution_intents_created"):
        if field in result and result.get(field) != 0:
            raise OperatorError(f"Safety contract failed: {field} must be zero")


def _configured_ai_token() -> str:
    # Importing config is the approved way to load the ignored local .env file.
    from src import config as _config  # noqa: F401

    token = str(os.getenv("AI_TRADE_DESK_AI_TOKEN") or "").strip()
    if not token:
        raise OperatorError("AI Trade Desk ingestion token is not configured")
    return token


class TradingWorkOperator:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        requester: Callable[..., Mapping[str, Any]] | None = None,
        operations_probe: Callable[[], Mapping[str, Any]] | None = None,
        environment_probe: Callable[[], Mapping[str, Any]] | None = None,
        recovery_launcher: Callable[[AnalyticsRecoverySpec], Mapping[str, Any]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.base_url = _base_url(base_url)
        self._requester = requester
        self._operations_probe = operations_probe
        self._environment_probe = environment_probe
        self._recovery_launcher = recovery_launcher
        self._sleep = sleep

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
        role: str | None = None,
        actor: str = "chatgpt-work-operator",
        ai_token: str | None = None,
    ) -> dict[str, Any]:
        if self._requester is not None:
            result = self._requester(path, method=method, payload=payload, role=role, actor=actor, ai_token=ai_token)
            if not isinstance(result, Mapping):
                raise OperatorError("Trading Work API returned an invalid response object")
            result = dict(result)
            _assert_safe_result(result)
            return result

        headers = {"Accept": "application/json"}
        if role:
            headers["X-AI-Trade-Desk-Role"] = role
            headers["X-AI-Trade-Desk-Actor"] = actor
        if ai_token:
            headers["X-AI-Trade-Desk-Token"] = ai_token
        body = None
        if payload is not None:
            body = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            with urlopen(Request(f"{self.base_url}{path}", data=body, headers=headers, method=method), timeout=REQUEST_TIMEOUT_SECONDS) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raise OperatorError(f"Trading Work API returned HTTP {exc.code} for {method} {path}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise OperatorError("Trading Work API is unavailable") from exc
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OperatorError("Trading Work API returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise OperatorError("Trading Work API returned an invalid response object")
        _assert_safe_result(result)
        return result

    @staticmethod
    def _probe_error(exc: Exception) -> tuple[str, bool]:
        """Return a safe reason and whether a bounded retry is appropriate."""
        message = re.sub(
            r"(?i)(token|authorization|password|secret)(\s*[:=]\s*)\S+",
            r"\1\2<redacted>",
            str(exc),
        )[:240]
        lowered = message.lower()
        if "http 401" in lowered or "http 403" in lowered or "http 404" in lowered:
            return (message, False)
        if "http 408" in lowered or "http 429" in lowered or any(f"http {code}" in lowered for code in (500, 502, 503, 504)):
            return (message, True)
        if any(token in lowered for token in ("timed out", "timeout", "temporar", "connection reset", "database is locked", "database busy")):
            return (message, True)
        if "api unavailable" in lowered or "api is unavailable" in lowered:
            return (message, True)
        return (message, False)

    def _probe(self, path: str) -> dict[str, Any]:
        """Return one independent endpoint result with bounded diagnostics."""
        checked_at = datetime.now(timezone.utc).isoformat()
        started = time.perf_counter()
        try:
            response = self._request(path)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            return {
                "status": "HEALTHY",
                "response": response,
                "endpoint": f"{self.base_url}{path}",
                "checked_at": checked_at,
                "response_time_ms": elapsed_ms,
                "transient": False,
            }
        except OperatorError as exc:
            reason, transient = self._probe_error(exc)
            return {
                "status": "UNAVAILABLE",
                "reason_code": "API_UNAVAILABLE",
                "reason": reason,
                "endpoint": f"{self.base_url}{path}",
                "checked_at": checked_at,
                "response_time_ms": round((time.perf_counter() - started) * 1000, 2),
                "transient": transient,
            }

    @staticmethod
    def _operations_probe_default() -> Mapping[str, Any]:
        from ops.trading_platform_ops import Supervisor

        return Supervisor().run(start_missing=False)

    @staticmethod
    def _environment_probe_default() -> Mapping[str, Any]:
        """Collect only local execution evidence; never infer remote loopback access."""
        process_visibility = {"status": "UNKNOWN", "reason": None}
        try:
            from ops.trading_platform_ops import _process_snapshot

            snapshot = _process_snapshot()
            process_visibility = {"status": snapshot.visibility, "reason": snapshot.reason}
        except Exception as exc:  # The operator must report a bounded diagnostic result.
            process_visibility = {"status": "unavailable", "reason": type(exc).__name__}
        return {
            "host_os": platform.system(),
            "host_name": socket.gethostname(),
            "repository_path": str(ROOT),
            "repository_access": (ROOT / ".git").exists(),
            "local_process_visibility": process_visibility,
            "loopback_origin": DEFAULT_BASE_URL,
            "local_host": (
                "LOCAL_WINDOWS_HOST"
                if platform.system() == "Windows" and (ROOT / ".git").exists() and process_visibility["status"] == "available"
                else "LOCAL_HOST_UNVERIFIED"
            ),
        }

    @staticmethod
    def _service_row(operations: Mapping[str, Any], key: str) -> dict[str, Any]:
        services = operations.get("services") if isinstance(operations.get("services"), Mapping) else {}
        value = services.get(key) if isinstance(services, Mapping) else None
        return dict(value) if isinstance(value, Mapping) else {
            "status": "UNKNOWN", "operational_state": "UNKNOWN", "reason_code": "SERVICE_STATUS_UNAVAILABLE"
        }

    @staticmethod
    def _dashboard_api_row(probe: Mapping[str, Any], *, required: bool = True) -> dict[str, Any]:
        response = probe.get("response") if isinstance(probe.get("response"), Mapping) else {}
        return {
            "required_for_new_scan": required,
            "required_for_order_execution": False,
            "status": probe.get("status"),
            "reason_code": probe.get("reason_code"),
            "service_status": response,
            "endpoint": probe.get("endpoint"),
            "checked_at": probe.get("checked_at"),
            "response_time_ms": probe.get("response_time_ms"),
            "transient": probe.get("transient", False),
            "underlying_error": probe.get("reason"),
        }

    @classmethod
    def _daily_trader_api_row(cls, probe: Mapping[str, Any]) -> dict[str, Any]:
        row = cls._dashboard_api_row(probe)
        response = row.get("service_status") if isinstance(row.get("service_status"), Mapping) else {}
        payload_status = str(response.get("status") or "").strip().lower()
        if row.get("status") == "HEALTHY" and payload_status not in {"", "ok", "healthy"}:
            row.update(status="NOT_READY", reason_code="DAILY_TRADER_NOT_READY", transient=False)
        return row

    @staticmethod
    def _analytics_service_row(row: Mapping[str, Any], *, required: bool) -> dict[str, Any]:
        """Keep supervisor lifecycle labels while using its operational verdict for scan readiness."""
        value = dict(row)
        raw_status = str(value.get("status") or "UNKNOWN").upper()
        operational_state = str(value.get("operational_state") or "UNKNOWN").upper()
        heartbeat = value.get("heartbeat") if isinstance(value.get("heartbeat"), Mapping) else {}
        if operational_state == "HEALTHY":
            status = "HEALTHY"
        else:
            status = raw_status
        freshness = "UNAVAILABLE"
        if heartbeat.get("available"):
            if heartbeat.get("fresh") is False:
                freshness = "STALE"
            elif heartbeat.get("data_quality") == "INCOMPLETE":
                freshness = "INCOMPLETE"
            elif heartbeat.get("session_state") == "POST_SESSION":
                freshness = "AFTER_HOURS_FRESH_HEARTBEAT"
            elif heartbeat.get("fresh") is True:
                freshness = "FRESH_AND_USABLE"
            else:
                freshness = "COVERAGE_UNVERIFIED"
        return {
            **value,
            "status": status,
            "supervisor_status": raw_status,
            "required_for_new_scan": required,
            "required_for_order_execution": True,
            "data_freshness": freshness,
            "coverage_warnings": value.get("coverage_warnings") or heartbeat.get("quality") or {},
        }

    @staticmethod
    def _service_diagnosis(row: Mapping[str, Any]) -> str:
        """Classify a service without treating absent evidence as an empty healthy state."""
        if row.get("port_conflict"):
            return "PORT_CONFLICT"
        if str(row.get("reason_code") or "").upper() in {"PORT_OWNERSHIP_AMBIGUOUS", "DUPLICATE_PROCESS_DETECTED"}:
            return "PORT_CONFLICT"
        if str(row.get("process_visibility", {}).get("status") if isinstance(row.get("process_visibility"), Mapping) else "").lower() == "unavailable":
            return "UNKNOWN"
        if row.get("process_running") and str(row.get("operational_state") or "").upper() != "HEALTHY":
            return "RUNNING_UNHEALTHY"
        if not row.get("process_running") and str(row.get("operational_state") or "").upper() in {"STOPPED", "UNKNOWN"}:
            return "NOT_RUNNING" if row.get("reason_code") != "PORT_OWNERSHIP_AMBIGUOUS" else "PORT_CONFLICT"
        return "HEALTHY" if str(row.get("operational_state") or "").upper() == "HEALTHY" else "UNKNOWN"

    @staticmethod
    def _ai_token_readiness() -> dict[str, Any]:
        try:
            _configured_ai_token()
            return {"status": "CONFIGURED"}
        except OperatorError as exc:
            return {"status": "MISSING", "reason_code": "AI_TOKEN_NOT_CONFIGURED", "reason": str(exc)}
        except Exception as exc:
            return {"status": "UNVERIFIABLE", "reason_code": "AI_TOKEN_READINESS_UNVERIFIABLE", "reason": type(exc).__name__}

    def preflight(self, *, operations_probe: Callable[[], Mapping[str, Any]] | None = None) -> dict[str, Any]:
        """Report all analytics dependencies even when the dashboard is unavailable."""
        environment = dict((self._environment_probe or self._environment_probe_default)())
        try:
            selected_operations_probe = operations_probe or self._operations_probe or self._operations_probe_default
            operations = dict(selected_operations_probe())
            _assert_safe_result(operations)
        except Exception as exc:
            operations = {
                "overall_status": "UNAVAILABLE", "reason_code": "OPERATIONS_PROBE_UNAVAILABLE",
                "reason": type(exc).__name__, "services": {}, "apis": {},
                "broker": {"status": "UNKNOWN", "reason_code": "BROKER_STATUS_UNAVAILABLE"},
            }

        services_probe = self._probe("/api/services")
        scans_probe = self._probe("/api/ai-trade-desk/scans?limit=20")
        proposals_probe = self._probe("/api/ai-trade-desk/proposals")
        radar_probe = self._probe("/api/radar/top?limit=1")
        scanner_probe = self._probe("/api/market-screener/health")
        daily_probe = self._probe("/api/daily-trader/health")

        scans_response = scans_probe.get("response") if isinstance(scans_probe.get("response"), Mapping) else {}
        proposals_response = proposals_probe.get("response") if isinstance(proposals_probe.get("response"), Mapping) else {}
        active_scans = [
            run for run in scans_response.get("runs") or []
            if isinstance(run, Mapping) and str(run.get("status") or "").upper() in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}
        ]
        broker = dict(operations.get("broker") or {})
        market_data = self._service_row(operations, "market_data")
        scanner_mcp = self._service_row(operations, "strategy_scanner_mcp")
        bot_mcp = self._service_row(operations, "trading_bot_mcp")
        dashboard_service = self._service_row(operations, "dashboard")
        execution_worker = self._service_row(operations, "execute_worker")
        dashboard_dependency = self._dashboard_api_row(services_probe)
        dashboard_dependency.update({
            "ownership_process_running": dashboard_service.get("process_running"),
            "ownership_reason_code": dashboard_service.get("reason_code"),
            "ownership_logical_instances": dashboard_service.get("logical_instances"),
            "service_diagnosis": self._service_diagnosis(dashboard_service),
        })
        ai_token = self._ai_token_readiness()

        dependencies = {
            "dashboard_api": dashboard_dependency,
            "ibkr_paper_gateway": {
                "required_for_new_scan": True,
                "required_for_order_execution": True,
                "status": broker.get("status", "UNKNOWN"),
                "reason_code": broker.get("reason_code"),
                "account_identity_verified": bool(broker.get("account_identity_verified")),
            },
            "market_data": {**self._analytics_service_row(market_data, required=True), "service_diagnosis": self._service_diagnosis(market_data)},
            "trend_radar": self._dashboard_api_row(radar_probe),
            "strategy_scanner_mcp": {**scanner_mcp, "required_for_new_scan": True, "required_for_order_execution": False, "service_diagnosis": self._service_diagnosis(scanner_mcp)},
            "trading_bot_mcp": {**bot_mcp, "required_for_new_scan": False, "required_for_order_execution": False, "service_diagnosis": self._service_diagnosis(bot_mcp)},
            "production_scanner": self._dashboard_api_row(scanner_probe),
            "daily_trader": self._daily_trader_api_row(daily_probe),
            "ai_trade_desk_api": {
                "required_for_new_scan": True,
                "required_for_order_execution": False,
                "status": "HEALTHY" if scans_probe.get("status") == "HEALTHY" and proposals_probe.get("status") == "HEALTHY" else "UNAVAILABLE",
                "reason_code": scans_probe.get("reason_code") or proposals_probe.get("reason_code"),
            },
            "ai_authentication": {
                "required_for_new_scan": False,
                "required_for_order_execution": False,
                **ai_token,
            },
            "execution_worker": {**execution_worker, "required_for_new_scan": False, "required_for_order_execution": True, "informational_only": True},
        }
        blocked = [
            key for key, row in dependencies.items()
            if row.get("required_for_new_scan")
            and str(row.get("status") or "").upper() not in {"HEALTHY", "CONFIGURED"}
        ]
        analytics_scan_readiness = "READY" if not blocked else "BLOCKED"
        safety = operations.get("safety") if isinstance(operations.get("safety"), Mapping) else {}
        execution_boundary = operations.get("execution_boundary") if isinstance(operations.get("execution_boundary"), Mapping) else {}
        broker_execution_readiness = {
            "status": "READY" if broker.get("status") == "HEALTHY" and safety.get("status") == "SAFE_FOR_PAPER_EXECUTION" and execution_boundary.get("safe_to_start") is True else "NOT_READY",
            "broker_status": broker.get("status", "UNKNOWN"),
            "safety_status": safety.get("status", "UNKNOWN"),
            "execution_boundary_status": execution_boundary.get("status", "UNKNOWN"),
            "safety_rule": "PAPER_TRADING=true, ALLOW_IBKR_PAPER_TRADING=true, and all live-trading flags disabled",
        }
        if active_scans:
            status = "SCAN_ALREADY_ACTIVE"
        elif environment.get("local_host") != "LOCAL_WINDOWS_HOST":
            status = "LOCAL_HOST_UNVERIFIED"
        elif blocked:
            status = "BLOCKED"
        else:
            status = "READY_FOR_OPERATOR_REVIEW"
        persisted_mode = (
            "PERSISTED_ANALYSIS_ONLY"
            if broker.get("status") == "WAITING_FOR_IBKR"
            and str(market_data.get("operational_state") or "").upper() == "HEALTHY"
            and bool((market_data.get("heartbeat") or {}).get("fresh"))
            and daily_probe.get("status") == "HEALTHY"
            else "NOT_AVAILABLE"
        )
        return {
            "status": status,
            "execution_environment": environment,
            "dependencies": dependencies,
            "blocked_dependencies": blocked,
            "analytics_scan_readiness": {"status": analytics_scan_readiness, "blocked_dependencies": blocked},
            "broker_execution_readiness": broker_execution_readiness,
            "persisted_data_mode": persisted_mode,
            "dashboard": {"reachable": services_probe.get("status") == "HEALTHY", "service_status": services_probe.get("response")},
            "operations": operations,
            "trade_desk": {
                "active_scan_ids": [str(run.get("scan_id")) for run in active_scans] if scans_probe.get("status") == "HEALTHY" else None,
                "active_scan_status": "VERIFIED" if scans_probe.get("status") == "HEALTHY" else "UNVERIFIED",
                "proposal_count": len(proposals_response.get("proposals") or []) if proposals_probe.get("status") == "HEALTHY" else None,
                "proposal_status": "VERIFIED" if proposals_probe.get("status") == "HEALTHY" else "UNVERIFIED",
                "ai_ingestion_auth": dependencies["ai_authentication"]["status"],
            },
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
        }

    @staticmethod
    def _ai_token_configured() -> bool:
        try:
            _configured_ai_token()
        except OperatorError:
            return False
        return True

    @staticmethod
    def _launcher_is_approved(spec: AnalyticsRecoverySpec) -> tuple[bool, str | None]:
        """Verify the launcher is a documented isolated analytics launcher before use."""
        path = ROOT / spec.launcher
        if not path.is_file():
            return False, "APPROVED_LAUNCHER_MISSING"
        try:
            source = path.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            return False, "APPROVED_LAUNCHER_UNREADABLE"
        if "run_react_dashboard.cmd" in source:
            return False, "BROAD_LAUNCHER_REFERENCE_DETECTED"
        required_markers = {
            "dashboard": ("dashboard_react.server:app", "--host', '127.0.0.1"),
            "strategy_scanner_mcp": ("scanner_mcp.server", "127.0.0.1"),
            "market_data": ("--job market_data", "--client-id 17"),
        }[spec.key]
        if not all(marker in source for marker in required_markers):
            return False, "APPROVED_LAUNCHER_CONTRACT_MISMATCH"
        return True, None

    @staticmethod
    def _safe_to_recover_service(key: str, preflight: Mapping[str, Any]) -> tuple[bool, str | None]:
        environment = preflight.get("execution_environment") if isinstance(preflight.get("execution_environment"), Mapping) else {}
        if environment.get("local_host") != "LOCAL_WINDOWS_HOST":
            return False, "LOCAL_HOST_UNVERIFIED"
        dependencies = preflight.get("dependencies") if isinstance(preflight.get("dependencies"), Mapping) else {}
        dependency_key = "dashboard_api" if key == "dashboard" else key
        row = dependencies.get(dependency_key) if isinstance(dependencies, Mapping) else None
        if not isinstance(row, Mapping):
            return False, "SERVICE_STATUS_UNAVAILABLE"
        if str(row.get("status") or "").upper() in {"HEALTHY", "CONFIGURED"}:
            return False, "ALREADY_HEALTHY"
        if row.get("reason_code") in {"DUPLICATE_PROCESS_DETECTED", "PORT_OWNERSHIP_AMBIGUOUS"} or row.get("ownership_reason_code") in {"DUPLICATE_PROCESS_DETECTED", "PORT_OWNERSHIP_AMBIGUOUS"}:
            return False, "OWNERSHIP_AMBIGUOUS"
        if row.get("ownership_process_running") is True:
            return False, "EXISTING_PROCESS_NOT_READY"
        if key == "market_data":
            broker = dependencies.get("ibkr_paper_gateway") if isinstance(dependencies, Mapping) else {}
            if not isinstance(broker, Mapping) or str(broker.get("status") or "").upper() != "HEALTHY":
                return False, "IBKR_LOGIN_REQUIRED"
            heartbeat = row.get("heartbeat") if isinstance(row.get("heartbeat"), Mapping) else {}
            lock = row.get("lock") if isinstance(row.get("lock"), Mapping) else {}
            if (
                row.get("reason_code") == "LOCK_OWNER_MISMATCH"
                or heartbeat.get("lock_owner_matches_process") is False
                or lock.get("owner_matches_process_tree") is False
            ):
                return False, "MARKET_DATA_OWNERSHIP_MISMATCH"
        return True, None

    def _launch_recovery_service(self, spec: AnalyticsRecoverySpec) -> dict[str, Any]:
        if self._recovery_launcher is not None:
            result = self._recovery_launcher(spec)
            return dict(result) if isinstance(result, Mapping) else {"ok": False, "reason_code": "INVALID_LAUNCHER_RESULT"}
        try:
            if spec.detached:
                completed = subprocess.run(
                    ["cmd.exe", "/d", "/c", spec.launcher], cwd=str(ROOT), text=True,
                    capture_output=True, timeout=20,
                )
                return {"ok": completed.returncode == 0, "exit_code": completed.returncode}
            subprocess.Popen(
                ["cmd.exe", "/d", "/c", spec.launcher], cwd=str(ROOT),
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return {"ok": True}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "reason_code": f"LAUNCHER_FAILED:{type(exc).__name__}"}

    def recover_analytics(self, *, authorized: bool) -> dict[str, Any]:
        """Start only missing, verified analytics services. Never stops or restarts anything."""
        if not authorized:
            raise OperatorError("Analytics recovery requires explicit --authorize-safe-start authorization")
        before = self.preflight()
        actions: list[dict[str, Any]] = []
        for key in ANALYTICS_RECOVERY_KEYS:
            spec = RECOVERY_SPECS[key]
            eligible, reason = self._safe_to_recover_service(key, before)
            if not eligible:
                actions.append({"service": key, "action": "NOT_STARTED", "reason_code": reason})
                continue
            approved, launcher_reason = self._launcher_is_approved(spec)
            if not approved:
                actions.append({"service": key, "action": "NOT_STARTED", "reason_code": launcher_reason})
                continue
            launch = self._launch_recovery_service(spec)
            actions.append({"service": key, "action": "START_REQUESTED" if launch.get("ok") else "NOT_STARTED", **launch})
        # A recovery command is a start-only operation. Its second preflight proves readiness or preserves blockers.
        after = self.preflight()
        return {
            "status": after.get("status"),
            "recovery_actions": actions,
            "preflight_before": before,
            "preflight_after": after,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
        }

    def submit_full_market_scan(self, *, authorized: bool) -> dict[str, Any]:
        if not authorized:
            raise OperatorError("Full market scan requires explicit per-run user authorization")
        readiness = self.preflight()
        attempts = [readiness]
        for attempt in range(1, READINESS_REVALIDATION_ATTEMPTS):
            daily = (readiness.get("dependencies") or {}).get("daily_trader")
            transient = isinstance(daily, Mapping) and daily.get("transient") is True
            if readiness.get("status") == "READY_FOR_OPERATOR_REVIEW" or not transient:
                break
            self._sleep(READINESS_REVALIDATION_DELAY_SECONDS)
            readiness = self.preflight()
            attempts.append(readiness)
        if readiness.get("status") != "READY_FOR_OPERATOR_REVIEW":
            blockers = ", ".join(readiness.get("blocked_dependencies") or []) or str(readiness.get("status"))
            daily = (readiness.get("dependencies") or {}).get("daily_trader") or {}
            diagnostic = {
                "attempts": len(attempts),
                "status": daily.get("status"),
                "reason_code": daily.get("reason_code"),
                "checked_at": daily.get("checked_at"),
                "endpoint": daily.get("endpoint"),
                "response_time_ms": daily.get("response_time_ms"),
                "transient": daily.get("transient", False),
                "underlying_error": daily.get("underlying_error"),
            }
            raise OperatorError(
                f"Full market scan is blocked until analytics preflight is ready: {blockers}; "
                f"daily_trader_diagnostic={json.dumps(diagnostic, sort_keys=True)}"
            )
        active = self._request("/api/ai-trade-desk/scans?limit=20").get("runs") or []
        if any(isinstance(run, Mapping) and str(run.get("status") or "").upper() in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"} for run in active):
            raise OperatorError("Full market scan is blocked because an active scan appeared during final revalidation")
        return self._request("/api/ai-trade-desk/scans/full-market", method="POST", payload={}, role="human")

    def locate_gpt_review_job(self, market_scan_id: str) -> dict[str, Any] | None:
        parent_scan_id = str(market_scan_id or "").strip()
        if not parent_scan_id:
            raise ValueError("market_scan_id is required")
        runs = self._request("/api/ai-trade-desk/scans?limit=100").get("runs") or []
        matches = [
            dict(run) for run in runs
            if isinstance(run, Mapping)
            and str(run.get("job_type") or "").upper() == "GPT_REVIEW"
            and str(run.get("parent_scan_id") or "") == parent_scan_id
        ]
        return matches[0] if matches else None

    def ensure_gpt_review_job(self, market_scan_id: str, *, authorized: bool) -> dict[str, Any]:
        existing = self.locate_gpt_review_job(market_scan_id)
        if existing is not None:
            return existing
        if not authorized:
            raise OperatorError("GPT review job creation requires explicit user authorization")
        parent_scan_id = str(market_scan_id or "").strip()
        if not parent_scan_id:
            raise ValueError("market_scan_id is required")
        return self._request(
            "/api/ai-trade-desk/scans/review",
            method="POST",
            payload={"parent_scan_id": parent_scan_id},
            role="human",
        )

    def scan_status(self, scan_id: str) -> dict[str, Any]:
        normalized = quote(str(scan_id or "").strip(), safe="")
        if not normalized:
            raise ValueError("scan_id is required")
        return self._request(f"/api/ai-trade-desk/scans/{normalized}")

    def poll_market_scan(self, scan_id: str, *, max_wait_seconds: float = 300.0) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.0, max_wait_seconds)
        current = self.scan_status(scan_id)
        while str(current.get("status") or "").upper() not in MARKET_SCAN_TERMINAL_STATES and time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL_SECONDS)
            current = self.scan_status(scan_id)
        if str(current.get("status") or "").upper() not in MARKET_SCAN_TERMINAL_STATES:
            current["polling_exhausted"] = True
        return current

    def review_payload(self, scan_id: str) -> dict[str, Any]:
        normalized = quote(str(scan_id or "").strip(), safe="")
        if not normalized:
            raise ValueError("scan_id is required")
        return self._request(f"/api/ai-trade-desk/scans/{normalized}/review-payload", role="human")

    def validate_reviews(self, payload: Mapping[str, Any], reviews: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        candidates = [item for item in payload.get("candidates") or [] if isinstance(item, Mapping)]
        by_proposal_id = {str(item.get("proposal_id")): item for item in candidates if item.get("proposal_id")}
        if not reviews and str(payload.get("review_status") or "").upper() == "SKIPPED_NO_CANDIDATES":
            return []
        if not reviews:
            raise OperatorError("No reviews supplied for a nonempty review payload")
        validated: list[dict[str, Any]] = []
        for review in reviews:
            item = dict(review)
            proposal = by_proposal_id.get(str(item.get("proposal_id") or ""))
            if not str(item.get("source_scan_id") or "").strip() or not str((proposal or {}).get("source_scan_id") or "").strip():
                proposal_id = str(item.get("proposal_id") or "<missing>")
                raise OperatorError(
                    "SOURCE_PROVENANCE_UNVERIFIED: "
                    f"proposal_id={proposal_id}; missing source_scan_id; "
                    "authoritative recovery is required before review publication"
                )
            missing = [field for field in REQUIRED_REVIEW_FIELDS if item.get(field) in (None, "")]
            if missing:
                raise OperatorError("Review is missing required fields: " + ", ".join(missing))
            for field in ("strengths", "weaknesses", "risk_observations"):
                if not isinstance(item[field], list) or not all(isinstance(value, str) and value.strip() for value in item[field]):
                    raise OperatorError(f"Review field {field} must be a nonempty string list")
            proposal = by_proposal_id.get(str(item["proposal_id"]))
            if proposal is None:
                raise OperatorError("Review proposal_id is not an exact persisted candidate from this payload")
            for field in ("source_scan_id", "symbol", "strategy"):
                if str(item[field]).upper() != str(proposal.get(field) or "").upper():
                    raise OperatorError(f"Review {field} does not match its authoritative candidate")
            validated.append(item)
        if len({item["proposal_id"] for item in validated}) != len(validated):
            raise OperatorError("Review batch contains duplicate proposal IDs")
        return validated

    def publish_reviews(self, scan_id: str, payload: Mapping[str, Any], reviews: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        validated = self.validate_reviews(payload, reviews)
        normalized = quote(str(scan_id or "").strip(), safe="")
        if not normalized:
            raise ValueError("scan_id is required")
        result = self._request(
            f"/api/ai-trade-desk/scans/{normalized}/review-import",
            method="POST",
            payload={"reviews": validated},
            role="ai",
            ai_token=_configured_ai_token() if validated else None,
        )
        published = result.get("published") or []
        for proposal in published:
            proposal_id = str(proposal.get("proposal_id") or "")
            detail = self._request(f"/api/ai-trade-desk/proposals/{quote(proposal_id, safe='')}")
            events = detail.get("events") or []
            if not any(event.get("event_type") == "AI_REVIEW_PUBLISHED" for event in events if isinstance(event, Mapping)):
                raise OperatorError("Published review is missing AI_REVIEW_PUBLISHED audit evidence")
        return result

    def reconcile_review_job(self, scan_id: str) -> dict[str, Any]:
        normalized = quote(str(scan_id or "").strip(), safe="")
        if not normalized:
            raise ValueError("scan_id is required")
        return self._request(
            f"/api/ai-trade-desk/scans/{normalized}/review-reconcile",
            method="POST", payload={}, role="human",
        )


def _read_reviews(path: str) -> list[dict[str, Any]]:
    raw = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    value = json.loads(raw)
    reviews = value.get("reviews") if isinstance(value, Mapping) else value
    if not isinstance(reviews, list) or not all(isinstance(item, Mapping) for item in reviews):
        raise OperatorError("Review input must be a JSON list or an object with a reviews list")
    return [dict(item) for item in reviews]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local ChatGPT Work AI Trade Desk operator")
    parser.add_argument("command", choices=("preflight", "recover-analytics", "submit-market-scan", "submit-gpt-review", "scan-status", "review-payload", "publish-reviews", "reconcile-review"))
    parser.add_argument("--scan-id")
    parser.add_argument("--market-scan-id")
    parser.add_argument("--reviews", help="JSON review file path, or - for stdin")
    parser.add_argument("--authorize-market-scan", action="store_true")
    parser.add_argument("--authorize-gpt-review", action="store_true")
    parser.add_argument("--authorize-safe-start", action="store_true")
    parser.add_argument("--max-wait-seconds", type=float, default=300.0)
    args = parser.parse_args(argv)
    operator = TradingWorkOperator()
    try:
        if args.command == "preflight":
            result = operator.preflight()
        elif args.command == "recover-analytics":
            result = operator.recover_analytics(authorized=args.authorize_safe_start)
        elif args.command == "submit-market-scan":
            submitted = operator.submit_full_market_scan(authorized=args.authorize_market_scan)
            result = operator.poll_market_scan(str(submitted.get("scan_id") or ""), max_wait_seconds=args.max_wait_seconds)
        elif args.command == "submit-gpt-review":
            result = operator.ensure_gpt_review_job(str(args.market_scan_id or ""), authorized=args.authorize_gpt_review)
        elif args.command == "scan-status":
            result = operator.scan_status(str(args.scan_id or ""))
        elif args.command == "review-payload":
            result = operator.review_payload(str(args.scan_id or ""))
        elif args.command == "reconcile-review":
            result = operator.reconcile_review_job(str(args.scan_id or ""))
        else:
            if not args.reviews:
                raise OperatorError("publish-reviews requires --reviews")
            payload = operator.review_payload(str(args.scan_id or ""))
            result = operator.publish_reviews(str(args.scan_id or ""), payload, _read_reviews(args.reviews))
    except (OperatorError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "message": str(exc), "orders_submitted": 0, "approvals_created": 0, "execution_intents_created": 0}, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
