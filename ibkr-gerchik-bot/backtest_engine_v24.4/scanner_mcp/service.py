"""Thin artifact-safe service over the existing deterministic scanner API."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Iterable

from scanner.data_adapter import available_daily_symbols, configured_symbols, universe_source
from scanner.registry import PRODUCTION_STRATEGY_IDS, get_strategy_spec, list_strategy_specs
from scanner.runner import run_universe_scan as scanner_run_universe_scan
from src.data.bar_store import index_snapshot
from src.symbol_universe import normalize_stock_symbol

from .proposals import ProposalSizingInputs, build_candidate_proposal
from .live_proposals import LiveProposalError, build_live_candidate_proposal
from .schemas import ScannerMCPError, validate_run_id, validate_strategy_token
from live_engine import CanonicalLiveEngine, PersistedSessionAdapter, load_closed_daily_frame

LOGGER = logging.getLogger("strategy_scanner.service")
ENGINE_ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = ENGINE_ROOT / "scanner" / "runs"


class StrategyScannerService:
    """Synchronous v0.2 service; all strategy work delegates to scanner.runner."""

    def __init__(self, runs_root: Path | str | None = None):
        self.runs_root = Path(runs_root or RUNS_ROOT)

    def list_strategies(self) -> dict[str, Any]:
        strategies = []
        for spec in list_strategy_specs():
            strategies.append({
                "strategy_id": spec.strategy_id,
                "display_name": spec.display_name,
                "version": spec.version,
                "production_enabled": spec.production_enabled,
                "supported_directions": list(spec.directions),
                "required_timeframe": spec.timeframe,
                "required_fields": list(spec.required_fields),
                "minimum_bars": spec.minimum_bars,
            })
        return {"namespace": "strategy_scanner", "count": len(strategies), "strategies": strategies}

    def list_available_symbols(self) -> dict[str, Any]:
        requested = configured_symbols()
        available = available_daily_symbols(requested)
        available_set = set(available)
        missing = [symbol for symbol in requested if symbol not in available_set]
        index = index_snapshot()
        coverage = []
        for symbol in available:
            metadata = index.get(symbol, {}).get("daily", {})
            coverage.append({
                "symbol": symbol,
                "rows": metadata.get("rows"),
                "last_bar": metadata.get("last_bar"),
                "updated_at": metadata.get("updated_at"),
            })
        return {
            "universe_source": universe_source(),
            "requested_count": len(requested),
            "available_count": len(available),
            "missing_count": len(missing),
            "available_symbols": available,
            "missing_symbols": [{"symbol": symbol, "reason": "NO_PERSISTED_DAILY_DATA"} for symbol in missing],
            "coverage": coverage,
            "closed_bars_only": True,
            "network_fallback_enabled": False,
        }

    def run_universe_scan(
        self,
        *,
        strategies: Iterable[str] | None = None,
        symbols: Iterable[str] | str | None = "ALL",
        direction: str = "LONG",
        as_of: str | None = None,
    ) -> dict[str, Any]:
        selected_strategies = self._strategies(strategies)
        selected_symbols = self._symbols(symbols)
        normalized_direction = str(direction or "").strip().upper()
        if normalized_direction != "LONG":
            raise ScannerMCPError("INVALID_DIRECTION", "Strategy Scanner v0.2 supports LONG only", {"direction": direction})
        symbol_count = len(configured_symbols()) if selected_symbols is None else len(selected_symbols)
        LOGGER.info(
            "Harness request -> MCP tool run_universe_scan -> scanner runner start strategies=%s symbols=%s direction=%s",
            selected_strategies, symbol_count, normalized_direction,
        )
        try:
            result = scanner_run_universe_scan(
                symbols=selected_symbols,
                strategy_ids=selected_strategies,
                direction=normalized_direction,
                as_of=as_of,
                runs_root=self.runs_root,
                write_artifacts=True,
            )
        except ScannerMCPError:
            raise
        except Exception as exc:
            LOGGER.exception("Strategy scan failed")
            raise ScannerMCPError("SCAN_FAILED", "Deterministic scanner execution failed", {"type": type(exc).__name__}) from exc
        manifest = result["manifest"]
        summary = result["summary"]
        candidate_count = sum(item["entry_signals"] for item in summary["strategies"].values())
        LOGGER.info(
            "scanner runner -> artifacts -> MCP response run_id=%s runtime=%s scanned=%s candidates=%s errors=%s",
            result["run_id"], summary["runtime_seconds"], summary["symbols_scanned"], candidate_count, summary["errors"],
        )
        return {
            "run_id": result["run_id"],
            "status": "COMPLETED",
            "started_at": manifest["started_at"],
            "completed_at": manifest["completed_at"],
            "runtime_seconds": summary["runtime_seconds"],
            "symbols_scanned": summary["symbols_scanned"],
            "candidate_count": candidate_count,
            "error_count": summary["errors"],
            "summary_reference": {"tool": "get_scan_summary", "run_id": result["run_id"]},
        }

    def run_live_universe_scan(self, *, strategies: Iterable[str] | None = None, symbols: Iterable[str] | str | None = "ALL", direction: str = "LONG", as_of: str | None = None, timeframe: str = "5m") -> dict[str, Any]:
        """Read-only two-stage preview over persisted data; never writes scan artifacts."""
        if str(direction).upper() != "LONG":
            raise ScannerMCPError("INVALID_DIRECTION", "Canonical live preview supports LONG only", {"direction": direction})
        selected_strategies = tuple(str(x).lower() for x in (strategies or ("lp1", "lp2", "prb1", "prb2")))
        if any(x not in {"lp1","lp2","prb1","prb2"} for x in selected_strategies):
            raise ScannerMCPError("INVALID_STRATEGY", "Unsupported canonical live strategy", {"strategies": list(selected_strategies)})
        selected_symbols = self._symbols(symbols)
        if selected_symbols is None: selected_symbols = configured_symbols()
        started=time.perf_counter(); results=[]; errors=[]
        for symbol in selected_symbols:
            try:
                daily=load_closed_daily_frame(symbol,as_of=as_of)
                session=PersistedSessionAdapter().load(symbol,timeframe=timeframe,as_of=as_of)
                from live_engine import detect_daily_setups, evaluate_live_setup
                setups=detect_daily_setups(symbol,daily,strategies=selected_strategies,current_session_date=session.session_date)
                if not setups:
                    results.append({"symbol":symbol,"status":"NO_SETUP","session":session.to_dict(),"setups":[],"triggers":[]})
                    continue
                triggers=[]
                for setup in setups:
                    previous_close=None
                    if len(daily): previous_close=float(daily.iloc[-1]["Close"])
                    triggers.append(evaluate_live_setup(setup,session,previous_close=previous_close,historical_daily_bars=daily).to_dict())
                results.append({"symbol":symbol,"status":"READY" if any(x["status"]=="READY" for x in triggers) else "WATCH" if any(x["status"]=="WATCH" for x in triggers) else triggers[0]["status"],"session":session.to_dict(),"setups":[x.to_dict() for x in setups],"triggers":triggers})
            except Exception as exc:
                LOGGER.warning("live preview failed symbol=%s type=%s",symbol,type(exc).__name__)
                errors.append({"symbol":symbol,"type":type(exc).__name__})
                results.append({"symbol":symbol,"status":"ERROR","setups":[],"triggers":[]})
        counts={key:sum(1 for item in results if item.get("status")==key) for key in ("READY","WATCH","REJECTED","NO_SETUP","STALE_DATA","INSUFFICIENT_DATA","ERROR")}
        freshness_counts={key:sum(1 for item in results if str((item.get("session") or {}).get("freshness", ""))==key) for key in ("FRESH","DELAYED_USABLE","STALE","MISSING")}
        return {"tool":"run_live_universe_scan","schema_version":"1.0","direction":"LONG","strategies":list(selected_strategies),"symbols_requested":len(selected_symbols),"symbols_available":sum(1 for x in results if x.get("status")!="ERROR"),"counts":counts,"freshness_counts":freshness_counts,"runtime_seconds":time.perf_counter()-started,"errors":errors,"results":results,"read_only":True,"legacy_scanner_unchanged":True}

    def get_live_candidate_proposals(
        self,
        *,
        account_capital: float | None = None,
        max_capital_allocation_pct: float | None = None,
        max_loss_risk_pct: float | None = None,
        available_funds: float | None = None,
        max_position_value: float | None = None,
        slippage_per_share: float = 0.0,
        fees_per_share: float = 0.0,
        include_execution_costs: bool = False,
        strategies: Iterable[str] | None = None,
        symbols: Iterable[str] | str | None = "ALL",
        as_of: str | None = None,
        timeframe: str = "5m",
    ) -> dict[str, Any]:
        """Run the canonical live scan and plan READY results only."""
        scan = self.run_live_universe_scan(strategies=strategies, symbols=symbols, as_of=as_of, timeframe=timeframe)
        sizing = ProposalSizingInputs(
            account_capital=account_capital,
            max_capital_allocation_pct=max_capital_allocation_pct,
            max_loss_risk_pct=max_loss_risk_pct,
            available_funds=available_funds,
            max_position_value=max_position_value,
            slippage_per_share=slippage_per_share,
            fees_per_share=fees_per_share,
            include_execution_costs=include_execution_costs,
        )
        proposals: list[dict[str, Any]] = []
        watch: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        counts = {"WATCH": 0, "READY": 0, "REJECTED": 0, "STALE_DATA": 0, "NO_SETUP": 0, "INVALID": 0, "ERROR": 0}
        for symbol_result in scan.get("results", []):
            symbol = symbol_result.get("symbol")
            symbol_status = str(symbol_result.get("status", "ERROR"))
            if symbol_status == "ERROR":
                counts["ERROR"] += 1
            if not symbol_result.get("triggers") and symbol_status == "NO_SETUP":
                counts["NO_SETUP"] += 1
            for trigger in symbol_result.get("triggers", []):
                trigger_status = str(trigger.get("status", ""))
                if trigger_status in counts:
                    counts[trigger_status] += 1
                if trigger_status == "WATCH":
                    watch.append({"symbol": symbol, "setup": trigger.get("setup"), "session": trigger.get("session"), "trigger": trigger.get("trigger")})
                elif trigger_status == "READY":
                    try:
                        proposal = build_live_candidate_proposal(trigger, sizing).to_dict()
                        proposals.append(proposal)
                    except LiveProposalError as exc:
                        errors.append({"symbol": symbol, "code": exc.code, "message": exc.message})
        actionable = sum(item.get("proposal_status") == "ACTIONABLE" for item in proposals)
        non_actionable = sum(item.get("proposal_status") == "NON_ACTIONABLE" for item in proposals)
        invalid = sum(item.get("proposal_status") == "INVALID" for item in proposals)
        return {
            "tool": "get_live_candidate_proposals", "schema_version": "1.0",
            "scan": {key: scan.get(key) for key in ("tool", "strategies", "symbols_requested", "symbols_available", "runtime_seconds", "counts", "freshness_counts", "errors")},
            "watch_count": len(watch), "ready_count": counts["READY"], "stale_count": counts["STALE_DATA"],
            "freshness_counts": scan.get("freshness_counts", {}), "delayed_usable_count": scan.get("freshness_counts", {}).get("DELAYED_USABLE", 0),
            "rejected_count": counts["REJECTED"], "no_setup_count": counts["NO_SETUP"],
            "proposal_count": len(proposals), "actionable_count": actionable,
            "non_actionable_count": non_actionable, "invalid_count": invalid,
            "sizing_inputs": sizing.to_dict(), "watch": watch, "proposals": proposals, "errors": errors,
            "read_only": True, "planner_invoked_for": "READY only",
        }

    def preview_live_setups(self, symbol: str, *, strategies: Iterable[str] | None = None, as_of: str | None = None, timeframe: str = "5m") -> dict[str, Any]:
        """Read-only canonical setup/trigger preview; never starts the legacy scanner."""
        normalized = self._normalize_symbol(symbol)
        selected = tuple(str(x).lower() for x in (strategies or ("lp1", "lp2", "prb1", "prb2")))
        if any(x not in {"lp1", "lp2", "prb1", "prb2"} for x in selected):
            raise ScannerMCPError("INVALID_STRATEGY", "Unsupported canonical live strategy", {"strategies": list(selected)})
        try:
            daily = load_closed_daily_frame(normalized, as_of=as_of)
            session = PersistedSessionAdapter().load(normalized, timeframe=timeframe, as_of=as_of)
            from live_engine import detect_daily_setups, evaluate_live_setup
            setups = detect_daily_setups(normalized, daily, strategies=selected, current_session_date=session.session_date)
            if not setups:
                return {"schema_version":"1.0","symbol":normalized,"status":"NO_SETUP","session":session.to_dict(),"setups":[],"triggers":[],"read_only":True}
            previous_close=float(daily.iloc[-1]["Close"]) if len(daily) else None
            triggers=[evaluate_live_setup(setup,session,previous_close=previous_close,historical_daily_bars=daily).to_dict() for setup in setups]
            status="READY" if any(x["status"]=="READY" for x in triggers) else "WATCH" if any(x["status"]=="WATCH" for x in triggers) else triggers[0]["status"]
            return {"schema_version":"1.0","symbol":normalized,"status":status,"session":session.to_dict(),"setups":[x.to_dict() for x in setups],"triggers":triggers,"read_only":True}
        except ScannerMCPError:
            raise
        except Exception as exc:
            raise ScannerMCPError("LIVE_PREVIEW_FAILED", "Canonical live preview failed", {"type": type(exc).__name__}) from exc

    def get_scan_summary(self, run_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        summary = self._read_json(run_dir / "summary.json")
        if summary.get("run_id") != run_dir.name or not isinstance(summary.get("strategies"), dict):
            raise ScannerMCPError("ARTIFACT_INVALID", "Scan summary is invalid", {"run_id": run_dir.name})
        return summary

    def get_strategy_candidates(
        self,
        run_id: str,
        *,
        strategy: str | None = None,
        status: str | None = "ENTRY_SIGNAL",
        symbol: str | None = None,
        limit: int = 50,
        page: int = 1,
    ) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        selected_strategy = self._strategy(strategy) if strategy else None
        selected_symbol = self._normalize_symbol(symbol) if symbol else None
        normalized_status = str(status or "ENTRY_SIGNAL").strip().upper()
        if limit < 1 or limit > 200 or page < 1:
            raise ScannerMCPError("ARTIFACT_INVALID", "Pagination must use limit 1..200 and page >= 1", {"limit": limit, "page": page})
        payload = self._read_json(run_dir / "candidates.json")
        records = payload.get("candidates")
        if not isinstance(records, list):
            raise ScannerMCPError("ARTIFACT_INVALID", "Candidate artifact is invalid", {"run_id": run_dir.name})
        filtered = [
            item for item in records
            if item.get("matched") is True
            and item.get("status") == normalized_status
            and (selected_strategy is None or item.get("strategy_id") == selected_strategy)
            and (selected_symbol is None or item.get("symbol") == selected_symbol)
        ]
        offset = (page - 1) * limit
        compact = [self._compact_candidate(item) for item in filtered[offset: offset + limit]]
        return {
            "run_id": run_dir.name,
            "filters": {"strategy": selected_strategy, "status": normalized_status, "symbol": selected_symbol},
            "total": len(filtered), "page": page, "limit": limit, "candidates": compact,
        }

    def get_candidate_proposals(
        self,
        run_id: str,
        *,
        account_capital: float | None = None,
        max_capital_allocation_pct: float | None = None,
        max_loss_risk_pct: float | None = None,
        available_funds: float | None = None,
        max_position_value: float | None = None,
        slippage_per_share: float = 0.0,
        fees_per_share: float = 0.0,
        include_execution_costs: bool = False,
        strategies: Iterable[str] | None = None,
        symbols: Iterable[str] | str | None = None,
        limit: int = 200,
        page: int = 1,
    ) -> dict[str, Any]:
        """Size existing ENTRY_SIGNAL artifacts without scanning or mutating them."""
        run_dir = self._run_dir(run_id)
        if limit < 1 or limit > 200 or page < 1:
            raise ScannerMCPError("ARTIFACT_INVALID", "Pagination must use limit 1..200 and page >= 1", {"limit": limit, "page": page})
        selected_strategies = None if strategies is None else {self._strategy(item) for item in strategies}
        selected_symbols = None
        if symbols is not None and not (isinstance(symbols, str) and symbols.strip().upper() == "ALL"):
            raw_symbols = [symbols] if isinstance(symbols, str) else list(symbols)
            if not raw_symbols:
                raise ScannerMCPError("INVALID_SYMBOL", "At least one symbol or ALL is required")
            selected_symbols = {self._normalize_symbol(item) for item in raw_symbols}
        sizing = ProposalSizingInputs(
            account_capital=account_capital,
            max_capital_allocation_pct=max_capital_allocation_pct,
            max_loss_risk_pct=max_loss_risk_pct,
            available_funds=available_funds,
            max_position_value=max_position_value,
            slippage_per_share=slippage_per_share,
            fees_per_share=fees_per_share,
            include_execution_costs=include_execution_costs,
        )
        payload = self._read_json(run_dir / "candidates.json")
        records = payload.get("candidates")
        if not isinstance(records, list):
            raise ScannerMCPError("ARTIFACT_INVALID", "Candidate artifact is invalid", {"run_id": run_dir.name})
        eligible = [
            item for item in records
            if item.get("matched") is True and item.get("status") == "ENTRY_SIGNAL"
            and str(item.get("direction", "")).upper() == "LONG"
            and (selected_strategies is None or item.get("strategy_id") in selected_strategies)
            and (selected_symbols is None or item.get("symbol") in selected_symbols)
        ]
        offset = (page - 1) * limit
        proposals: list[dict[str, Any]] = []
        for item in eligible[offset:offset + limit]:
            try:
                proposals.append(build_candidate_proposal({**item, "run_id": run_dir.name}, sizing).to_dict())
            except (TypeError, ValueError, KeyError) as exc:
                raise ScannerMCPError("PROPOSAL_INVALID", "Candidate proposal could not be built", {"symbol": item.get("symbol"), "strategy": item.get("strategy_id"), "reason": str(exc)}) from exc
        counts = {"ACTIONABLE": 0, "NON_ACTIONABLE": 0, "INVALID": 0}
        for proposal in proposals:
            counts[proposal["proposal_status"]] = counts.get(proposal["proposal_status"], 0) + 1
        return {
            "run_id": run_dir.name,
            "candidate_count": len(eligible),
            "actionable_count": counts["ACTIONABLE"],
            "non_actionable_count": counts["NON_ACTIONABLE"],
            "invalid_count": counts["INVALID"],
            "page": page,
            "limit": limit,
            "filters": {"strategies": sorted(selected_strategies) if selected_strategies is not None else None, "symbols": sorted(selected_symbols) if selected_symbols is not None else None},
            "sizing_inputs": sizing.to_dict(),
            "proposals": proposals,
        }

    def get_symbol_scan(self, run_id: str, symbol: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        normalized = self._normalize_symbol(symbol)
        path = run_dir / "symbols" / f"{normalized}.json"
        if not path.is_file():
            raise ScannerMCPError("SYMBOL_NOT_IN_RUN", "Symbol was not evaluated in this scan run", {"run_id": run_dir.name, "symbol": normalized})
        payload = self._read_json(path)
        if payload.get("run_id") != run_dir.name or payload.get("symbol") != normalized or not isinstance(payload.get("strategies"), dict):
            raise ScannerMCPError("ARTIFACT_INVALID", "Symbol scan artifact is invalid", {"run_id": run_dir.name, "symbol": normalized})
        return payload

    def list_scan_runs(self, limit: int = 20) -> dict[str, Any]:
        if limit < 1 or limit > 100:
            raise ScannerMCPError("ARTIFACT_INVALID", "Run-list limit must be 1..100", {"limit": limit})
        if not self.runs_root.exists():
            return {"count": 0, "runs": []}
        runs = []
        for path in self.runs_root.iterdir():
            if not path.is_dir():
                continue
            try:
                validate_run_id(path.name)
                manifest = self._read_json(path / "manifest.json")
                summary = self._read_json(path / "summary.json")
                runs.append({
                    "run_id": path.name,
                    "started_at": manifest.get("started_at"),
                    "completed_at": manifest.get("completed_at"),
                    "direction": manifest.get("direction"),
                    "strategies": manifest.get("strategy_ids", []),
                    "symbol_count": summary.get("symbols_scanned"),
                    "candidate_count": sum(int(item.get("entry_signals", 0)) for item in summary.get("strategies", {}).values()),
                    "error_count": summary.get("errors"),
                    "runtime_seconds": summary.get("runtime_seconds"),
                })
            except ScannerMCPError:
                LOGGER.warning("Skipping invalid scanner run artifact directory: %s", path.name)
        runs.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
        return {"count": min(len(runs), limit), "runs": runs[:limit]}

    def _strategies(self, strategies: Iterable[str] | None) -> list[str]:
        values = list(strategies) if strategies is not None else list(PRODUCTION_STRATEGY_IDS)
        if not values:
            raise ScannerMCPError("INVALID_STRATEGY", "At least one production strategy is required")
        return list(dict.fromkeys(self._strategy(value) for value in values))

    def _strategy(self, strategy_id: str) -> str:
        value = validate_strategy_token(strategy_id)
        try:
            return get_strategy_spec(value).strategy_id
        except KeyError as exc:
            raise ScannerMCPError("INVALID_STRATEGY", "Strategy is not production-allowlisted", {"strategy_id": value}) from exc

    def _symbols(self, symbols: Iterable[str] | str | None) -> list[str] | None:
        if symbols is None or (isinstance(symbols, str) and symbols.strip().upper() == "ALL"):
            return None
        raw = [symbols] if isinstance(symbols, str) else list(symbols)
        if not raw:
            raise ScannerMCPError("INVALID_SYMBOL", "At least one symbol or ALL is required")
        configured = set(configured_symbols())
        available = set(available_daily_symbols(configured))
        normalized = []
        for item in raw:
            symbol = self._normalize_symbol(item)
            if symbol not in configured:
                raise ScannerMCPError("INVALID_SYMBOL", "Symbol is not in the configured scanner universe", {"symbol": symbol})
            if symbol not in available:
                raise ScannerMCPError("INVALID_SYMBOL", "Symbol has no persisted daily data", {"symbol": symbol})
            if symbol not in normalized:
                normalized.append(symbol)
        return normalized

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        try:
            return normalize_stock_symbol(symbol)
        except (TypeError, ValueError) as exc:
            raise ScannerMCPError("INVALID_SYMBOL", "Invalid scanner symbol", {"symbol": str(symbol)}) from exc

    def _run_dir(self, run_id: str) -> Path:
        value = validate_run_id(run_id)
        root = self.runs_root.resolve()
        candidate = (root / value).resolve()
        if candidate.parent != root or not candidate.is_dir():
            raise ScannerMCPError("RUN_NOT_FOUND", "Scan run was not found", {"run_id": value})
        return candidate

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ScannerMCPError("ARTIFACT_INVALID", "Scanner artifact is missing or invalid", {"artifact": path.name}) from exc
        if not isinstance(payload, dict):
            raise ScannerMCPError("ARTIFACT_INVALID", "Scanner artifact must be a JSON object", {"artifact": path.name})
        return payload

    @staticmethod
    def _compact_candidate(item: dict[str, Any]) -> dict[str, Any]:
        signal = item.get("signal") or {}
        trade_plan = item.get("trade_plan") or {}
        quality = item.get("quality") or {}
        evidence = item.get("evidence") or {}
        level = item.get("level") or {}
        market = item.get("market") or {}
        setup_id = item.get("setup_id") or item.get("candidate_id")
        provenance = item.get("provenance") or {}
        plan_hash = item.get("plan_hash") or (provenance.get("plan_hash") if isinstance(provenance, dict) else None)
        if item.get("status") == "ENTRY_SIGNAL":
            LOGGER.info("ENTRY_SIGNAL detected run_id=%s symbol=%s strategy=%s", item.get("run_id"), item.get("symbol"), item.get("strategy_id"))
            if setup_id:
                LOGGER.info("canonical setup_id found setup_id=%s", setup_id)
            else:
                LOGGER.warning("canonical setup_id missing run_id=%s symbol=%s", item.get("run_id"), item.get("symbol"))
            if plan_hash:
                LOGGER.info("canonical plan_hash found plan_hash=%s", plan_hash)
            else:
                LOGGER.warning("canonical plan_hash missing run_id=%s symbol=%s", item.get("run_id"), item.get("symbol"))
        return {
            "setup_id": setup_id, "plan_hash": plan_hash,
            "canonical_identity_status": "available" if setup_id and plan_hash else "missing",
            "symbol": item.get("symbol"), "strategy_id": item.get("strategy_id"),
            "direction": item.get("direction"), "status": item.get("status"),
            "matched": item.get("matched"), "pattern": signal.get("pattern"),
            "signal_bar": signal.get("signal_bar"),
            "signal_bar_date": signal.get("signal_bar_date", signal.get("signal_bar")),
            "signal_bar_index": signal.get("signal_bar_index"),
            "triggered": signal.get("triggered"),
            "level": level.get("price"), "level_type": level.get("level_type"),
            "atr": market.get("atr"),
            "entry": trade_plan.get("entry"), "stop": trade_plan.get("stop"),
            "target": trade_plan.get("target"), "rr": trade_plan.get("rr"),
            "reward_risk": trade_plan.get("reward_risk", trade_plan.get("rr")),
            "score": quality.get("score"),
            "evidence": {key: evidence.get(key) for key in ("gap_atr", "chase_atr", "overextended", "regime_ok", "rsi_ok")},
            "pattern_definition": item.get("pattern_definition") or {},
            "pattern_bars": item.get("pattern_bars") or {},
            "trigger_evidence": item.get("trigger_evidence") or {},
            "rejection_evidence": item.get("rejection_evidence") or [],
            "chart_annotations": item.get("chart_annotations") or [],
            "evidence_reference": {"tool": "get_symbol_scan", "run_id": item.get("run_id"), "symbol": item.get("symbol")},
        }
