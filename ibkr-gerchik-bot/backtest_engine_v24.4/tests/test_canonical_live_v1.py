from __future__ import annotations
import ast
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_engine import CurrentSessionState, DailySetup, SetupStatus, evaluate_entry_day


def setup(entry=140.708):
    return DailySetup("1.0", "orcl_lp2_a", "ORCL", "lp2", "LONG", "VALID_SETUP",
        {"price": 137.2475, "type": "support"},
        {"bar_1": {"date":"2026-09-28","role":"penetration_bar","open":132.84,"high":135.69,"low":131.58,"close":132.60,"volume":17569986},
         "bar_2": {"date":"2026-09-29","role":"reclaim_bar","open":132.86,"high":143.67,"low":132.53,"close":137.79,"volume":31558965}},
        {"entry":entry,"stop":129.8497,"target":162.4246}, {"atr":6.921},
        {"historical_data_through":"2026-09-29","strategy_params":{"gap_max_atr":1.2,"chase_max_atr":.3}})


def session(high, freshness=1):
    return CurrentSessionState("2026-09-30",136.54,high,134.75,137.08,4447349,
        "2026-09-30T14:45:00Z","in_progress","stored_daily_current_session","1D",
        "2026-09-30T14:45:00Z",freshness)


def test_orcl_setup_a_watch():
    result=evaluate_entry_day(setup(),session(138.15),previous_close=137.79)
    assert result.status is SetupStatus.WATCH
    assert result.trigger_evidence["entry_reached"] is False


def test_orcl_setup_b_watch_and_synthetic_ready():
    s=setup(138.3705)
    assert evaluate_entry_day(s,session(138.15),previous_close=137.79).status is SetupStatus.WATCH
    ready=evaluate_entry_day(s,session(138.50),previous_close=137.79)
    assert ready.status is SetupStatus.READY
    assert ready.setup_id==evaluate_entry_day(s,session(138.15),previous_close=137.79).setup_id


def test_current_session_is_not_pattern_bar():
    assert all(bar["date"] != "2026-09-30" for bar in setup().pattern_bars.values())


def test_stale_missing_and_complete_sessions_never_ready():
    assert evaluate_entry_day(setup(),session(150,9999),previous_close=137.79).status is SetupStatus.STALE_DATA
    assert evaluate_entry_day(setup(),None).status is SetupStatus.INVALID
    complete=CurrentSessionState("2026-09-30",136.54,150,134.75,137.08,1,"now","complete","fixture","1D","now",1)
    assert evaluate_entry_day(setup(),complete).status is SetupStatus.INVALID


def test_gap_and_invalid_stop_rejection_are_structured():
    assert "GAP_EXCEEDED" in evaluate_entry_day(setup(),session(150),previous_close=100).rejection_evidence
    bad=DailySetup(**{**setup().__dict__, "planned_trade":{"entry":140.708,"stop":141,"target":150}})
    assert "INVALID_STOP" in evaluate_entry_day(bad,session(138),previous_close=137.79).rejection_evidence


def test_engine_source_has_no_execution_imports():
    source=(Path(__file__).resolve().parents[1]/"live_engine"/"engine.py").read_text(encoding="utf-8")
    tree=ast.parse(source)
    imports=[node.module or "" for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
    assert not any(token in source for token in ("ib_insync","placeOrder","execute_requests","Telegram","LLM"))
    assert not any(x.startswith(("src.execution","src.decision")) for x in imports)
