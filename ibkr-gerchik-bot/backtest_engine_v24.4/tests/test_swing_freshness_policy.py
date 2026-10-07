from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_engine import (
    CurrentSessionState,
    SessionFreshnessStatus,
    SWING_FRESHNESS_POLICY,
    evaluate_live_setup,
)
from scanner_mcp.live_proposals import build_live_candidate_proposal
from scanner_mcp.proposals import ProposalSizingInputs
from test_live_trade_plan_alignment import _historical, _session, _setup


def test_freshness_boundaries_are_exact():
    expected = {
        899: SessionFreshnessStatus.FRESH,
        900: SessionFreshnessStatus.FRESH,
        901: SessionFreshnessStatus.DELAYED_USABLE,
        1800: SessionFreshnessStatus.DELAYED_USABLE,
        3599: SessionFreshnessStatus.DELAYED_USABLE,
        3600: SessionFreshnessStatus.DELAYED_USABLE,
        3601: SessionFreshnessStatus.STALE,
    }
    for age, status in expected.items():
        session = replace(_session("09:45"), freshness_seconds=float(age), reference_time="2026-09-30T15:00:00-04:00")
        assert session.freshness_detail.status is status
        assert session.freshness_detail.fresh_limit_seconds == SWING_FRESHNESS_POLICY.fresh_limit_seconds
        assert session.freshness_detail.delayed_usable_limit_seconds == SWING_FRESHNESS_POLICY.delayed_usable_limit_seconds


def test_orcl_same_snapshot_changes_freshness_not_plan_identity():
    snapshot = replace(_session("09:45"), high=140.0, freshness_seconds=600.0, reference_time="2026-09-30T10:00:00-04:00")
    setup = _setup("lp2", 134.91)
    fresh = evaluate_live_setup(setup, snapshot, previous_close=137.79, historical_daily_bars=_historical())
    delayed = evaluate_live_setup(setup, replace(snapshot, freshness_seconds=1800.0, reference_time="2026-09-30T10:20:00-04:00"), previous_close=137.79, historical_daily_bars=_historical())
    stale = evaluate_live_setup(setup, replace(snapshot, freshness_seconds=4200.0, reference_time="2026-09-30T11:10:00-04:00"), previous_close=137.79, historical_daily_bars=_historical())
    assert fresh.status.value == "READY"
    assert delayed.status.value == "READY"
    assert stale.status.value == "STALE_DATA"
    assert fresh.session.freshness_state == "FRESH"
    assert delayed.session.freshness_state == "DELAYED_USABLE"
    assert stale.session.freshness_state == "STALE"
    assert fresh.live_plan.to_dict() == delayed.live_plan.to_dict()
    assert fresh.live_plan.plan_hash == delayed.live_plan.plan_hash
    assert fresh.live_plan.entry == delayed.live_plan.entry
    assert fresh.live_plan.stop == delayed.live_plan.stop
    assert fresh.live_plan.target == delayed.live_plan.target


def _sizing():
    return ProposalSizingInputs(account_capital=10000, max_capital_allocation_pct=5, max_loss_risk_pct=1, available_funds=10000)


def test_delayed_ready_can_create_proposal_with_revalidation_metadata():
    setup = _setup("lp2", 134.91)
    session = replace(_session("09:45"), high=140.0, freshness_seconds=1800.0, reference_time="2026-09-30T10:20:00-04:00")
    result = evaluate_live_setup(setup, session, previous_close=137.79, historical_daily_bars=_historical()).to_dict()
    proposal = build_live_candidate_proposal(result, _sizing()).to_dict()
    assert proposal["live_status"] == "READY"
    assert proposal["setup"]["market_data_freshness_status"] == "DELAYED_USABLE"
    assert proposal["setup"]["market_data_age_seconds"] == 1800.0
    assert proposal["setup"]["execution_revalidation_required"] is True
    assert proposal["live_provenance"]["execution_revalidation_reason"] == "DELAYED_MARKET_DATA"
    assert proposal["live_provenance"]["session_as_of"] == session.as_of


def test_stale_ready_result_cannot_reach_proposal_builder():
    setup = _setup("lp2", 134.91)
    session = replace(_session("09:45"), high=140.0, freshness_seconds=3601.0, reference_time="2026-09-30T11:10:00-04:00")
    result = evaluate_live_setup(setup, session, previous_close=137.79, historical_daily_bars=_historical()).to_dict()
    assert result["status"] == "STALE_DATA"
    with pytest.raises(Exception) as exc:
        build_live_candidate_proposal(result, _sizing())
    assert getattr(exc.value, "code", None) == "STALE_SESSION"
