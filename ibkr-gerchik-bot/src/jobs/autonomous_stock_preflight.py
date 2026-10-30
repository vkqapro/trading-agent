"""Read-only readiness checks for the persistent autonomous stock worker.

This module deliberately contains no order-placement path.  It is shared by
the resident worker and the operator preflight script so the worker does not
shell out to a console command and parse human-readable output.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.config import SETTINGS
from src.decision.models import AgentMode
from src.decision.provider_health import ProviderHealthResult


REQUIRED_TABLES = {
    "candidates",
    "decision_snapshots",
    "model_decisions",
    "risk_decisions",
    "execution_links",
    "execution_reservations",
    "provider_health",
}


@dataclass(frozen=True)
class PreflightResult:
    """Safe, dashboard-ready readiness evidence with no account identifier."""

    ready: bool
    mode: str
    provider_status: str
    broker_status: str
    autonomous_entry_enabled: bool
    position_management_allowed: bool
    reasons: tuple[str, ...] = tuple()
    account_status: str = "UNKNOWN"
    allowlist_status: str = "UNKNOWN"
    broker_positions_readable: bool = False
    broker_open_orders_readable: bool = False
    broker_executions_readable: bool = False

    @property
    def blocked_reason(self) -> str:
        return "; ".join(self.reasons)


def read_only_schema(path: Path) -> tuple[bool, str]:
    """Check the decision database without creating or mutating it."""
    if not path.exists():
        return False, "decision_database_missing"
    try:
        with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=2.0) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
    except (OSError, sqlite3.Error) as exc:
        return False, f"decision_database_unreadable:{type(exc).__name__}"
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        return False, "decision_database_missing_tables:" + ",".join(missing)
    return True, "decision_database_healthy"


def read_only_unresolved(path: Path, mode: str) -> tuple[bool, str]:
    """Reject reservations that need reconciliation before a new entry."""
    if not path.exists():
        return False, "decision_database_missing"
    try:
        with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=2.0) as connection:
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM execution_reservations "
                    "WHERE mode=? AND state IN "
                    "('RESERVED','READY_TO_SUBMIT','SUBMITTING','PAPER_MUTATING','RECONCILIATION_REQUIRED')",
                    (mode,),
                ).fetchone()[0]
            )
    except (OSError, sqlite3.Error) as exc:
        return False, f"reservation_reconciliation_unreadable:{type(exc).__name__}"
    return (False, f"unresolved_reservations:{count}") if count else (True, "reconciliation_healthy")


def _provider_evidence(config: Any, audit: Any | None) -> tuple[str, list[str]]:
    """Validate provider construction and stored health without a network call."""
    try:
        from src.decision.provider import build_provider

        provider = build_provider(config)
        provider_name = str(getattr(provider, "provider_name", "") or "")
        model = str(getattr(provider, "model", "") or "")
        if not provider_name or not model:
            return "MISCONFIGURED", ["provider_not_configured"]
        if audit is None:
            return "UNVERIFIED", ["provider_health_database_unavailable"]
        if not audit.provider_health_is_recent(
            provider=provider_name,
            model=model,
            max_age_seconds=float(getattr(config, "provider_health_max_age_seconds", 900.0)),
        ):
            return "UNHEALTHY", ["provider_health_stale_or_missing"]
        return "HEALTHY", []
    except Exception as exc:
        return "UNAVAILABLE", [f"provider_unavailable:{type(exc).__name__}"]


def run_preflight(
    *,
    broker_factory: Callable[[], Any] | None = None,
    config: Any | None = None,
    settings: Any | None = None,
    provider_health: ProviderHealthResult | None = None,
) -> PreflightResult:
    """Run mode-aware, read-only readiness checks.

    Broker checks use only connect/account/positions/open-orders/executions
    reads.  No order manager is constructed and no order mutation is called.
    """
    settings = settings or SETTINGS
    config = config or settings.decision_agent
    reasons: list[str] = []
    try:
        mode = AgentMode.from_value(getattr(config, "mode", AgentMode.OFF.value))
    except ValueError:
        raw_mode = str(getattr(config, "mode", "") or "").strip().lower()
        return PreflightResult(False, raw_mode, "DISABLED", "UNKNOWN", False, False, ("invalid_llm_mode",))

    mode_name = mode.value
    database_path = Path(config.database_path)
    db_ok, db_reason = read_only_schema(database_path)
    audit = None
    if db_ok:
        try:
            from src.decision.audit import DecisionAudit

            audit = DecisionAudit(database_path)
        except Exception:
            reasons.append("decision_database_unavailable")
    elif mode is not AgentMode.OFF:
        reasons.append(db_reason)

    provider_status = "DISABLED" if mode is AgentMode.OFF else "UNVERIFIED"
    if mode is not AgentMode.OFF:
        if provider_health is not None:
            provider_status = "HEALTHY" if provider_health.connected else "UNAVAILABLE"
            if not provider_health.connected:
                reasons.append("provider_health_unavailable")
        else:
            provider_status, provider_reasons = _provider_evidence(config, audit)
            reasons.extend(provider_reasons)

    # The legacy lifecycle still needs a broker connection for data and
    # deterministic position protection. The account allowlist check below is
    # only applied to broker-autonomous modes.
    broker_status = "UNKNOWN"
    account_status = "UNKNOWN"
    allowlist_status = "NOT_APPLICABLE" if mode is not AgentMode.IBKR_PAPER_AUTONOMOUS else "UNKNOWN"
    broker_positions_readable = False
    broker_open_orders_readable = False
    broker_executions_readable = False
    broker = None
    try:
        from src.brokers.ibkr import IBKRClient
    except Exception:
        IBKRClient = None  # type: ignore[assignment]
    if IBKRClient is None and broker_factory is None:
        reasons.append("broker_adapter_unavailable")
    else:
        try:
            broker = (broker_factory or IBKRClient)()  # type: ignore[misc]
            broker.connect()
            identity = broker.get_account_identity()
            paper_verified = identity.get("paper_verified") is True
            account_status = "VERIFIED_PAPER" if paper_verified else "NOT_PAPER"
            broker_status = "PAPER_VERIFIED" if paper_verified else "CONNECTED"
            broker.get_positions()
            broker_positions_readable = True
            broker.get_open_orders()
            broker_open_orders_readable = True
            broker.get_executions()
            broker_executions_readable = True
            if mode is AgentMode.IBKR_PAPER_AUTONOMOUS:
                if not bool(getattr(config, "allow_ibkr_paper_trading", False)):
                    reasons.append("ibkr_paper_permission_missing")
                if settings.paper_trading is not True:
                    reasons.append("paper_trading_disabled")
                if settings.dry_run_mode is not False:
                    reasons.append("dry_run_enabled")
                if bool(getattr(config, "allow_live_trading", False)) or bool(
                    getattr(settings.inefficiency_reclaim, "allow_live_trading", False)
                ):
                    reasons.append("live_trading_permission_enabled")
                if not paper_verified:
                    reasons.append("paper_account_not_verified")
                account_id = str(identity.get("account_id") or "")
                allowlist_match = account_id in tuple(getattr(config, "ibkr_paper_account_allowlist", ()))
                allowlist_status = "VERIFIED" if allowlist_match else "MISMATCH"
                if not allowlist_match:
                    reasons.append("paper_account_not_allowlisted")
                try:
                    config.validate(
                        paper_trading=settings.paper_trading,
                        dry_run=settings.dry_run_mode,
                        account_id=account_id,
                        paper_account_verified=paper_verified,
                        live_trading_enabled=getattr(settings.inefficiency_reclaim, "allow_live_trading", False),
                    )
                except Exception as exc:
                    reasons.append(f"paper_startup_gate:{str(exc)}")
            elif mode is AgentMode.LIVE_AUTONOMOUS:
                try:
                    config.validate(
                        paper_trading=settings.paper_trading,
                        dry_run=settings.dry_run_mode,
                        account_id=str(identity.get("account_id") or ""),
                        paper_account_verified=paper_verified,
                        live_trading_enabled=getattr(settings.inefficiency_reclaim, "allow_live_trading", False),
                    )
                except Exception as exc:
                    reasons.append(f"live_startup_gate:{str(exc)}")
        except Exception as exc:
            broker_status = "DISCONNECTED"
            reasons.append(f"broker_unavailable:{type(exc).__name__}")
        finally:
            if broker is not None:
                try:
                    broker.disconnect()
                except Exception:
                    pass

    if audit is not None and mode is not AgentMode.OFF:
        unresolved_ok, unresolved_reason = read_only_unresolved(database_path, mode_name)
        if not unresolved_ok:
            reasons.append(unresolved_reason)

    autonomous_mode = mode in {
        AgentMode.PAPER_AUTONOMOUS,
        AgentMode.IBKR_PAPER_AUTONOMOUS,
        AgentMode.LIVE_AUTONOMOUS,
    }
    provider_failure_only = bool(reasons) and all(
        reason.startswith("provider_") for reason in reasons
    )
    entry_enabled = autonomous_mode and not reasons
    management_allowed = broker_status in {"CONNECTED", "PAPER_VERIFIED"} and not any(
        reason.startswith("broker_") or reason.startswith("decision_database") or reason.startswith("reconciliation")
        for reason in reasons
    )
    if provider_failure_only and management_allowed:
        # Position management/protection is deterministic and may continue;
        # only new LLM entries remain disabled until provider health recovers.
        return PreflightResult(
            False,
            mode_name,
            provider_status,
            broker_status,
            False,
            True,
            tuple(dict.fromkeys(reasons)),
            account_status,
            allowlist_status,
            broker_positions_readable,
            broker_open_orders_readable,
            broker_executions_readable,
        )
    return PreflightResult(
        not reasons,
        mode_name,
        provider_status,
        broker_status,
        entry_enabled,
        management_allowed,
        tuple(dict.fromkeys(reasons)),
        account_status,
        allowlist_status,
        broker_positions_readable,
        broker_open_orders_readable,
        broker_executions_readable,
    )
