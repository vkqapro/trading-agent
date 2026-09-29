from __future__ import annotations

import time
from types import SimpleNamespace

from src.decision.strategy_control import create_run, list_runs, save_snapshot
from src.decision.strategy_controller import (
    AnalysisExecutionContext,
    StrategyAnalysisController,
)
from src.decision.strategy_sources import (
    StrategyScanContext,
    StrategySnapshot,
    StrategySource,
    StrategySourceInfo,
    StrategySourceRegistry,
)


class _FakeAgent:
    class _Mode:
        value = "paper_autonomous"

    mode = _Mode()

    def __init__(self, order_manager=None):
        self.order_manager = order_manager

    def process_candidate(self, candidate, *, context, signal=None, allowed_actions=None):
        return SimpleNamespace(
            status="wait",
            action="WAIT",
            execution=None,
            reasons=(),
        )


def _snapshot_adapter(context):
    return [
        StrategySnapshot(
            scan_id=context.scan_id,
            source="test",
            symbol="AAPL",
            strategy="TEST",
            signal_state="BUY",
            candidate_class="WATCH_CANDIDATE",
            price=100.0,
            source_timestamp=context.completed_at,
            bar_timestamp=context.completed_at,
            direction="long",
        )
    ]


def test_worker_controller_auto_is_idempotent(monkeypatch, tmp_path):
    import src.decision.strategy_controller as controller_module

    monkeypatch.setattr(controller_module, "AutonomousGerchikAgent", _FakeAgent)
    registry = StrategySourceRegistry(
        [StrategySource(StrategySourceInfo("test", "Test", "test"), _snapshot_adapter)]
    )
    controller = StrategyAnalysisController(runtime_dir=tmp_path, registry=registry)
    scan = StrategyScanContext(
        scan_id="scan-1",
        completed_at="2026-09-28T14:00:00+00:00",
        symbols=("AAPL",),
        watchlist={"AAPL": {}},
        scan_result={},
        bars_by_symbol={},
    )
    # Persist the actual control selection directly for this isolated worker.
    from src.decision.strategy_control import update_control

    update_control(
        analysis_mode="auto",
        strategy_source="test",
        runtime_dir=tmp_path,
        registry=registry,
    )
    context = AnalysisExecutionContext(
        order_manager=SimpleNamespace(broker=SimpleNamespace(is_connected=False)),
        market_data=None,
        account_equity=100000.0,
        cash_available=100000.0,
        current_positions=[],
        open_risk_amount=0.0,
        market_open=False,
    )
    controller.publish_completed_scan(scan, context)
    controller.publish_completed_scan(scan, context)
    deadline = time.time() + 2.0
    while time.time() < deadline:
        runs = list_runs(runtime_dir=tmp_path)
        if any(item.get("scan_id") == "scan-1" and item.get("status") == "COMPLETED" for item in runs):
            break
        time.sleep(0.02)
    runs = [item for item in list_runs(runtime_dir=tmp_path) if item.get("scan_id") == "scan-1"]
    assert len(runs) == 1
    assert runs[0]["decision_count"] == 1


def test_worker_controller_consumes_manual_run_and_completes(monkeypatch, tmp_path):
    import src.decision.strategy_controller as controller_module

    monkeypatch.setattr(controller_module, "AutonomousGerchikAgent", _FakeAgent)
    registry = StrategySourceRegistry(
        [StrategySource(StrategySourceInfo("test", "Test", "test"), _snapshot_adapter)]
    )
    controller = StrategyAnalysisController(runtime_dir=tmp_path, registry=registry)
    item = _snapshot_adapter(
        StrategyScanContext(
            scan_id="scan-manual",
            completed_at="2026-09-28T14:00:00+00:00",
            symbols=("AAPL",),
            watchlist={"AAPL": {}},
        )
    )[0].to_dict()
    save_snapshot(
        {
            "scan_id": "scan-manual",
            "completed_at": "2026-09-28T14:00:00+00:00",
            "source_counts": {"test": 1},
            "source_status": {"test": "READY"},
            "sources": {"test": [item]},
        },
        runtime_dir=tmp_path,
    )
    run = create_run(
        analysis_mode="manual",
        strategy_source="test",
        scan_id="scan-manual",
        snapshot_timestamp="2026-09-28T14:00:00+00:00",
        runtime_dir=tmp_path,
        registry=registry,
    )
    context = AnalysisExecutionContext(
        order_manager=SimpleNamespace(broker=SimpleNamespace(is_connected=False)),
        market_data=None,
        account_equity=100000.0,
        cash_available=100000.0,
        current_positions=[],
        open_risk_amount=0.0,
        market_open=False,
    )
    assert controller.drain_pending_manual(context) == 1
    deadline = time.time() + 2.0
    while time.time() < deadline:
        current = list_runs(runtime_dir=tmp_path)[0]
        if current["status"] == "COMPLETED":
            break
        time.sleep(0.02)
    current = list_runs(runtime_dir=tmp_path)[0]
    assert current["run_id"] == run["run_id"]
    assert current["status"] == "COMPLETED"
    assert current["candidate_count"] == 1
    assert current["decision_count"] == 1


def test_zero_candidate_manual_snapshot_completes(monkeypatch, tmp_path):
    import src.decision.strategy_controller as controller_module

    monkeypatch.setattr(controller_module, "AutonomousGerchikAgent", _FakeAgent)
    registry = StrategySourceRegistry(
        [StrategySource(StrategySourceInfo("test", "Test", "test"), lambda _context: [])]
    )
    controller = StrategyAnalysisController(runtime_dir=tmp_path, registry=registry)
    save_snapshot(
        {
            "scan_id": "scan-zero",
            "completed_at": "2026-09-28T14:00:00+00:00",
            "source_counts": {"test": 0},
            "source_status": {"test": "NO_CANDIDATES"},
            "sources": {"test": []},
        },
        runtime_dir=tmp_path,
    )
    create_run(
        analysis_mode="manual",
        strategy_source="test",
        scan_id="scan-zero",
        snapshot_timestamp="2026-09-28T14:00:00+00:00",
        runtime_dir=tmp_path,
        registry=registry,
    )
    assert controller.drain_pending_manual(
        AnalysisExecutionContext(
            order_manager=SimpleNamespace(broker=SimpleNamespace(is_connected=False)),
            market_data=None,
            account_equity=100000.0,
            cash_available=100000.0,
            current_positions=[],
            open_risk_amount=0.0,
            market_open=False,
        )
    ) == 1
    deadline = time.time() + 2.0
    while time.time() < deadline and list_runs(runtime_dir=tmp_path)[0]["status"] == "RUNNING":
        time.sleep(0.02)
    current = list_runs(runtime_dir=tmp_path)[0]
    assert current["status"] == "COMPLETED"
    assert current["candidate_count"] == 0
    assert current["decision_count"] == 0
