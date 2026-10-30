"""Forward-only state machine for persistent IRS setups."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from src.strategy.inefficiency_reclaim import STRATEGY_VERSION, SetupState


REJECTION_STATES = frozenset(
    {
        SetupState.REJECTED_BY_DATA,
        SetupState.REJECTED_BY_REGIME,
        SetupState.REJECTED_BY_NEWS,
        SetupState.REJECTED_BY_LIQUIDITY,
        SetupState.REJECTED_BY_TARGET_SPACE,
        SetupState.REJECTED_BY_RISK,
    }
)
TERMINAL_STATES = frozenset(
    {
        SetupState.POSITION_CLOSED,
        SetupState.EXPIRED,
        SetupState.INVALIDATED,
        SetupState.CANCELLED,
        *REJECTION_STATES,
    }
)
PRE_ORDER_STATES = frozenset(
    {
        SetupState.SEARCHING,
        SetupState.DISPLACEMENT_FOUND,
        SetupState.INEFFICIENCY_REGISTERED,
        SetupState.WAITING_FOR_RETRACE,
        SetupState.RETRACE_DETECTED,
        SetupState.WAITING_FOR_CONFIRMATION,
        SetupState.ENTRY_ARMED,
    }
)


ALLOWED_TRANSITIONS: Mapping[SetupState, frozenset[SetupState]] = {
    SetupState.SEARCHING: frozenset({SetupState.DISPLACEMENT_FOUND, *REJECTION_STATES}),
    SetupState.DISPLACEMENT_FOUND: frozenset(
        {SetupState.INEFFICIENCY_REGISTERED, SetupState.INVALIDATED, *REJECTION_STATES}
    ),
    SetupState.INEFFICIENCY_REGISTERED: frozenset(
        {SetupState.WAITING_FOR_RETRACE, SetupState.INVALIDATED, *REJECTION_STATES}
    ),
    SetupState.WAITING_FOR_RETRACE: frozenset(
        {SetupState.RETRACE_DETECTED, SetupState.EXPIRED, SetupState.INVALIDATED, *REJECTION_STATES}
    ),
    SetupState.RETRACE_DETECTED: frozenset(
        {SetupState.WAITING_FOR_CONFIRMATION, SetupState.INVALIDATED, *REJECTION_STATES}
    ),
    SetupState.WAITING_FOR_CONFIRMATION: frozenset(
        {SetupState.ENTRY_ARMED, SetupState.EXPIRED, SetupState.INVALIDATED, *REJECTION_STATES}
    ),
    SetupState.ENTRY_ARMED: frozenset(
        {SetupState.ORDER_SUBMITTED, SetupState.EXPIRED, SetupState.CANCELLED, SetupState.INVALIDATED, *REJECTION_STATES}
    ),
    SetupState.ORDER_SUBMITTED: frozenset(
        {SetupState.ORDER_PARTIALLY_FILLED, SetupState.ORDER_FILLED, SetupState.CANCELLED}
    ),
    SetupState.ORDER_PARTIALLY_FILLED: frozenset(
        {SetupState.ORDER_FILLED, SetupState.POSITION_OPEN, SetupState.CANCELLED}
    ),
    SetupState.ORDER_FILLED: frozenset({SetupState.POSITION_OPEN}),
    SetupState.POSITION_OPEN: frozenset({SetupState.POSITION_CLOSED}),
}


class InvalidStateTransition(ValueError):
    """Raised when a setup attempts a backward or terminal transition."""


@dataclass(frozen=True)
class StateTransition:
    setup_id: str
    previous_state: SetupState
    new_state: SetupState
    exchange_timestamp: datetime
    reason: str
    source_bar_id: str | None
    strategy_version: str
    run_id: str

    def __post_init__(self) -> None:
        if self.exchange_timestamp.tzinfo is None:
            raise ValueError("Transition timestamp must be timezone-aware.")
        if not self.setup_id or not self.reason or not self.run_id:
            raise ValueError("Transition setup_id, reason, and run_id are required.")


def can_transition(previous: SetupState, new: SetupState) -> bool:
    return new in ALLOWED_TRANSITIONS.get(previous, frozenset())


def advance_setup_state(
    *,
    setup_id: str,
    previous: SetupState,
    new: SetupState,
    exchange_timestamp: datetime,
    reason: str,
    source_bar_id: str | None,
    run_id: str,
    strategy_version: str = STRATEGY_VERSION,
) -> StateTransition:
    if previous in TERMINAL_STATES:
        raise InvalidStateTransition(f"Terminal state {previous.value} cannot transition.")
    if previous == new:
        raise InvalidStateTransition(f"Repeated state {previous.value} is not a transition.")
    if not can_transition(previous, new):
        raise InvalidStateTransition(f"Forbidden IRS transition: {previous.value} -> {new.value}.")
    return StateTransition(
        setup_id=setup_id,
        previous_state=previous,
        new_state=new,
        exchange_timestamp=exchange_timestamp,
        reason=reason,
        source_bar_id=source_bar_id,
        strategy_version=strategy_version,
        run_id=run_id,
    )
