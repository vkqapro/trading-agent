"""Deterministic, failure-isolated LP/PRB universe runner."""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .artifacts import write_run_artifacts
from .contracts import SCANNER_VERSION, SCHEMA_VERSION, ScanStatus, StrategyScanResult
from .explainability import pattern_definition
from .data_adapter import (
    DATA_SOURCE,
    available_daily_symbols,
    configured_symbols,
    load_partial_daily_bars,
    universe_source,
)

# Kept as the production loader name so integrations can patch the scanner
# boundary; it now resolves to the canonical partial-day loader.
load_closed_daily_bars = load_partial_daily_bars
from .registry import PRODUCTION_STRATEGY_IDS, get_strategy_spec

_DEFAULT_RUNS_ROOT = Path(__file__).resolve().parent / "runs"
_STRATEGY_SOURCE = Path(__file__).resolve().parents[1] / "strategies" / "market_screener_lp_prb_strategy_2.py"


def new_run_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"scan_{stamp}_{uuid4().hex[:6]}"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _source_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _error_result(run_id: str, symbol: str, strategy_id: str, message: str, data: dict[str, Any]) -> StrategyScanResult:
    spec = get_strategy_spec(strategy_id)
    return StrategyScanResult(
        run_id=run_id, symbol=symbol, strategy_id=strategy_id, strategy_version=spec.version,
        direction="LONG", timeframe=spec.timeframe, as_of=data.get("last_bar_timestamp"),
        status=ScanStatus.ERROR, matched=False,
        signal={"pattern": spec.display_name, "signal_bar": None, "signal_bar_date": None, "signal_bar_index": None, "triggered": False},
        market={"close": None, "atr": None}, level={"price": None, "level_type": None, "zone_low": None, "zone_high": None},
        trade_plan={"entry": None, "stop": None, "target": None, "rr": None, "reward_risk": None}, quality={"score": None},
        evidence={}, pattern_definition=pattern_definition(strategy_id),
        rejection_evidence=("evaluation_error",),
        reason_codes=("EVALUATION_ERROR",), rejection_reasons=(message,), data=data, warnings=(),
    )


def run_universe_scan(
    symbols: Iterable[str] | None = None,
    *,
    strategy_ids: Iterable[str] = PRODUCTION_STRATEGY_IDS,
    direction: str = "LONG",
    as_of: str | None = None,
    runs_root: Path | str | None = None,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    if direction.upper() != "LONG":
        raise ValueError("Strategy Scanner v0.2 supports LONG only")
    requested = list(dict.fromkeys(str(symbol).strip().upper() for symbol in (symbols or configured_symbols()) if str(symbol).strip()))
    strategies = tuple(str(item).lower() for item in strategy_ids)
    specs = [get_strategy_spec(item) for item in strategies]
    run_id = new_run_id()
    started_at = _iso_now()
    started_perf = time.perf_counter()
    available = available_daily_symbols(requested)
    available_set = set(available)
    skipped: list[dict[str, str]] = [
        {"symbol": symbol, "reason": "NO_PERSISTED_DAILY_DATA"}
        for symbol in requested if symbol not in available_set
    ]
    errors: list[dict[str, Any]] = [dict(item, scope="symbol") for item in skipped]
    symbol_payloads: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    scanned: list[str] = []

    for symbol in available:
        try:
            loaded = load_closed_daily_bars(symbol, as_of=as_of)
        except Exception as exc:
            record = {"symbol": symbol, "scope": "symbol", "reason": type(exc).__name__, "message": str(exc)}
            skipped.append({"symbol": symbol, "reason": f"{type(exc).__name__}: {exc}"})
            errors.append(record)
            continue
        scanned.append(symbol)
        provenance = {
            "first_bar_timestamp": loaded.first_bar_timestamp,
            "last_bar_timestamp": loaded.last_bar_timestamp,
            "last_bar_closed": loaded.last_bar_closed,
            "bars_used": loaded.bars_used,
            "data_source": loaded.data_source,
            "input_hash": loaded.input_hash,
        }
        strategy_results: dict[str, dict[str, Any]] = {}
        for spec in specs:
            try:
                result = spec.adapter(
                    symbol, spec.strategy_id, {spec.timeframe: loaded.frame}, direction="LONG", as_of=as_of,
                    run_id=run_id, strategy_version=spec.version, provenance=provenance,
                )
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                result = _error_result(run_id, symbol, spec.strategy_id, message, provenance)
                errors.append({"symbol": symbol, "strategy_id": spec.strategy_id, "scope": "strategy", "reason": type(exc).__name__, "message": str(exc)})
            payload = result.to_dict()
            strategy_results[spec.strategy_id] = payload
            if result.matched:
                candidates.append(payload)
        symbol_payloads[symbol] = {
            "schema_version": SCHEMA_VERSION, "run_id": run_id, "symbol": symbol,
            "as_of": loaded.last_bar_timestamp, "strategies": strategy_results,
        }

    completed_at = _iso_now()
    runtime_seconds = round(time.perf_counter() - started_perf, 6)
    strategy_summary: dict[str, dict[str, int]] = {}
    for strategy_id in strategies:
        results = [payload["strategies"][strategy_id] for payload in symbol_payloads.values()]
        strategy_summary[strategy_id] = {
            "evaluated": len(results),
            "entry_signals": sum(item["status"] == ScanStatus.ENTRY_SIGNAL.value for item in results),
            "rejected": sum(item["status"] == ScanStatus.REJECTED.value for item in results),
            "no_signal": sum(item["status"] == ScanStatus.NO_SIGNAL.value for item in results),
            "insufficient_data": sum(item["status"] == ScanStatus.INSUFFICIENT_DATA.value for item in results),
            "errors": sum(item["status"] == ScanStatus.ERROR.value for item in results),
        }
    candidate_symbols = sorted({item["symbol"] for item in candidates})
    summary = {
        "run_id": run_id,
        "symbols_requested": len(requested), "symbols_available": len(available),
        "symbols_scanned": len(scanned), "symbols_skipped": len(skipped),
        "strategies": strategy_summary,
        "unique_candidate_symbols": len(candidate_symbols), "candidate_symbols": candidate_symbols,
        "errors": len(errors), "runtime_seconds": runtime_seconds,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "started_at": started_at, "completed_at": completed_at,
        "direction": "LONG", "strategy_ids": list(strategies),
        "strategy_versions": {spec.strategy_id: spec.version for spec in specs},
        "symbol_universe_source": universe_source(),
        "requested_symbols": requested, "available_symbols": available, "scanned_symbols": scanned, "skipped_symbols": skipped,
        "requested_symbol_count": len(requested), "available_symbol_count": len(available), "scanned_symbol_count": len(scanned),
        "timeframe": "1D", "closed_bars_only": True, "data_source": DATA_SOURCE,
        "network_fallback_enabled": False, "scanner_version": SCANNER_VERSION,
        "errors_count": len(errors), "runtime_seconds": runtime_seconds,
        "source_code_hashes": {str(_STRATEGY_SOURCE.relative_to(Path(__file__).resolve().parents[2])): _source_hash(_STRATEGY_SOURCE)},
    }
    run_dir = None
    if write_artifacts:
        run_dir = write_run_artifacts(Path(runs_root or _DEFAULT_RUNS_ROOT), run_id, manifest=manifest, summary=summary, candidates=candidates, errors=errors, symbols=symbol_payloads)
    return {
        "run_id": run_id, "run_dir": str(run_dir) if run_dir else None,
        "manifest": manifest, "summary": summary, "candidates": candidates,
        "errors": errors, "symbols": symbol_payloads,
    }
