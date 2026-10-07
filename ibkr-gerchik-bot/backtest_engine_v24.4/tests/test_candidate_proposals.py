from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ENGINE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_ROOT.parent
for path in (str(ENGINE_ROOT), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from scanner_mcp.proposals import ProposalSizingInputs, build_candidate_proposal


def candidate(strategy="lp2", entry=250.0, stop=240.0, target=270.0):
    return {
        "run_id": "scan_20260930T000000Z_abcdef", "schema_version": "1.1", "scanner_version": "0.2.0",
        "symbol": "TEST", "strategy_id": strategy, "direction": "LONG", "status": "ENTRY_SIGNAL", "matched": True,
        "signal": {"signal_bar": "2026-09-28"}, "level": {"price": 245.0, "level_type": "support"},
        "trade_plan": {"entry": entry, "stop": stop, "target": target, "reward_risk": 2.0},
        "quality": {"score": 75.99810695028297}, "pattern_definition": {"pattern_family": strategy},
        "pattern_bars": {"signal_bar": {"date": "2026-09-28"}}, "trigger_evidence": {"entry_triggered": True},
        "rejection_evidence": [], "chart_annotations": [], "quantity": 999,
    }


PROFILE = ProposalSizingInputs(account_capital=10000, max_capital_allocation_pct=5,
                               max_loss_risk_pct=1, available_funds=10000, max_position_value=25000)


def test_actionable_proposal_uses_canonical_planner_and_preserves_precision():
    raw = candidate(entry=250.123456789, stop=240.123456789, target=270.123456789)
    proposal = build_candidate_proposal(raw, PROFILE)
    assert proposal.quantity == 1
    assert proposal.order_plan["quantity"] == 1
    assert proposal.order_plan["quantity_constraints"] == {
        "capital_allocation": 1, "loss_risk": 10, "available_funds": 39, "max_position_value": 99,
    }
    assert proposal.setup["entry"] == 250.123456789
    assert proposal.legacy_scanner_quantity == 999
    assert proposal.order_plan["position_value"] == pytest.approx(250.123456789)


def test_dual_constraint_fixture_and_zero_quantity():
    proposal = build_candidate_proposal(candidate(), PROFILE)
    assert proposal.quantity == 2
    assert proposal.order_plan["binding_constraint"] == "capital_allocation"
    assert proposal.order_plan["risk"]["max_loss_before_costs"] == 20
    assert proposal.order_plan["reward"]["potential_profit_before_costs"] == 40
    zero = build_candidate_proposal(candidate(entry=600, stop=590, target=620), PROFILE)
    assert zero.proposal_status == "NON_ACTIONABLE"
    assert zero.quantity == 0
    assert zero.order_plan["reasons"][0]["code"] == "ZERO_QUANTITY"


@pytest.mark.parametrize("strategy", ["lp1", "lp2", "prb1", "prb2"])
def test_all_production_strategies_are_eligible(strategy):
    proposal = build_candidate_proposal(candidate(strategy), PROFILE)
    assert proposal.strategy["strategy_id"] == strategy


def test_proposal_id_and_settings_hash_are_deterministic_and_profile_sensitive():
    first = build_candidate_proposal(candidate(), PROFILE).to_dict()
    second = build_candidate_proposal(copy.deepcopy(candidate()), PROFILE).to_dict()
    other = build_candidate_proposal(candidate(), ProposalSizingInputs(account_capital=5000, max_capital_allocation_pct=5, max_loss_risk_pct=1)).to_dict()
    assert first["proposal_id"] == second["proposal_id"]
    assert first["settings_hash"] == second["settings_hash"]
    assert first["proposal_id"] != other["proposal_id"]
    assert first["settings_hash"] != other["settings_hash"]


def test_ineligible_scanner_result_is_not_proposal():
    raw = candidate()
    raw["status"] = "NO_SIGNAL"
    with pytest.raises(ValueError):
        build_candidate_proposal(raw, PROFILE)
