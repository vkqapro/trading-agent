"""Manual/AUTO Decision Lab controller owned by the backend worker."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.config import SETTINGS
from src.decision.agent import AgentRuntimeContext, AutonomousGerchikAgent, AgentResult
from src.decision.strategy_control import (
    claim_queued_run,
    create_run,
    load_control,
    load_snapshot,
    list_runs,
    pending_manual_runs,
    save_snapshot,
    update_run,
)
from src.decision.strategy_sources import (
    DEFAULT_REGISTRY,
    StrategyScanContext,
    StrategySnapshot,
    StrategySourceRegistry,
    snapshot_to_candidate,
    snapshot_to_trade_signal,
)




@dataclass(frozen=True)
class AnalysisExecutionContext:
    order_manager: Any
    market_data: Any
    account_equity: float
    cash_available: float
    current_positions: list[Mapping[str, object]]
    open_risk_amount: float
    market_open: bool


def _snapshot_payload(
    snapshots: dict[str, list[StrategySnapshot]],
    scan: StrategyScanContext,
    source_status: Mapping[str, str] | None = None,
) -> dict[str, object]:
    return {
        "scan_id": scan.scan_id,
        "completed_at": scan.completed_at,
        "source_counts": {source: len(items) for source, items in snapshots.items()},
        "source_status": dict(source_status or {
            source: "READY" if items else "NO_CANDIDATES"
            for source, items in snapshots.items()
        }),
        "sources": {
            source: [item.to_dict() for item in items]
            for source, items in snapshots.items()
        },
    }


def _snapshots_from_payload(payload: Mapping[str, object]) -> dict[str, list[StrategySnapshot]]:
    raw_sources = payload.get("sources", {})
    if not isinstance(raw_sources, Mapping):
        return {}
    result: dict[str, list[StrategySnapshot]] = {}
    for source, raw_items in raw_sources.items():
        if not isinstance(raw_items, list):
            continue
        items: list[StrategySnapshot] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                continue
            try:
                items.append(StrategySnapshot(**dict(raw_item)))
            except (TypeError, ValueError):
                continue
        result[str(source)] = items
    return result


class StrategyAnalysisController:
    """Serializes bounded LLM analysis runs without owning a broker client."""

    def __init__(
        self,
        *,
        runtime_dir: Path | None = None,
        registry: StrategySourceRegistry = DEFAULT_REGISTRY,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self.runtime_dir = Path(runtime_dir or SETTINGS.paths.runtime_dir)
        self.registry = registry
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="decision-lab-run")
        self._lock = threading.Lock()

    def publish_completed_scan(
        self,
        scan: StrategyScanContext,
        execution_context: AnalysisExecutionContext,
    ) -> dict[str, object]:
        """Persist one coherent source snapshot, then schedule eligible work."""
        # A MANUAL request refers to the latest snapshot that existed when the
        # operator pressed RUN ANALYSIS. Drain that cached snapshot before
        # replacing it with the next normal worker scan. This keeps MANUAL
        # analysis from triggering a fetch or scanner of its own.
        self.drain_pending_manual(execution_context)
        snapshots, source_status = self.registry.all_snapshots_with_status(scan)
        payload = _snapshot_payload(snapshots, scan, source_status)
        save_snapshot(payload, runtime_dir=self.runtime_dir)

        # MANUAL requests are immutable at creation time and consume the latest
        # completed snapshot once. AUTO uses the current control and this scan
        # identity only; changing controls affects the next scan.
        for run in pending_manual_runs(runtime_dir=self.runtime_dir):
            selected = str(run.get("strategy_selection") or "")
            requested_scan_id = str(run.get("scan_id") or "")
            if requested_scan_id and requested_scan_id != scan.scan_id:
                continue
            selected_items = (
                self._group_all_snapshots(snapshots)
                if selected == "all"
                else snapshots.get(selected, [])
            )
            self._schedule(run, selected_items, execution_context, snapshots)

        control = load_control(runtime_dir=self.runtime_dir, registry=self.registry)
        if control.get("analysis_mode") == "auto":
            source = str(control.get("strategy_source") or "")
            run = create_run(
                analysis_mode="auto",
                strategy_source=source,
                prompt_preset_id=str(control.get("prompt_preset_id") or "") or None,
                scan_id=scan.scan_id,
                snapshot_timestamp=scan.completed_at,
                runtime_dir=self.runtime_dir,
                registry=self.registry,
            )
            # create_run returns the already completed/idempotent run when this
            # scan/source pair was seen before. Only schedule a new QUEUED run.
            if run.get("status") == "QUEUED":
                selected_items = (
                    self._group_all_snapshots(snapshots)
                    if source == "all"
                    else snapshots.get(source, [])
                )
                self._schedule(run, selected_items, execution_context, snapshots)
        return payload

    def drain_pending_manual(self, execution_context: AnalysisExecutionContext) -> int:
        """Schedule queued MANUAL runs from the persisted latest snapshot."""
        scheduled = 0
        for run in pending_manual_runs(runtime_dir=self.runtime_dir):
            requested_scan_id = str(run.get("scan_id") or "")
            payload = load_snapshot(
                runtime_dir=self.runtime_dir,
                scan_id=requested_scan_id or None,
            )
            if not isinstance(payload, Mapping):
                continue
            snapshots = _snapshots_from_payload(payload)
            selected = str(run.get("strategy_selection") or "")
            selected_items = (
                self._group_all_snapshots(snapshots)
                if selected == "all"
                else snapshots.get(selected, [])
            )
            self._schedule(run, selected_items, execution_context, snapshots)
            scheduled += 1
        return scheduled

    def schedule_auto_from_snapshot(
        self,
        payload: Mapping[str, object],
        *,
        strategy_source: str,
        execution_context: AnalysisExecutionContext,
        prompt_preset_id: str | None = None,
    ) -> dict[str, object] | None:
        """Create and schedule one AUTO analysis from current source data.

        This consumes an immutable application snapshot. It never starts a
        market scan and never acquires a trading-session lock.
        """
        source = str(strategy_source or "").strip().lower()
        self.registry.validate_selection(source)
        snapshot_id = str(payload.get("analysis_snapshot_id") or payload.get("scan_id") or "").strip()
        snapshot_timestamp = ""
        metadata = payload.get("source_metadata", {})
        if isinstance(metadata, Mapping):
            if source == "all":
                timestamps = [
                    str(item.get("source_timestamp") or "")
                    for item in metadata.values()
                    if isinstance(item, Mapping) and item.get("source_timestamp")
                ]
                snapshot_timestamp = max(timestamps) if timestamps else ""
            else:
                selected = metadata.get(source, {})
                if isinstance(selected, Mapping):
                    snapshot_timestamp = str(selected.get("source_timestamp") or "")
        if not snapshot_id or not snapshot_timestamp:
            return None
        run = create_run(
            analysis_mode="auto",
            strategy_source=source,
            prompt_preset_id=prompt_preset_id,
            scan_id=snapshot_id,
            snapshot_timestamp=snapshot_timestamp,
            runtime_dir=self.runtime_dir,
            registry=self.registry,
        )
        if run.get("status") == "QUEUED":
            snapshots = _snapshots_from_payload(payload)
            selected_items = (
                self._group_all_snapshots(snapshots)
                if source == "all"
                else snapshots.get(source, [])
            )
            self._schedule(run, selected_items, execution_context, snapshots)
        return run

    def _schedule(
        self,
        run: Mapping[str, object],
        selected_snapshots: list[StrategySnapshot],
        execution_context: AnalysisExecutionContext,
        all_snapshots: dict[str, list[StrategySnapshot]],
    ) -> None:
        run_id = str(run.get("run_id") or "")
        if not run_id:
            return
        if not self._claim_queued(run_id):
            return
        self.executor.submit(
            self._execute,
            run_id,
            str(run.get("strategy_selection") or ""),
            selected_snapshots,
            execution_context,
            all_snapshots,
        )

    def _claim_queued(self, run_id: str) -> bool:
        with self._lock:
            current = claim_queued_run(run_id, runtime_dir=self.runtime_dir)
            return bool(current and current.get("status") == "RUNNING")

    @staticmethod
    def _group_all_snapshots(
        all_snapshots: dict[str, list[StrategySnapshot]],
    ) -> list[StrategySnapshot]:
        grouped: dict[str, list[StrategySnapshot]] = {}
        for source, items in all_snapshots.items():
            for item in items:
                grouped.setdefault(item.symbol, []).append(item)
        selected: list[StrategySnapshot] = []
        for symbol, items in sorted(grouped.items()):
            executable = next(
                (item for item in items if item.candidate_class == "EXECUTABLE_ENTRY_SIGNAL"),
                items[0],
            )
            evidence = [item.to_dict() for item in items]
            selected.append(
                StrategySnapshot(
                    **{
                        **executable.to_dict(),
                        "metadata": {**dict(executable.metadata), "source_evidence": evidence},
                    }
                )
            )
        return selected

    def _execute(
        self,
        run_id: str,
        source: str,
        selected_snapshots: list[StrategySnapshot],
        execution_context: AnalysisExecutionContext,
        all_snapshots: dict[str, list[StrategySnapshot]],
    ) -> None:
        try:
            run_record = next((item for item in list_runs(runtime_dir=self.runtime_dir, limit=200) if item.get("run_id") == run_id), {})
            prompt_preset_id = str(run_record.get("prompt_preset_id") or "") or None
            snapshots = self._group_all_snapshots(all_snapshots) if source == "all" else selected_snapshots
            agent = AutonomousGerchikAgent(order_manager=execution_context.order_manager)
            results: list[AgentResult] = []
            for snapshot in snapshots:
                candidate, allowed_actions = snapshot_to_candidate(snapshot)
                context = AgentRuntimeContext(
                    account_equity=execution_context.account_equity,
                    cash_available=execution_context.cash_available,
                    current_positions=tuple(dict(item) for item in execution_context.current_positions),
                    open_risk_amount=execution_context.open_risk_amount,
                    market_open=execution_context.market_open,
                    broker_connected=bool(getattr(getattr(execution_context.order_manager, "broker", None), "is_connected", False)),
                    current_price=snapshot.price,
                    market_context={
                        "strategy_source": snapshot.source,
                        "scan_id": snapshot.scan_id,
                        "analysis_snapshot_id": snapshot.scan_id,
                        "analysis_run_id": run_id,
                        "source_signal": snapshot.signal_state,
                        "candidate_class": snapshot.candidate_class,
                        "source_evidence": snapshot.metadata.get("source_evidence", []),
                        "prompt_preset_id": prompt_preset_id,
                    },
                    isolated_paper=agent.mode.value == "paper_autonomous",
                )
                # process_candidate is the existing authorization path. The
                # allowed action menu prevents analysis-only sources from ever
                # entering risk or execution with invented prices.
                results.append(
                    agent.process_candidate(
                        candidate,
                        context=context,
                        signal=snapshot_to_trade_signal(snapshot),
                        allowed_actions=allowed_actions,
                    )
                )

            model_actions = [
                str(getattr(result, "model_action", None) or getattr(result, "action", ""))
                for result in results
                if str(getattr(result, "model_action", None) or getattr(result, "action", "")) in {"ENTER", "WAIT", "REJECT"}
            ]
            origins = [str(getattr(result, "decision_origin", "")) for result in results]
            provider_statuses = [str(getattr(result, "provider_status", "") or "").upper() for result in results]
            not_applicable_count = sum(str(getattr(result, "status", "")) == "not_applicable" for result in results)
            counts = {
                "candidate_count": len(snapshots),
                "applicable_candidate_count": len(snapshots) - not_applicable_count,
                "not_applicable_count": not_applicable_count,
                "decision_count": len(results),
                "provider_success_count": sum(status == "SUCCESS" for status in provider_statuses),
                "provider_failure_count": sum(
                    status in {"FAILED", "INVALID_RESPONSE", "NOT_CALLED"} and status != "NOT_CALLED"
                    for status in provider_statuses
                ),
                "enter_count": sum(action == "ENTER" for action in model_actions),
                "wait_count": sum(action == "WAIT" for action in model_actions),
                "reject_count": sum(action == "REJECT" for action in model_actions),
                "execution_count": sum(bool(getattr(result, "execution", None)) for result in results),
                "model_enter_count": sum(action == "ENTER" for action in model_actions),
                "model_wait_count": sum(action == "WAIT" for action in model_actions),
                "model_reject_count": sum(action == "REJECT" for action in model_actions),
                "system_veto_count": sum(origin in {"SYSTEM_VETO", "ANALYSIS_ONLY_VETO", "STARTUP_VETO"} for origin in origins),
                "risk_veto_count": sum(origin == "RISK_VETO" for origin in origins),
            }
            statuses = [result.status for result in results]
            failed = [result for result in results if result.status in {"provider_error", "audit_error", "reconciliation_required"}]
            update_run(
                run_id,
                runtime_dir=self.runtime_dir,
                status="PARTIAL" if failed and results else "COMPLETED",
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=";".join(sorted({reason for result in failed for reason in result.reasons})) if failed else None,
                statuses=statuses,
                **counts,
            )
        except Exception as exc:
            update_run(
                run_id,
                runtime_dir=self.runtime_dir,
                status="FAILED",
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=type(exc).__name__,
            )


_CONTROLLER: StrategyAnalysisController | None = None
_CONTROLLER_LOCK = threading.Lock()


def get_strategy_analysis_controller() -> StrategyAnalysisController:
    global _CONTROLLER
    with _CONTROLLER_LOCK:
        if _CONTROLLER is None:
            _CONTROLLER = StrategyAnalysisController()
        return _CONTROLLER


def completed_scan_callback(
    scan_payload: Mapping[str, object],
    *,
    order_manager: object | None,
    market_data: object | None,
    account_equity: float,
    cash_available: float,
    current_positions: object,
    open_risk_amount: float,
    market_open: bool,
) -> dict[str, object]:
    """Worker callback used after an existing intraday scan fully completes."""
    execution_context = AnalysisExecutionContext(
        order_manager=order_manager,
        market_data=market_data,
        account_equity=account_equity,
        cash_available=cash_available,
        current_positions=list(current_positions or []),
        open_risk_amount=open_risk_amount,
        market_open=market_open,
    )
    scan_time = str(scan_payload.get("scan_id") or scan_payload.get("completed_at") or "")
    scan = StrategyScanContext(
        scan_id=scan_time,
        completed_at=str(scan_payload.get("completed_at") or scan_time),
        symbols=tuple(str(item).upper() for item in scan_payload.get("symbols", ()) if str(item).strip()),
        watchlist=scan_payload.get("watchlist", {}) if isinstance(scan_payload.get("watchlist", {}), Mapping) else {},
        scan_result=scan_payload.get("scan_result", {}) if isinstance(scan_payload.get("scan_result", {}), Mapping) else {},
        bars_by_symbol=scan_payload.get("bars_by_symbol", {}) if isinstance(scan_payload.get("bars_by_symbol", {}), Mapping) else {},
    )
    return get_strategy_analysis_controller().publish_completed_scan(scan, execution_context)
