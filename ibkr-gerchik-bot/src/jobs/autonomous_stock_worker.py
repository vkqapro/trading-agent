"""Persistent application worker for Decision Lab analysis and health state.

The independent Windows legacy scheduler remains the owner of all stock
session jobs. This worker observes shared preflight/provider evidence, reads
persisted strategy data, and consumes Decision Lab analysis runs. It never
starts a premarket, open, intraday, or entry-scan job and never owns their
session locks.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from src.config import LOGGER, SETTINGS, ensure_directories
from src.jobs.autonomous_stock_preflight import PreflightResult, run_preflight
from src.decision.provider_health import ProviderHealthResult, run_provider_health_check
from src.jobs.session_utils import (
    INTRADAY_END_TIME,
    OPEN_SCAN_END,
    OPEN_SCAN_START,
    session_now,
)


SERVICE_NAME = "autonomous_stock_worker"
HEARTBEAT_NAME = "autonomous_stock_worker.json"
LOCK_NAME = "autonomous_stock_worker.lock"
PID_NAME = "autonomous_stock_worker.pid"
STOP_NAME = "autonomous_stock_worker.stop"
HEARTBEAT_INTERVAL_SECONDS = 5.0
WORKER_SLEEP_SECONDS = 15.0
CLOSED_SLEEP_SECONDS = 30.0


def _heartbeat_provider_status(preflight: PreflightResult) -> str:
    """Reduce shared preflight provider evidence to a safe heartbeat category."""
    if preflight.provider_status in {"HEALTHY", "CONNECTED"}:
        return "CONNECTED"
    if preflight.provider_status in {"UNAVAILABLE", "DISCONNECTED", "UNHEALTHY"}:
        return "UNAVAILABLE"
    return str(preflight.provider_status or "UNKNOWN").upper()


def _heartbeat_blocked_reason(preflight: PreflightResult) -> str:
    """Map internal preflight reasons to bounded, non-sensitive UI categories."""
    reasons = " ".join(preflight.reasons).lower()
    if "broker_" in reasons or "broker adapter" in reasons:
        return "BROKER_DISCONNECTED"
    if "allowlist" in reasons:
        return "ALLOWLIST_MISMATCH"
    if "account_not_verified" in reasons or "account_unknown" in reasons:
        return "ACCOUNT_NOT_PAPER"
    if "managed_accounts" in reasons or "account_summary" in reasons or "account_id" in reasons:
        return "ACCOUNT_UNKNOWN"
    if "reconciliation" in reasons or "unresolved_reservations" in reasons:
        return "RECONCILIATION_REQUIRED"
    if "provider_" in reasons:
        return "PROVIDER_UNAVAILABLE"
    if reasons:
        return "PREFLIGHT_BLOCKED"
    return ""


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(SETTINGS.trading_hours.timezone))
    return value.isoformat()


def _pid_alive(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return pid == os.getpid()
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_owner_pid(path: Path) -> int | None:
    try:
        first = path.read_text(encoding="utf-8").split("|", 1)[0].strip()
        return int(first)
    except (OSError, TypeError, ValueError):
        return None


class AutonomousStockWorker:
    """Single-instance, heartbeat-producing session supervisor."""

    def __init__(
        self,
        *,
        runtime_dir: Path | None = None,
        now_provider: Callable[[], datetime] | None = None,
        sleep_provider: Callable[[float], None] | None = None,
        preflight_runner: Callable[[], PreflightResult] | None = None,
        provider_health_runner: Callable[[], ProviderHealthResult] | None = None,
        job_runner: Callable[[str, dict[str, object]], dict[str, object]] | None = None,
        heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        max_cycles: int | None = None,
    ) -> None:
        ensure_directories()
        self.runtime_dir = Path(runtime_dir or SETTINGS.paths.runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.status_path = self.runtime_dir / HEARTBEAT_NAME
        self.lock_path = self.runtime_dir / LOCK_NAME
        self.pid_path = self.runtime_dir / PID_NAME
        self.stop_path = self.runtime_dir / STOP_NAME
        self.now_provider = now_provider or session_now
        self.sleep_provider = sleep_provider or time.sleep
        self.provider_health_runner = provider_health_runner or (
            lambda: run_provider_health_check(SETTINGS.decision_agent)
        )
        self.provider_health_interval_seconds = max(
            float(getattr(SETTINGS.decision_agent, "provider_health_interval_seconds", 300.0)),
            1.0,
        )
        self.preflight_runner = preflight_runner or self._run_default_preflight
        # Retained as a source-compatible constructor argument for wrappers
        # that still pass it. It is intentionally never called: session-job
        # ownership belongs exclusively to the independent legacy scheduler.
        self.job_runner = job_runner
        self.heartbeat_interval_seconds = max(float(heartbeat_interval_seconds), 0.5)
        self.max_cycles = max_cycles
        self.stop_event = threading.Event()
        self._lock_handle: int | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._status_lock = threading.Lock()
        self._status: dict[str, object] = {
            "service": SERVICE_NAME,
            "pid": os.getpid(),
            "started_at": None,
            "last_heartbeat": None,
            "state": "STARTING",
            "current_session": "MARKET CLOSED",
            "llm_mode": str(getattr(SETTINGS.decision_agent, "mode", "off")),
            "provider_status": "UNKNOWN",
            "provider_last_checked_at": None,
            "provider_latency_ms": None,
            "provider_last_error": None,
            "broker_status": "UNKNOWN",
            "account_status": "UNKNOWN",
            "allowlist_status": "UNKNOWN",
            "broker_positions_readable": False,
            "broker_open_orders_readable": False,
            "broker_executions_readable": False,
            "autonomous_entry_enabled": False,
            "blocked_reason": "starting",
            "last_preflight_at": None,
            "last_error": None,
            "manual_queue_depth": 0,
            "active_manual_run_id": None,
            "active_manual_strategy": None,
            "active_manual_started_at": None,
            "last_manual_run_state": "IDLE",
            "last_manual_run_id": None,
        }
        self._session_date: date | None = None
        self._completed_phases: set[str] = set()
        self._last_preflight: PreflightResult | None = None
        self._last_provider_health: ProviderHealthResult | None = None
        self._last_provider_health_monotonic: float | None = None
        self._last_auto_fingerprint: str | None = None
        self._last_auto_provider: float | None = None

    def _write_status(self) -> None:
        with self._status_lock:
            payload = dict(self._status)
            payload["last_heartbeat"] = datetime.now(ZoneInfo("UTC")).isoformat()
        temporary = self.status_path.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(temporary, self.status_path)
        except OSError:
            LOGGER.debug("Unable to write autonomous worker heartbeat", exc_info=True)

    def _set_status(self, **values: object) -> None:
        with self._status_lock:
            self._status.update(values)
        self._write_status()

    def _heartbeat_loop(self) -> None:
        while not self.stop_event.wait(self.heartbeat_interval_seconds):
            self._write_status()

    def _acquire_lock(self) -> bool:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self._lock_handle = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                os.write(self._lock_handle, f"{os.getpid()}|{datetime.now().isoformat()}".encode("utf-8"))
                self.pid_path.write_text(str(os.getpid()), encoding="utf-8")
                return True
            except FileExistsError:
                owner = _read_owner_pid(self.lock_path)
                if owner is not None and _pid_alive(owner):
                    return False
                # A dead owner is safe to recover; no process is terminated.
                try:
                    self.lock_path.unlink()
                except OSError:
                    return False

    def _release_lock(self) -> None:
        if self._lock_handle is not None:
            try:
                os.close(self._lock_handle)
            except OSError:
                pass
            self._lock_handle = None
        try:
            if self.pid_path.exists() and _read_owner_pid(self.pid_path) in {None, os.getpid()}:
                self.pid_path.unlink()
        except OSError:
            pass
        try:
            if self.lock_path.exists() and _read_owner_pid(self.lock_path) in {None, os.getpid()}:
                self.lock_path.unlink()
        except OSError:
            pass

    def request_stop(self) -> None:
        self.stop_event.set()

    def _stop_requested(self) -> bool:
        return self.stop_event.is_set() or self.stop_path.exists()

    def _record_preflight(self, preflight: PreflightResult) -> None:
        self._set_status(
            provider_status=_heartbeat_provider_status(preflight),
            broker_status=str(preflight.broker_status or "UNKNOWN").upper(),
            account_status=str(preflight.account_status or "UNKNOWN").upper(),
            allowlist_status=str(preflight.allowlist_status or "UNKNOWN").upper(),
            broker_positions_readable=bool(preflight.broker_positions_readable),
            broker_open_orders_readable=bool(preflight.broker_open_orders_readable),
            broker_executions_readable=bool(preflight.broker_executions_readable),
            last_preflight_at=datetime.now(ZoneInfo("UTC")).isoformat(),
        )

    def _record_provider_health(self, result: ProviderHealthResult) -> None:
        self._set_status(
            provider_status=result.status,
            provider_last_checked_at=result.checked_at,
            provider_latency_ms=result.latency_ms,
            provider_last_error=result.error,
        )
        try:
            from src.decision.audit import DecisionAudit

            DecisionAudit(SETTINGS.decision_agent.database_path).record_provider_health(
                provider=result.provider,
                model=result.model,
                status="ok" if result.connected else "error",
                latency_ms=result.latency_ms,
                error=result.error,
            )
        except Exception:
            # The heartbeat remains authoritative for this worker cycle.  A
            # provider-health audit write must never stop broker protection.
            LOGGER.debug("Unable to persist provider health observation", exc_info=True)

    def _record_manual_status(self) -> None:
        try:
            from src.decision.strategy_control import manual_status_payload

            self._set_status(**manual_status_payload(runtime_dir=self.runtime_dir))
        except Exception:
            LOGGER.debug("Unable to update Decision Lab manual status", exc_info=True)

    def _analysis_execution_context(self, phase: str) -> object:
        from src.decision.strategy_controller import AnalysisExecutionContext

        return AnalysisExecutionContext(
            order_manager=None,
            market_data=None,
            account_equity=0.0,
            cash_available=0.0,
            current_positions=[],
            open_risk_amount=0.0,
            market_open=phase in {"OPEN", "INTRADAY"},
        )

    def _process_decision_lab(self, phase: str) -> None:
        """Refresh persisted source data and consume application-owned runs.

        This method is deliberately independent from the Windows trading
        backend. It does not call premarket/open/intraday, fetch candles, or
        acquire market-session locks.
        """
        from src.decision.strategy_control import load_control
        from src.decision.strategy_controller import get_strategy_analysis_controller
        from src.decision.strategy_sources import refresh_current_source_snapshot

        snapshot = refresh_current_source_snapshot(minimum_interval_seconds=30.0)
        execution_context = self._analysis_execution_context(phase)
        controller = get_strategy_analysis_controller()
        controller.drain_pending_manual(execution_context)

        control = load_control(runtime_dir=self.runtime_dir)
        if str(control.get("analysis_mode") or "manual").lower() != "auto":
            return
        now_monotonic = time.monotonic()
        interval = max(float(getattr(SETTINGS.decision_agent, "auto_analysis_interval_minutes", 30.0)) * 60.0, 60.0)
        if self._last_auto_provider is not None and now_monotonic - self._last_auto_provider < interval:
            return
        source = str(control.get("strategy_source") or "gerchik_router").strip().lower()
        metadata = snapshot.get("source_metadata", {})
        selected = metadata.get(source, {}) if isinstance(metadata, Mapping) else {}
        fingerprint = str(
            selected.get("fingerprint") if isinstance(selected, Mapping) else snapshot.get("analysis_snapshot_id")
        )
        if fingerprint and fingerprint == self._last_auto_fingerprint:
            self._last_auto_provider = now_monotonic
            return
        run = controller.schedule_auto_from_snapshot(
            snapshot,
            strategy_source=source,
            execution_context=execution_context,
            prompt_preset_id=str(control.get("prompt_preset_id") or "") or None,
        )
        if run is not None:
            self._last_auto_fingerprint = fingerprint
            self._last_auto_provider = now_monotonic

    def _provider_health_due(self) -> bool:
        if self._last_provider_health is None or not self._last_provider_health.connected:
            return True
        if self._last_provider_health_monotonic is None:
            return True
        return time.monotonic() - self._last_provider_health_monotonic >= self.provider_health_interval_seconds

    def _run_default_preflight(self) -> PreflightResult:
        raw_mode = getattr(SETTINGS.decision_agent, "mode", "off")
        mode = str(getattr(raw_mode, "value", raw_mode)).strip().lower()
        if mode != "off" and self._provider_health_due():
            try:
                result = self.provider_health_runner()
            except Exception:
                result = ProviderHealthResult(
                    status="UNAVAILABLE",
                    provider=str(getattr(SETTINGS.decision_agent, "provider", "") or "").strip().lower(),
                    model=str(getattr(SETTINGS.decision_agent, "model", "") or getattr(SETTINGS.decision_agent, "local_model", "") or "").strip(),
                    checked_at=datetime.now(ZoneInfo("UTC")).isoformat(),
                    error="UNKNOWN_PROVIDER_ERROR",
                )
            self._last_provider_health = result
            self._last_provider_health_monotonic = time.monotonic()
            self._record_provider_health(result)
        return run_preflight(
            provider_health=self._last_provider_health if mode != "off" else None,
        )

    @staticmethod
    def session_phase(current_time: datetime) -> str:
        """Return the existing weekday/time lifecycle phase.

        The project currently has weekday/time session logic but no exchange
        holiday calendar.  Holiday handling therefore remains a documented
        limitation and broker/data gates must fail closed on those days.
        """
        if current_time.weekday() >= 5:
            return "MARKET_CLOSED"
        local_time = current_time.timetz().replace(tzinfo=None)
        market_open = dt_time(
            SETTINGS.trading_hours.market_open_hour,
            SETTINGS.trading_hours.market_open_minute,
        )
        if local_time < market_open or local_time >= INTRADAY_END_TIME:
            return "MARKET_CLOSED"
        if local_time < OPEN_SCAN_START:
            return "PREMARKET"
        if local_time < OPEN_SCAN_END:
            return "OPEN"
        return "INTRADAY"

    def _reset_day_if_needed(self, current_time: datetime) -> None:
        current_date = current_time.date()
        if self._session_date != current_date:
            self._session_date = current_date
            self._completed_phases.clear()

    def run_cycle(self) -> str:
        """Advance one Decision Lab application cycle.

        The independent Windows scheduler remains the owner of all stock
        session jobs. This worker only observes safety/provider state, reads
        persisted strategy data, and processes Decision Lab analysis runs.
        """
        current_time = self.now_provider()
        self._reset_day_if_needed(current_time)
        phase = self.session_phase(current_time)
        session_label = phase.replace("_", " ")
        self._set_status(current_session=session_label, last_error=None)
        self._record_manual_status()
        try:
            preflight = self.preflight_runner()
        except Exception as exc:
            preflight = PreflightResult(
                False,
                str(getattr(SETTINGS.decision_agent, "mode", "off")),
                "UNKNOWN",
                "UNKNOWN",
                False,
                False,
                (f"preflight_exception:{type(exc).__name__}",),
            )
        self._last_preflight = preflight
        self._record_preflight(preflight)
        degraded = not preflight.ready
        if phase == "MARKET_CLOSED":
            runtime_state = "MARKET_CLOSED"
        elif not preflight.ready and not preflight.position_management_allowed:
            runtime_state = "BLOCKED"
        else:
            runtime_state = "DEGRADED" if degraded else "READY"
        self._set_status(
            state=runtime_state,
            llm_mode=preflight.mode,
            autonomous_entry_enabled=(preflight.autonomous_entry_enabled if phase != "MARKET_CLOSED" else False),
            blocked_reason=(
                _heartbeat_blocked_reason(preflight)
                or ("MARKET_CLOSED" if phase == "MARKET_CLOSED" else "PREFLIGHT_BLOCKED" if not preflight.ready else None)
            ),
        )
        try:
            self._process_decision_lab(phase)
        except KeyboardInterrupt:
            self.request_stop()
            return phase
        except Exception as exc:
            self._set_status(
                state="DEGRADED",
                blocked_reason="DECISION_LAB_ANALYSIS_ERROR",
                last_error=type(exc).__name__,
            )
        self._record_manual_status()
        return phase

    def _wait(self, seconds: float) -> None:
        remaining = max(float(seconds), 0.0)
        while remaining > 0 and not self._stop_requested():
            chunk = min(remaining, 5.0)
            self.sleep_provider(chunk)
            remaining -= chunk

    def run(self) -> int:
        """Run until a stop signal, termination signal, or test cycle limit."""
        if not self._acquire_lock():
            return 2
        try:
            # A stale stop request must not immediately kill a newly acquired
            # worker; the stop script creates it only after identifying this
            # exact worker or its wrapper.
            try:
                self.stop_path.unlink()
            except OSError:
                pass
            started_at = datetime.now(ZoneInfo("UTC")).isoformat()
            self._set_status(
                started_at=started_at,
                state="STARTING",
                blocked_reason="starting",
                last_preflight_at=None,
                pid=os.getpid(),
            )
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name="autonomous-stock-heartbeat",
                daemon=True,
            )
            self._heartbeat_thread.start()
            cycles = 0
            while not self._stop_requested():
                self.run_cycle()
                cycles += 1
                if self.max_cycles is not None and cycles >= self.max_cycles:
                    break
                phase = str(self._status.get("current_session", "MARKET CLOSED"))
                self._wait(CLOSED_SLEEP_SECONDS if phase == "MARKET CLOSED" else WORKER_SLEEP_SECONDS)
            return 0
        except KeyboardInterrupt:
            return 0
        finally:
            self.stop_event.set()
            self._set_status(
                state="STOPPING",
                autonomous_entry_enabled=False,
                blocked_reason="stop_requested",
            )
            if self._heartbeat_thread is not None:
                self._heartbeat_thread.join(timeout=max(self.heartbeat_interval_seconds + 1.0, 2.0))
            self._set_status(
                state="STOPPED",
                autonomous_entry_enabled=False,
                blocked_reason="stopped",
            )
            self._release_lock()


def run_autonomous_stock_worker() -> int:
    worker = AutonomousStockWorker()
    previous_handlers: dict[int, Any] = {}

    def _handle_signal(signum: int, _frame: Any) -> None:
        LOGGER.info("Autonomous stock worker received signal %s; stopping safely.", signum)
        worker.request_stop()

    for signum in (signal.SIGINT, getattr(signal, "SIGTERM", signal.SIGINT)):
        try:
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, _handle_signal)
        except (ValueError, OSError):
            pass
    try:
        return worker.run()
    finally:
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                pass


if __name__ == "__main__":
    raise SystemExit(run_autonomous_stock_worker())
