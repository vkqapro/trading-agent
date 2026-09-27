"""Read-only preflight for ``ibkr_paper_autonomous``.

The default invocation reads configuration and an already-created SQLite
database in read-only mode. ``--check-broker`` may read IBKR account identity,
positions, open orders, and executions, but this script has no order-placement
call and never edits ``.env`` or starts a worker.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TABLES = {
    "candidates", "decision_snapshots", "model_decisions", "risk_decisions",
    "execution_links", "execution_reservations", "provider_health",
}


def read_only_schema(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "not initialized"
    try:
        sqlite_path = quote(path.resolve().as_posix(), safe="/:\\")
        with sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True, timeout=2.0) as connection:
            tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except (OSError, sqlite3.Error) as exc:
        return False, f"unreadable: {exc}"
    missing = sorted(REQUIRED_TABLES - tables)
    return (False, "missing tables: " + ", ".join(missing)) if missing else (True, "schema available")


def read_only_unresolved(path: Path, mode: str) -> tuple[bool, str]:
    if not path.exists():
        return True, "database not initialized"
    try:
        sqlite_path = quote(path.resolve().as_posix(), safe="/:\\")
        with sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True, timeout=2.0) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM execution_reservations WHERE mode=? AND state IN ('RESERVED','READY_TO_SUBMIT','SUBMITTING','RECONCILIATION_REQUIRED')",
                (mode,),
            ).fetchone()[0]
    except (OSError, sqlite3.Error) as exc:
        return False, f"unreadable: {exc}"
    return (False, f"{count} unresolved reservation(s)") if count else (True, "no unresolved reservations")


def provider_check(config) -> tuple[bool, str]:
    from src.decision.models import AgentMode, DecisionAction, DecisionCandidate, DecisionRequest, DecisionSnapshot
    from src.decision.provider import build_provider

    provider = build_provider(config)
    now = datetime.now(timezone.utc)
    candidate = DecisionCandidate(
        candidate_id="ibkr-paper-health-preflight", created_at=now, asset_class="diagnostic",
        symbol="HEALTH", strategy="provider_health", direction="none", entry=0.0,
        stop=0.0, target=0.0, metadata={"health_probe": True, "non_trading": True},
    )
    request = DecisionRequest(
        decision_id="ibkr-paper-health-preflight", agent_id=str(config.agent_id),
        mode=AgentMode.IBKR_PAPER_AUTONOMOUS, provider=str(provider.provider_name),
        model=str(provider.model), snapshot=DecisionSnapshot.from_candidate(candidate, session="provider_health"),
        allowed_actions=(DecisionAction.WAIT.value,),
    )
    response = provider.decide(request)
    return (True, "provider returned WAIT") if response.action is DecisionAction.WAIT else (False, "provider returned non-WAIT action")


def check(name: str, condition: bool, detail: str, failures: list[str]) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {name}: {detail}")
    if not condition:
        failures.append(name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only IBKR Paper Autonomous preflight")
    parser.add_argument("--check-broker", action="store_true", help="read IBKR account/state evidence; never place an order")
    parser.add_argument("--check-provider", action="store_true", help="make one provider-only WAIT request")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    from src.config import SETTINGS

    config = SETTINGS.decision_agent
    mode = str(config.mode).strip().lower()
    database_path = Path(config.database_path)
    failures: list[str] = []
    print("IBKR Paper Autonomous configuration preflight (read-only)")
    print(f"repository: {ROOT}")
    print(f"database: {database_path}")

    check("agent mode", mode == "ibkr_paper_autonomous", f"LLM_AGENT_MODE={mode!r}", failures)
    check("dedicated permission", bool(getattr(config, "allow_ibkr_paper_trading", False)), f"ALLOW_LLM_IBKR_PAPER_TRADING={getattr(config, 'allow_ibkr_paper_trading', False)!r}", failures)
    check("Paper broker mode", SETTINGS.paper_trading is True, f"PAPER_TRADING={SETTINGS.paper_trading!r}", failures)
    check("real execution enabled", SETTINGS.dry_run_mode is False, f"DRY_RUN_MODE={SETTINGS.dry_run_mode!r}", failures)
    check("LLM live permission disabled", config.allow_live_trading is False, f"ALLOW_LLM_LIVE_TRADING={config.allow_live_trading!r}", failures)
    check("legacy live permission disabled", SETTINGS.inefficiency_reclaim.allow_live_trading is False, f"ALLOW_LIVE_TRADING={SETTINGS.inefficiency_reclaim.allow_live_trading!r}", failures)
    check("News disabled", config.use_news is False, f"LLM_AGENT_USE_NEWS={config.use_news!r}", failures)
    check("paper account allowlist", bool(getattr(config, "ibkr_paper_account_allowlist", ())), "configured" if getattr(config, "ibkr_paper_account_allowlist", ()) else "empty", failures)
    check("position cap", int(getattr(config, "ibkr_paper_max_open_positions", 0)) == 1, str(getattr(config, "ibkr_paper_max_open_positions", None)), failures)
    check("daily trade cap", 1 <= int(getattr(config, "ibkr_paper_max_trades_per_day", 0)) <= 3, str(getattr(config, "ibkr_paper_max_trades_per_day", None)), failures)
    check("risk cap", float(getattr(config, "ibkr_paper_risk_per_trade_pct", 0.0)) == 0.10, str(getattr(config, "ibkr_paper_risk_per_trade_pct", None)), failures)
    if mode == "ibkr_paper_autonomous":
        try:
            config.validate(
                paper_trading=SETTINGS.paper_trading,
                dry_run=SETTINGS.dry_run_mode,
                account_id="preflight-unverified",
                paper_account_verified=False,
                live_trading_enabled=SETTINGS.inefficiency_reclaim.allow_live_trading,
            )
            # The deliberately unverified placeholder must fail closed;
            # reaching this point indicates validation did not enforce account
            # evidence.
            check("configuration account gate", False, "unverified account was accepted", failures)
        except ValueError as exc:
            check("configuration safety validation", "verified" in str(exc) or "account" in str(exc), str(exc), failures)
        except Exception as exc:
            check("configuration safety validation", False, str(exc), failures)
    else:
        print("[INFO] configuration safety validation: not run because mode is not ibkr_paper_autonomous")

    schema_ok, schema_detail = read_only_schema(database_path)
    check("Decision DB schema", schema_ok, schema_detail, failures)
    unresolved_ok, unresolved_detail = read_only_unresolved(database_path, mode)
    check("unresolved reservations", unresolved_ok, unresolved_detail, failures)

    if args.check_broker:
        try:
            from src.brokers.ibkr import IBKRClient
            broker = IBKRClient()
            identity = broker.get_account_identity()
            account_id = str(identity.get("account_id") or "")
            account_ok = bool(identity.get("paper_verified") is True and account_id in tuple(config.ibkr_paper_account_allowlist))
            check("broker Paper identity and allowlist", account_ok, str(identity.get("evidence", "unknown")), failures)
            check("broker positions readable", isinstance(broker.get_positions(), list), "read-only positions", failures)
            check("broker open orders readable", isinstance(broker.get_open_orders(), list), "read-only open orders", failures)
            check("broker executions readable", isinstance(broker.get_executions(), list), "read-only executions", failures)
        except Exception as exc:
            check("broker read-only evidence", False, str(exc), failures)
    else:
        print("[INFO] broker evidence: not run; use --check-broker explicitly")
    if args.check_provider:
        try:
            ok, detail = provider_check(config)
        except Exception as exc:
            ok, detail = False, str(exc)
        check("provider connectivity", ok, detail, failures)
    else:
        print("[INFO] provider connectivity: not run; use --check-provider explicitly")

    print()
    if failures:
        print("IBKR PAPER AUTONOMOUS PREFLIGHT: NOT READY")
        print("failed checks: " + ", ".join(failures))
        return 1
    print("IBKR PAPER AUTONOMOUS PREFLIGHT: READY FOR CONTROLLED MANUAL VALIDATION")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
