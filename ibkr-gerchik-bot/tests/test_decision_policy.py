from __future__ import annotations

from datetime import datetime, timezone

from src.decision.models import DecisionAction, DecisionCandidate, PositionAction
from src.decision.policy import AutonomousRiskGate, evaluate_position_action


def _candidate() -> DecisionCandidate:
    return DecisionCandidate(
        candidate_id="policy-candidate-1",
        created_at=datetime.now(timezone.utc),
        asset_class="stock",
        symbol="AAPL",
        strategy="rebound",
        direction="long",
        entry=100.0,
        stop=98.0,
        target=106.0,
        level_price=99.5,
        level_type="support",
        level_strength=8.0,
        atr=4.0,
        reward_risk=3.0,
        risk_per_share=2.0,
    )


def test_risk_gate_reuses_deterministic_sizing_and_approves_clean_paper_entry() -> None:
    result = AutonomousRiskGate().evaluate_entry(
        _candidate(),
        account_equity=50_000.0,
        cash_available=50_000.0,
        current_positions=[],
        open_risk_amount=0.0,
        spread_pct=0.0005,
        broker_connected=True,
        account_synced=True,
        market_open=True,
        first_unstable_minutes=False,
        data_age_seconds=1.0,
        isolated_paper=True,
    )

    assert result.approved is True
    assert result.quantity == 250
    assert result.risk_amount == 500.0
    assert result.reasons == ()


def test_risk_gate_vetoes_stale_data_and_price_chase() -> None:
    result = AutonomousRiskGate().evaluate_entry(
        _candidate(),
        account_equity=100_000.0,
        cash_available=100_000.0,
        current_positions=[],
        open_risk_amount=0.0,
        spread_pct=0.0005,
        broker_connected=True,
        account_synced=True,
        market_open=True,
        first_unstable_minutes=False,
        data_age_seconds=90.0,
        current_price=103.0,
        isolated_paper=True,
    )

    assert result.approved is False
    assert "data_stale" in result.reasons
    assert "price_chase_too_far" in result.reasons


def test_risk_gate_vetoes_same_symbol_legacy_ownership() -> None:
    result = AutonomousRiskGate().evaluate_entry(
        _candidate(),
        account_equity=100_000.0,
        cash_available=100_000.0,
        current_positions=[{"symbol": "AAPL", "owner": "GERCHIK_LEGACY"}],
        open_risk_amount=0.0,
        spread_pct=0.0005,
        broker_connected=True,
        account_synced=True,
        market_open=True,
        first_unstable_minutes=False,
        isolated_paper=True,
    )

    assert result.approved is False
    assert "symbol_owned_by_legacy" in result.reasons


def test_position_policy_never_widens_stop_and_allows_safe_breakeven() -> None:
    position = {
        "position_id": "paper-pos-1",
        "symbol": "AAPL",
        "direction": "long",
        "entry": 100.0,
        "stop_loss": 98.0,
        "quantity": 10,
    }
    safe = evaluate_position_action(
        PositionAction.MOVE_STOP_TO_BREAKEVEN,
        position,
        current_price=102.0,
    )
    unsafe = evaluate_position_action(
        PositionAction.MOVE_STOP_TO_BREAKEVEN,
        position,
        current_price=99.0,
    )

    assert safe.approved is True
    assert safe.new_stop == 100.0
    assert unsafe.approved is False
    assert "breakeven_not_safe" in unsafe.reasons


def test_position_policy_supports_trim_and_close_only_from_menu() -> None:
    position = {"direction": "short", "entry": 100.0, "stop_loss": 102.0, "quantity": 5}
    trim = evaluate_position_action(PositionAction.TRIM_50, position, current_price=98.0)
    close = evaluate_position_action(PositionAction.CLOSE, position, current_price=98.0)

    assert trim.approved is True and trim.quantity == 2
    assert close.approved is True and close.quantity == 5
