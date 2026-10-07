from __future__ import annotations

from src.brokers.ibkr import OrderResult
from src.execution.order_manager import OrderManager


def _result(status: str, *, filled: float = 0.0) -> OrderResult:
    return OrderResult(
        order_id=1,
        symbol="XP",
        action="BUY",
        quantity=9,
        order_type="MKT",
        status=status,
        filled=filled,
        remaining=max(0.0, 9.0 - filled),
        avg_fill_price=20.74 if filled else 0.0,
    )


def test_bracket_fill_overrides_conflicting_cancel_snapshot() -> None:
    outcome = OrderManager._classify_bracket_snapshot(
        _result("Cancelled", filled=9),
        _result("PendingSubmit"),
        _result("PendingSubmit"),
    )

    assert outcome == "executed"


def test_bracket_conflict_requires_reconciliation_instead_of_rejection() -> None:
    outcome = OrderManager._classify_bracket_snapshot(
        _result("Cancelled"),
        _result("PendingSubmit"),
        _result("PendingSubmit"),
    )

    assert outcome == "unknown_requires_reconciliation"


def test_bracket_is_rejected_only_when_all_broker_states_are_terminal_failures() -> None:
    outcome = OrderManager._classify_bracket_snapshot(
        _result("Cancelled"),
        _result("Inactive"),
        _result("ApiCancelled"),
    )

    assert outcome == "broker_rejected"
