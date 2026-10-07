from __future__ import annotations

from src.decision.strategy_sources import (
    DEFAULT_REGISTRY,
    current_source_snapshot_payload,
    StrategyScanContext,
    StrategySource,
    StrategySourceInfo,
    StrategySourceRegistry,
    StrategySnapshot,
    snapshot_to_candidate,
    snapshot_to_trade_signal,
    _monitor_candidate_class,
)


def _snapshot(*, source: str = "gerchik_router", candidate_class: str = "EXECUTABLE_ENTRY_SIGNAL"):
    return StrategySnapshot(
        scan_id="scan-1",
        source=source,
        symbol="AAPL",
        strategy="TEST",
        signal_state="BUY",
        candidate_class=candidate_class,
        price=100.0,
        entry=100.0 if candidate_class == "EXECUTABLE_ENTRY_SIGNAL" else None,
        stop=98.0 if candidate_class == "EXECUTABLE_ENTRY_SIGNAL" else None,
        target=104.0 if candidate_class == "EXECUTABLE_ENTRY_SIGNAL" else None,
        reward_risk=2.0,
        atr=1.2,
        score=0.8,
        source_timestamp="2026-09-28T14:00:00+00:00",
        bar_timestamp="2026-09-28T14:00:00+00:00",
        direction="long",
        metadata={"signal_level": 99.5},
    )


def test_setup_id_is_stable_across_snapshot_refresh_batches():
    first = _snapshot()
    second = first.__class__(**{**first.to_dict(), "scan_id": "scan-2"})
    assert snapshot_to_candidate(first)[0].candidate_id == snapshot_to_candidate(second)[0].candidate_id


def test_registry_exposes_four_sources_and_combined_selection():
    assert DEFAULT_REGISTRY.ids() == ("stock_screener", "bmsb", "gaussian", "gerchik_router")
    assert DEFAULT_REGISTRY.selection_ids()[0] == "all"
    assert {item["id"] for item in DEFAULT_REGISTRY.infos()} == {
        "all",
        "stock_screener",
        "bmsb",
        "gaussian",
        "gerchik_router",
    }


def test_executable_snapshot_reuses_existing_candidate_boundary():
    candidate, allowed = snapshot_to_candidate(_snapshot())
    signal = snapshot_to_trade_signal(_snapshot())
    assert candidate.metadata["execution_eligible"] is True
    assert candidate.risk_per_share == 2.0
    assert candidate.level_price == 99.5
    assert allowed == ("ENTER", "WAIT", "REJECT")
    assert signal is not None
    assert signal.entry == 100.0
    assert signal.stop == 98.0
    assert signal.target == 104.0


def test_analysis_only_snapshot_cannot_enter_or_create_trade_signal():
    candidate, allowed = snapshot_to_candidate(
        _snapshot(source="bmsb", candidate_class="WATCH_CANDIDATE")
    )
    assert candidate.metadata["execution_eligible"] is False
    assert allowed == ("WAIT", "REJECT")
    assert snapshot_to_trade_signal(
        _snapshot(source="bmsb", candidate_class="WATCH_CANDIDATE")
    ) is None


def test_monitor_signal_semantics_do_not_grant_execution_capability():
    assert _monitor_candidate_class("gaussian", "GAUSSIAN_LONG_ENTRY") == "ENTRY_SIGNAL"
    assert _monitor_candidate_class("gaussian", "GAUSSIAN_NEAR_LONG") == "WATCH_CANDIDATE"
    assert _monitor_candidate_class("bmsb", "CROSSED_LONG") == "ENTRY_SIGNAL"
    assert _monitor_candidate_class("bmsb", "NEAR_LONG") == "WATCH_CANDIDATE"
    assert _monitor_candidate_class("bmsb", "CROSSED_EXIT") == "POSITION_MANAGEMENT_SIGNAL"


def test_analysis_only_entry_semantics_keep_wait_reject_and_no_trade_signal():
    snapshot = _snapshot(source="gaussian", candidate_class="ENTRY_SIGNAL")
    snapshot = snapshot.__class__(**{**snapshot.to_dict(), "metadata": {"execution_eligible": False}})
    candidate, allowed = snapshot_to_candidate(snapshot)
    assert candidate.metadata["candidate_class"] == "ENTRY_SIGNAL"
    assert candidate.metadata["execution_eligible"] is False
    assert candidate.entry == 0.0
    assert allowed == ("WAIT", "REJECT")
    assert snapshot_to_trade_signal(snapshot) is None


def test_registry_marks_a_source_unavailable_without_reusing_old_data():
    registry = StrategySourceRegistry(
        [
            StrategySource(
                StrategySourceInfo("broken", "Broken", "test"),
                lambda _context: (_ for _ in ()).throw(RuntimeError("source failed")),
            )
        ]
    )
    snapshots, statuses = registry.all_snapshots_with_status(
        StrategyScanContext(
            scan_id="scan-1",
            completed_at="2026-09-28T14:00:00+00:00",
            symbols=("AAPL",),
            watchlist={},
            scan_result={},
            bars_by_symbol={},
        )
    )
    assert snapshots["broken"] == []
    assert statuses["broken"] == "SOURCE_UNAVAILABLE"


def test_current_source_snapshot_is_immutable_source_data_with_zero_candidates(monkeypatch):
    context = StrategyScanContext(
        scan_id="source-context",
        completed_at="2026-09-28T14:00:00+00:00",
        symbols=("AAPL",),
        watchlist={},
        source_metadata={
            source: {
                "available": True,
                "source_timestamp": "2026-09-28T13:59:00+00:00",
                "latest_bar_timestamp": "2026-09-28T13:55:00+00:00",
                "data_age_seconds": 60.0,
                "freshness": "FRESH",
            }
            for source in ("stock_screener", "bmsb", "gaussian", "gerchik_router")
        },
    )
    registry = StrategySourceRegistry(
        [
            StrategySource(
                StrategySourceInfo(source, source.upper(), "test"),
                lambda _context: [],
            )
            for source in ("stock_screener", "bmsb", "gaussian", "gerchik_router")
        ]
    )
    monkeypatch.setattr("src.decision.strategy_sources._current_source_context", lambda: context)
    payload = current_source_snapshot_payload(registry)

    assert payload["analysis_snapshot_id"].startswith("analysis-")
    assert payload["source_counts"] == {
        "stock_screener": 0,
        "bmsb": 0,
        "gaussian": 0,
        "gerchik_router": 0,
    }
    assert all(value == "NO_CANDIDATES" for value in payload["source_status"].values())
    assert payload["source_metadata"]["bmsb"]["latest_bar_timestamp"]
    assert payload["source_metadata"]["bmsb"]["candidate_count"] == 0


def test_decision_lab_source_module_has_no_session_job_or_lock_ownership():
    from pathlib import Path
    import src.decision.strategy_sources as source_module

    text = Path(source_module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "run_premarket(",
        "run_open(",
        "run_intraday(",
        "run_entry_scan(",
        "market_session.lock",
        "intraday_session.lock",
    ):
        assert forbidden not in text
