"""Fail-closed paper executor for ENTRY_ARMED IRS candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Protocol, Sequence

from src.config import SETTINGS
from src.storage.inefficiency_reclaim_store import InefficiencyReclaimStore
from src.strategy.inefficiency_reclaim import (
    AccountState,
    Direction,
    IRSConfig,
    RejectionReason,
    SetupState,
    StrategyCandidate,
    size_position,
)
from src.strategy.inefficiency_reclaim_state import advance_setup_state


FAILURE_STATUSES = {"cancelled", "inactive", "apicancelled", "rejected"}


class IRSBroker(Protocol):
    @property
    def is_connected(self) -> bool: ...

    def get_account_summary(self) -> list[dict[str, Any]]: ...

    def get_open_orders(self) -> list[dict[str, Any]]: ...

    def get_positions(self) -> list[dict[str, Any]]: ...

    def get_market_price(self, symbol: str) -> dict[str, Any]: ...

    def place_stop_limit_bracket_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        entry_stop_price: float,
        entry_limit_price: float,
        stop_price: float,
        target_price: float,
        *,
        order_ref: str,
        tif: str,
        outside_rth: bool,
    ) -> tuple[Any, Any, Any]: ...

    def resize_open_order(self, order_id: int, quantity: int) -> bool: ...

    def cancel_order(self, order_id: int) -> bool: ...


@dataclass(frozen=True)
class IRSExecutionPolicy:
    enabled: bool
    paper_trading: bool
    allow_live_trading: bool
    trading_mode: str

    @classmethod
    def from_settings(cls) -> "IRSExecutionPolicy":
        settings = SETTINGS.inefficiency_reclaim
        return cls(
            enabled=settings.enabled,
            paper_trading=SETTINGS.paper_trading,
            allow_live_trading=settings.allow_live_trading,
            trading_mode=settings.trading_mode,
        )


@dataclass(frozen=True)
class IRSExecutionResult:
    submitted: bool
    duplicate_prevented: bool
    order_ref: str
    state: SetupState
    reasons: tuple[str, ...]
    broker_order_ids: tuple[int, ...] = ()
    quantity: int = 0
    global_entry_lock: bool = False


@dataclass(frozen=True)
class IRSReconnectResult:
    safe: bool
    global_entry_lock: bool
    reconciled_order_refs: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class IRSExpiryCancellationResult:
    cancelled_order_refs: tuple[str, ...]
    global_entry_lock: bool
    reasons: tuple[str, ...]


class InefficiencyReclaimPaperExecutor:
    def __init__(
        self,
        broker: IRSBroker,
        store: InefficiencyReclaimStore,
        *,
        config: IRSConfig,
        policy: IRSExecutionPolicy | None = None,
        alerter: Any | None = None,
    ) -> None:
        self.broker = broker
        self.store = store
        self.config = config
        self.policy = policy or IRSExecutionPolicy.from_settings()
        self.alerter = alerter

    @staticmethod
    def order_ref(candidate: StrategyCandidate) -> str:
        return f"IRS-{candidate.signal_id}"

    def _policy_rejections(self) -> list[str]:
        reasons: list[str] = []
        if not self.policy.enabled:
            reasons.append("INEFFICIENCY_RECLAIM_DISABLED")
        if self.policy.trading_mode != "paper" or not self.policy.paper_trading:
            reasons.append(RejectionReason.LIVE_TRADING_DISABLED.value)
        if self.policy.allow_live_trading:
            reasons.append(RejectionReason.LIVE_TRADING_DISABLED.value)
        if not self.broker.is_connected:
            reasons.append(RejectionReason.BROKER_DISCONNECTED.value)
        return reasons

    def _verify_paper_account(self) -> bool:
        rows = self.broker.get_account_summary()
        account_codes = {
            str(item.get("account", "") or "").strip().upper()
            for item in rows
            if str(item.get("account", "") or "").strip()
        }
        return bool(account_codes) and all(code.startswith("D") for code in account_codes)

    def _fresh_quote(self, candidate: StrategyCandidate, as_of: datetime) -> tuple[dict[str, Decimal] | None, list[str]]:
        quote = self.broker.get_market_price(candidate.symbol)
        try:
            bid = Decimal(str(quote.get("bid") or 0))
            ask = Decimal(str(quote.get("ask") or 0))
            last = Decimal(str(quote.get("last") or 0))
        except Exception:
            return None, [RejectionReason.MISSING_QUOTE.value]
        if bid <= 0 or ask <= 0:
            return None, [RejectionReason.MISSING_QUOTE.value]
        timestamp = quote.get("timestamp")
        if timestamp is not None:
            try:
                quote_time = datetime.fromisoformat(str(timestamp))
                if quote_time.tzinfo is None or (as_of - quote_time).total_seconds() > self.config.max_quote_age_seconds:
                    return None, [RejectionReason.STALE_QUOTE.value]
            except (TypeError, ValueError):
                return None, [RejectionReason.STALE_QUOTE.value]
        mid = (bid + ask) / Decimal("2")
        spread = (ask - bid) / mid * Decimal("100")
        if spread > self.config.max_spread_percent:
            return None, [RejectionReason.SPREAD_TOO_WIDE.value]
        return {"bid": bid, "ask": ask, "last": last, "spread_percent": spread}, []

    def _broker_duplicate(self, order_ref: str) -> dict[str, Any] | None:
        for order in self.broker.get_open_orders():
            reference = str(order.get("order_ref", "") or "")
            if reference == order_ref or reference.startswith(f"{order_ref}-"):
                return order
        return None

    def submit(
        self,
        candidate: StrategyCandidate,
        *,
        as_of: datetime,
        account: AccountState,
        run_id: str,
    ) -> IRSExecutionResult:
        order_ref = self.order_ref(candidate)
        reasons = self._policy_rejections()
        if candidate.state is not SetupState.ENTRY_ARMED:
            reasons.append("SETUP_NOT_ENTRY_ARMED")
        reasons.extend(candidate.hard_rejections)
        if candidate.order_plan is None:
            reasons.append("ORDER_PLAN_MISSING")
        if reasons:
            return IRSExecutionResult(
                False, False, order_ref, candidate.state, tuple(dict.fromkeys(reasons))
            )
        if not self._verify_paper_account():
            return IRSExecutionResult(
                False,
                False,
                order_ref,
                candidate.state,
                (RejectionReason.LIVE_TRADING_DISABLED.value,),
            )
        local_order = self.store.get_order(order_ref)
        broker_order = self._broker_duplicate(order_ref)
        if local_order or broker_order:
            if broker_order and not local_order:
                self.store.record_order(
                    signal_id=candidate.signal_id,
                    order_ref=order_ref,
                    broker_order_id=int(broker_order.get("order_id") or 0),
                    status=str(broker_order.get("status") or "Reconciled"),
                    quantity=int(broker_order.get("quantity") or 0),
                    submitted_at=as_of,
                    payload={
                        "setup_id": candidate.setup_id,
                        "reconciled_from_broker": True,
                        "broker_order": broker_order,
                    },
                )
            return IRSExecutionResult(
                False,
                True,
                order_ref,
                SetupState.ORDER_SUBMITTED,
                (RejectionReason.DUPLICATE_SIGNAL.value,),
            )
        quote, quote_reasons = self._fresh_quote(candidate, as_of)
        if quote_reasons:
            return IRSExecutionResult(
                False, False, order_ref, candidate.state, tuple(quote_reasons)
            )
        plan = candidate.order_plan
        overextended = (
            quote["ask"] > plan.entry_limit
            if candidate.direction is Direction.LONG
            else quote["bid"] < plan.entry_limit
        )
        if overextended:
            return IRSExecutionResult(
                False,
                False,
                order_ref,
                candidate.state,
                (RejectionReason.ENTRY_OVEREXTENDED.value,),
            )
        executable_entry = (
            max(plan.entry_limit, quote["ask"])
            if candidate.direction is Direction.LONG
            else min(plan.entry_limit, quote["bid"])
        )
        quantity, _risk_cash, _notional = size_position(
            account, executable_entry, plan.stop, self.config
        )
        quantity = min(quantity, plan.quantity)
        if quantity <= 0:
            return IRSExecutionResult(
                False,
                False,
                order_ref,
                candidate.state,
                (RejectionReason.BUYING_POWER.value,),
            )
        results = self.broker.place_stop_limit_bracket_order(
            candidate.symbol,
            "BUY" if candidate.direction is Direction.LONG else "SELL",
            quantity,
            float(plan.entry_stop),
            float(plan.entry_limit),
            float(plan.stop),
            float(plan.target),
            order_ref=order_ref,
            tif="DAY",
            outside_rth=False,
        )
        statuses = [str(getattr(item, "status", "") or "") for item in results]
        if any(status.lower() in FAILURE_STATUSES for status in statuses):
            if self.alerter is not None:
                self.alerter.send_error(
                    f"PAPER IRS bracket rejected {candidate.symbol} ref={order_ref}; "
                    "new entries locked."
                )
            return IRSExecutionResult(
                False,
                False,
                order_ref,
                candidate.state,
                ("BROKER_REJECTED",),
                tuple(int(getattr(item, "order_id", 0) or 0) for item in results),
                quantity,
                True,
            )
        ids = tuple(int(getattr(item, "order_id", 0) or 0) for item in results)
        self.store.record_order(
            signal_id=candidate.signal_id,
            order_ref=order_ref,
            broker_order_id=ids[0],
            status=statuses[0],
            quantity=quantity,
            submitted_at=as_of,
            payload={
                "setup_id": candidate.setup_id,
                "parent_order_id": ids[0],
                "stop_order_id": ids[1],
                "target_order_id": ids[2],
                "statuses": statuses,
                "quote": quote,
                "paper": True,
            },
        )
        transition = advance_setup_state(
            setup_id=candidate.setup_id,
            previous=SetupState.ENTRY_ARMED,
            new=SetupState.ORDER_SUBMITTED,
            exchange_timestamp=as_of,
            reason="PAPER stop-limit bracket accepted",
            source_bar_id=candidate.confirmation.bar_id if candidate.confirmation else None,
            run_id=run_id,
            strategy_version=candidate.strategy_version,
        )
        self.store.record_transition(transition)
        if self.alerter is not None:
            self.alerter.send(
                f"PAPER IRS order submitted {candidate.symbol} {candidate.direction.value} "
                f"qty={quantity} entry={plan.entry_stop}/{plan.entry_limit} "
                f"stop={plan.stop} target={plan.target} ref={order_ref}; "
                "protective orders placed."
            )
        return IRSExecutionResult(
            True,
            False,
            order_ref,
            SetupState.ORDER_SUBMITTED,
            (),
            ids,
            quantity,
        )

    def reconcile_partial_fill(
        self,
        *,
        signal_id: str,
        order_ref: str,
        filled_quantity: int,
        as_of: datetime,
        run_id: str,
    ) -> IRSExecutionResult:
        """Resize both protective children to exactly the filled quantity."""
        order = self.store.get_order(order_ref)
        if order is None:
            return IRSExecutionResult(
                False, False, order_ref, SetupState.REJECTED_BY_DATA, ("LOCAL_ORDER_STATE_MISSING",), global_entry_lock=True
            )
        setup_id = str(order["payload"].get("setup_id") or "")
        setup = self.store.get_setup(setup_id)
        if setup is None:
            return IRSExecutionResult(
                False, False, order_ref, SetupState.REJECTED_BY_DATA, ("LOCAL_ORDER_STATE_MISSING",), global_entry_lock=True
            )
        if filled_quantity <= 0:
            return IRSExecutionResult(
                False, False, order_ref, SetupState.ORDER_SUBMITTED, ("NO_FILL",)
            )
        payload = order["payload"]
        child_ids = [
            int(payload.get("stop_order_id") or 0),
            int(payload.get("target_order_id") or 0),
        ]
        protected = all(
            order_id > 0 and self.broker.resize_open_order(order_id, filled_quantity)
            for order_id in child_ids
        )
        if not protected:
            if self.alerter is not None:
                self.alerter.send_error(
                    f"PAPER IRS protection resize failed ref={order_ref}; new entries locked."
                )
            return IRSExecutionResult(
                False,
                False,
                order_ref,
                SetupState.ORDER_SUBMITTED,
                (RejectionReason.PROTECTIVE_STOP_UNAVAILABLE.value,),
                tuple(child_ids),
                filled_quantity,
                True,
            )
        previous = SetupState(str(setup["state"]))
        new = SetupState.ORDER_PARTIALLY_FILLED
        if previous is SetupState.ORDER_SUBMITTED:
            transition = advance_setup_state(
                setup_id=setup_id,
                previous=previous,
                new=new,
                exchange_timestamp=as_of,
                reason=f"Partial fill protected quantity={filled_quantity}",
                source_bar_id=None,
                run_id=run_id,
            )
            self.store.record_transition(transition)
        if self.alerter is not None:
            self.alerter.send(
                f"PAPER IRS partial fill ref={order_ref} qty={filled_quantity}; "
                "stop and target protection resized."
            )
        return IRSExecutionResult(
            False, False, order_ref, new, (), tuple(child_ids), filled_quantity
        )

    def reconcile_after_reconnect(self) -> IRSReconnectResult:
        """Audit local IRS orders against IBKR without ever repeating an entry."""
        policy_rejections = self._policy_rejections()
        if policy_rejections or not self._verify_paper_account():
            reasons = tuple(
                dict.fromkeys(
                    (
                        *policy_rejections,
                        RejectionReason.LIVE_TRADING_DISABLED.value,
                    )
                )
            )
            return IRSReconnectResult(False, True, (), reasons)

        broker_orders = [
            dict(item)
            for item in self.broker.get_open_orders()
            if str(item.get("order_ref") or "").startswith("IRS-")
        ]
        positions = {
            str(item.get("symbol") or "").strip().upper(): abs(
                int(float(item.get("position") or 0))
            )
            for item in self.broker.get_positions()
            if float(item.get("position") or 0) != 0
        }
        local_orders = self.store.list_orders()
        local_refs = {str(item["order_ref"]) for item in local_orders}
        reasons: list[str] = []
        reconciled: list[str] = []

        for broker_order in broker_orders:
            broker_ref = str(broker_order.get("order_ref") or "")
            base_ref = (
                broker_ref[:-3]
                if broker_ref.endswith(("-SL", "-TP"))
                else broker_ref
            )
            if base_ref not in local_refs:
                reasons.append(f"BROKER_ONLY_ORDER:{base_ref}")

        for local in local_orders:
            order_ref = str(local["order_ref"])
            payload = local["payload"]
            setup = self.store.get_setup(str(payload.get("setup_id") or ""))
            if setup is None:
                reasons.append(f"LOCAL_SETUP_MISSING:{order_ref}")
                continue
            symbol = str(setup.get("symbol") or "").upper()
            position_quantity = positions.get(symbol, 0)
            matches = [
                item
                for item in broker_orders
                if str(item.get("order_ref") or "") in {
                    order_ref,
                    f"{order_ref}-SL",
                    f"{order_ref}-TP",
                }
            ]
            parent = [item for item in matches if str(item.get("order_ref") or "") == order_ref]
            stops = [item for item in matches if str(item.get("order_ref") or "") == f"{order_ref}-SL"]
            targets = [item for item in matches if str(item.get("order_ref") or "") == f"{order_ref}-TP"]

            if len(parent) > 1 or len(stops) > 1 or len(targets) > 1:
                reasons.append(f"DUPLICATE_BROKER_ORDER:{order_ref}")
                continue
            if position_quantity:
                protected_quantities = [
                    int(float(item.get("quantity") or 0))
                    for item in (*stops, *targets)
                ]
                if (
                    len(stops) != 1
                    or len(targets) != 1
                    or protected_quantities != [position_quantity, position_quantity]
                ):
                    reasons.append(f"PROTECTION_MISMATCH:{order_ref}")
                    continue
            elif not parent:
                reasons.append(f"ENTRY_STATE_UNCERTAIN:{order_ref}")
                continue
            reconciled.append(order_ref)

        unique_reasons = tuple(dict.fromkeys(reasons))
        if unique_reasons and self.alerter is not None:
            self.alerter.send_error(
                "PAPER IRS reconnect discrepancy; new entries locked: "
                + ", ".join(unique_reasons)
            )
        return IRSReconnectResult(
            safe=not unique_reasons,
            global_entry_lock=bool(unique_reasons),
            reconciled_order_refs=tuple(reconciled),
            reasons=unique_reasons,
        )

    def cancel_expired_orders(
        self,
        *,
        as_of: datetime,
        run_id: str,
    ) -> IRSExpiryCancellationResult:
        """Cancel expired unfilled brackets while preserving filled positions."""
        if not self.broker.is_connected or not self._verify_paper_account():
            return IRSExpiryCancellationResult(
                (),
                True,
                (
                    RejectionReason.BROKER_DISCONNECTED.value
                    if not self.broker.is_connected
                    else RejectionReason.LIVE_TRADING_DISABLED.value,
                ),
            )
        broker_orders = self.broker.get_open_orders()
        positions = {
            str(item.get("symbol") or "").strip().upper()
            for item in self.broker.get_positions()
            if float(item.get("position") or 0) != 0
        }
        cancelled: list[str] = []
        reasons: list[str] = []
        for local in self.store.list_orders():
            payload = local["payload"]
            setup_id = str(payload.get("setup_id") or "")
            setup = self.store.get_setup(setup_id)
            if setup is None or str(setup.get("state")) != SetupState.ORDER_SUBMITTED.value:
                continue
            expires_at = setup.get("expires_at")
            if not expires_at or datetime.fromisoformat(str(expires_at)) > as_of:
                continue
            order_ref = str(local["order_ref"])
            symbol = str(setup.get("symbol") or "").upper()
            if symbol in positions:
                reasons.append(f"EXPIRED_ENTRY_HAS_POSITION:{order_ref}")
                continue
            matching = [
                item
                for item in broker_orders
                if str(item.get("order_ref") or "") in {
                    order_ref,
                    f"{order_ref}-SL",
                    f"{order_ref}-TP",
                }
            ]
            cancellation_ok = all(
                int(item.get("order_id") or 0) > 0
                and self.broker.cancel_order(int(item["order_id"]))
                for item in matching
            )
            if not cancellation_ok:
                reasons.append(f"EXPIRY_CANCELLATION_FAILED:{order_ref}")
                continue
            transition = advance_setup_state(
                setup_id=setup_id,
                previous=SetupState.ORDER_SUBMITTED,
                new=SetupState.CANCELLED,
                exchange_timestamp=as_of,
                reason="PAPER entry bracket expired before fill",
                source_bar_id=None,
                run_id=run_id,
            )
            self.store.record_transition(transition)
            self.store.update_order_status(
                order_ref,
                status="Cancelled",
                updated_at=as_of,
            )
            cancelled.append(order_ref)
            if self.alerter is not None:
                self.alerter.send(
                    f"PAPER IRS expired bracket cancelled {symbol} ref={order_ref}."
                )
        unique_reasons = tuple(dict.fromkeys(reasons))
        if unique_reasons and self.alerter is not None:
            self.alerter.send_error(
                "PAPER IRS expiry discrepancy; new entries locked: "
                + ", ".join(unique_reasons)
            )
        return IRSExpiryCancellationResult(
            tuple(cancelled),
            bool(unique_reasons),
            unique_reasons,
        )
