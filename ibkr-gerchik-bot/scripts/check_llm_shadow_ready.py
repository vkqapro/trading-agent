"""Read-only configuration preflight for the first LLM Shadow experiment.

This script never creates the Decision Lab database, starts a job, connects to
IBKR, calls an order method, or edits ``.env``. ``--check-provider`` is an
explicit optional network call to the configured LLM provider only.
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
    "candidates",
    "decision_snapshots",
    "model_decisions",
    "risk_decisions",
    "execution_links",
    "execution_reservations",
    "provider_health",
}


def _read_only_schema(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "not initialized"
    try:
        sqlite_path = quote(path.resolve().as_posix(), safe="/:\\")
        with sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True, timeout=2.0) as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        tables = {str(row[0]) for row in rows}
    except (OSError, sqlite3.Error) as exc:
        return False, f"unreadable: {exc}"
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        return False, "missing tables: " + ", ".join(missing)
    return True, "schema available"


def _provider_check(config) -> tuple[bool, str]:
    from src.decision.models import AgentMode, DecisionAction, DecisionCandidate, DecisionRequest, DecisionSnapshot
    from src.decision.provider import build_provider

    provider = build_provider(config)
    now = datetime.now(timezone.utc)
    candidate = DecisionCandidate(
        candidate_id="shadow-health-preflight",
        created_at=now,
        asset_class="stock",
        symbol="HEALTH",
        strategy="provider_health",
        direction="long",
        entry=1.0,
        stop=0.99,
        target=1.02,
        metadata={"health_probe": True, "identity_status": "stable"},
    )
    request = DecisionRequest(
        decision_id="shadow-health-preflight",
        agent_id=str(config.agent_id),
        mode=AgentMode.SHADOW,
        provider=str(provider.provider_name),
        model=str(provider.model),
        snapshot=DecisionSnapshot.from_candidate(
            candidate,
            session="provider_health",
            context={"health_probe": True},
        ),
        allowed_actions=(DecisionAction.WAIT.value,),
    )
    response = provider.decide(request)
    if response.action is not DecisionAction.WAIT:
        return False, f"returned non-WAIT action: {response.action.value}"
    return True, "provider returned WAIT"


def _check(name: str, condition: bool, detail: str, failures: list[str]) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}: {detail}")
    if not condition:
        failures.append(name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only LLM Shadow configuration preflight")
    parser.add_argument(
        "--allow-uninitialized-db",
        action="store_true",
        help="allow a fresh Decision Lab path that the first Shadow run will initialize",
    )
    parser.add_argument(
        "--check-provider",
        action="store_true",
        help="make one optional provider-only WAIT health request; never contacts IBKR",
    )
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    from src.config import SETTINGS

    config = SETTINGS.decision_agent
    failures: list[str] = []
    mode = str(config.mode).strip().lower()
    provider = str(config.provider).strip().lower()
    model = str(config.model or config.local_model or "").strip()
    database_path = Path(config.database_path)

    print("LLM Shadow configuration preflight (read-only)")
    print(f"repository: {ROOT}")
    print(f"database: {database_path}")
    print()

    _check("agent mode", mode == "shadow", f"LLM_AGENT_MODE={mode!r}", failures)
    _check(
        "Live permission",
        config.allow_live_trading is False,
        f"ALLOW_LLM_LIVE_TRADING={config.allow_live_trading!r}",
        failures,
    )
    _check(
        "autonomous News",
        config.use_news is False,
        f"LLM_AGENT_USE_NEWS={config.use_news!r}",
        failures,
    )
    _check(
        "Paper broker mode",
        SETTINGS.paper_trading is True,
        f"PAPER_TRADING={SETTINGS.paper_trading!r}",
        failures,
    )
    _check(
        "dry-run broker mode",
        SETTINGS.dry_run_mode is True,
        f"DRY_RUN_MODE={SETTINGS.dry_run_mode!r}",
        failures,
    )
    _check(
        "legacy live-trading permission",
        SETTINGS.inefficiency_reclaim.allow_live_trading is False,
        f"ALLOW_LIVE_TRADING={SETTINGS.inefficiency_reclaim.allow_live_trading!r}",
        failures,
    )
    _check(
        "legacy trading mode",
        str(SETTINGS.inefficiency_reclaim.trading_mode).lower() == "paper",
        f"IRS_TRADING_MODE={SETTINGS.inefficiency_reclaim.trading_mode!r}",
        failures,
    )
    _check("provider", provider in {"deepseek", "local_openai", "openai", "anthropic"}, provider, failures)
    _check("model", bool(model), "configured" if model else "missing", failures)
    _check(
        "single-provider Shadow",
        config.multi_provider_shadow is False,
        f"LLM_MULTI_PROVIDER_SHADOW={config.multi_provider_shadow!r}",
        failures,
    )
    _check("decision workers", config.decision_workers > 0, str(config.decision_workers), failures)
    _check("decision queue depth", config.decision_queue_depth > 0, str(config.decision_queue_depth), failures)
    _check("candidate expiry", config.candidate_expiry_seconds > 0, str(config.candidate_expiry_seconds), failures)
    try:
        config.validate(paper_trading=SETTINGS.paper_trading)
        config_validation_detail = "configuration validation passed"
        config_validation_ok = True
    except Exception as exc:
        config_validation_detail = str(exc)
        config_validation_ok = False
    _check("agent configuration", config_validation_ok, config_validation_detail, failures)

    parent_ok = database_path.parent.exists() and os.access(database_path.parent, os.R_OK | os.W_OK)
    _check("Decision DB path", parent_ok, "parent exists and is accessible" if parent_ok else "parent is missing or inaccessible", failures)
    schema_ok, schema_detail = _read_only_schema(database_path)
    if not schema_ok and args.allow_uninitialized_db and schema_detail == "not initialized":
        print("[WARN] Decision DB schema: not initialized; first Shadow start will create it")
    else:
        _check("Decision DB schema", schema_ok, schema_detail, failures)

    if args.check_provider:
        try:
            provider_ok, provider_detail = _provider_check(config)
        except Exception as exc:  # provider-only diagnostic; never a broker path
            provider_ok, provider_detail = False, f"provider check failed: {exc}"
        _check("provider connectivity", provider_ok, provider_detail, failures)
    else:
        print("[INFO] provider connectivity: not run; use --check-provider explicitly")

    print()
    if failures:
        print("SHADOW PREFLIGHT: NOT READY")
        print("failed checks: " + ", ".join(failures))
        return 1
    print("SHADOW PREFLIGHT: READY FOR CONFIGURATION-ONLY START")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
