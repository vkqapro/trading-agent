from __future__ import annotations
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_engine import CurrentSessionState, DailySetup, SetupStatus, evaluate_entry_day
from scanner_mcp.live_proposals import LiveProposalError, build_live_candidate_proposal
from scanner_mcp.proposals import ProposalSizingInputs


def sizing(**kwargs):
    values={"account_capital":10000,"max_capital_allocation_pct":5,"max_loss_risk_pct":1,"available_funds":10000}
    values.update(kwargs)
    return ProposalSizingInputs(**values)

def ready_result(high=138.50, freshness=1):
    setup=DailySetup("1.0","setup_orcl_b","ORCL","lp2","LONG","VALID_SETUP",{"price":134.91,"type":"support"},{"bar_1":{"date":"2026-09-28","role":"penetration_bar"},"bar_2":{"date":"2026-09-29","role":"reclaim_bar"}},{"entry":138.3705,"stop":129.8497,"target":155.4121},{"atr":6.921},{"historical_data_through":"2026-09-29"})
    session=CurrentSessionState("2026-09-30",136.54,high,134.75,137.08,4447349,"2026-09-30T14:45:00-04:00","in_progress","fixture","5m","2026-09-30T14:45:00-04:00",freshness)
    trigger=evaluate_entry_day(setup,session,previous_close=137.79)
    return {"schema_version":"1.0","symbol":"ORCL","strategy_id":"lp2","direction":"LONG","status":trigger.status.value,"setup":setup.to_dict(),"session":session.to_dict(),"trigger":trigger.to_dict(),"provenance":{"source":"fixture"}}

def test_watch_never_produces_proposal():
    result=ready_result(high=138.15)
    assert result["status"] == "WATCH"
    with pytest.raises(LiveProposalError) as exc:
        build_live_candidate_proposal(result,sizing())
    assert exc.value.code == "NOT_READY_FOR_PROPOSAL"

def test_ready_produces_deterministic_proposal_and_preserves_setup_id():
    result=ready_result()
    one=build_live_candidate_proposal(result,sizing()).to_dict()
    two=build_live_candidate_proposal(result,sizing()).to_dict()
    assert one == two
    assert one["proposal_status"] == "ACTIONABLE"
    assert one["setup_id"] == "setup_orcl_b"
    assert one["proposal_id"].startswith("prop_live_setup_orcl_b_2026-09-30_")
    assert one["order_plan"]["quantity"] == one["quantity"]

def test_live_plan_hash_versions_proposal_identity():
    first = ready_result()
    first["live_plan"] = {"entry": 138.3705, "stop": 129.8497, "target": 155.4121, "plan_hash": "plan_a"}
    second = ready_result()
    second["live_plan"] = {"entry": 138.3748, "stop": 129.8476, "target": 155.4292, "plan_hash": "plan_b"}
    one = build_live_candidate_proposal(first, sizing()).to_dict()
    two = build_live_candidate_proposal(second, sizing()).to_dict()
    assert one["setup_id"] == two["setup_id"]
    assert one["proposal_id"] != two["proposal_id"]
    assert one["setup"]["plan_hash"] == "plan_a"
    assert two["setup"]["plan_hash"] == "plan_b"


def test_stale_ready_fixture_is_rejected_before_planner():
    result=ready_result(high=138.50, freshness=9999)
    # Even if a caller incorrectly labels the trigger READY, freshness is a
    # mandatory second gate before planner invocation.
    result["status"]="READY"
    with pytest.raises(LiveProposalError) as exc:
        build_live_candidate_proposal(result,sizing())
    assert exc.value.code == "STALE_SESSION"

def test_ready_zero_quantity_is_non_actionable():
    result=ready_result()
    zero=sizing(max_capital_allocation_pct=0, max_loss_risk_pct=0, available_funds=0, max_position_value=0)
    proposal=build_live_candidate_proposal(result,zero).to_dict()
    assert result["status"] == "READY"
    assert proposal["proposal_status"] == "NON_ACTIONABLE"
    assert proposal["order_plan"]["reasons"][0]["code"] == "ZERO_QUANTITY"

def test_allocation_and_loss_risk_remain_separate():
    proposal=build_live_candidate_proposal(ready_result(), sizing())
    assert proposal.sizing_inputs["max_capital_allocation_pct"] == 5
    assert proposal.sizing_inputs["max_loss_risk_pct"] == 1
