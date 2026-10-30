from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from src.execution.inefficiency_reclaim import (
    IRSExecutionPolicy,
    InefficiencyReclaimPaperExecutor,
)
from src.storage.inefficiency_reclaim_store import InefficiencyReclaimStore
from src.strategy.inefficiency_reclaim import (
    AccountState,
    ConfirmationEvent,
    ConfirmationType,
    Direction,
    IRSConfig,
    InefficiencyZone,
    OrderPlan,
    ScoreBreakdown,
    SetupState,
    StrategyCandidate,
    StrategyProfile,
    ZoneType,
)


ET = ZoneInfo("America/New_York")
D = Decimal


def armed_candidate() -> StrategyCandidate:
    now = datetime(2026, 7, 1, 10, 30, tzinfo=ET)
    zone = InefficiencyZone(
        zone_id="zone-execution",
        symbol="TEST",
        direction=Direction.LONG,
        zone_type=ZoneType.LOW_OVERLAP_DISPLACEMENT,
        source_timeframe="1 hour",
        created_at=now,
        displacement_bar_time=now,
        zone_low=D("99.5"),
        zone_high=D("100.5"),
        zone_mid=D("100"),
        zone_width=D("1"),
        zone_width_atr=D("0.4"),
        displacement_atr_multiple=D("1.7"),
        body_ratio=D("0.75"),
        close_location=D("0.85"),
        relative_volume=D("1.6"),
        source_bar_ids=("a", "b"),
        structure_reference_id="balance_high:99",
        expires_at=now + timedelta(hours=7),
    )
    confirmation = ConfirmationEvent(
        ConfirmationType.SWEEP_AND_RECLAIM,
        now + timedelta(hours=1),
        "confirm-bar",
        D("100.8"),
        D("99.7"),
        D("100"),
        D("0.9"),
        {},
    )
    breakdown = ScoreBreakdown(
        D("15"), D("14"), D("9"), D("14"), D("10"), D("14"), D("10"), D("10")
    )
    plan = OrderPlan(
        entry_stop=D("101"),
        entry_limit=D("101.05"),
        stop=D("99"),
        target=D("103.5"),
        risk_per_share=D("2.065"),
        reward_per_share=D("2.485"),
        structural_r=D("1.20"),
        estimated_costs=D("0.015"),
        quantity=10,
        risk_cash=D("250"),
        required_buying_power=D("1010.5"),
    )
    return StrategyCandidate(
        signal_id="signal-execution",
        symbol="TEST",
        strategy="INEFFICIENCY_RECLAIM",
        profile=StrategyProfile.INTRADAY,
        direction=Direction.LONG,
        state=SetupState.ENTRY_ARMED,
        score=breakdown.total,
        score_breakdown=breakdown,
        zone=zone,
        displacement_metrics={},
        retrace_metrics={},
        confirmation=confirmation,
        order_plan=plan,
        expires_at=now + timedelta(hours=2),
        hard_rejections=(),
        soft_warnings=(),
        diagnostics={},
        explanation="PAPER.",
    )


class BrokerStub:
    def __init__(self) -> None:
        self.is_connected = True
        self.placed = 0
        self.open_orders: list[dict] = []
        self.quote_timestamp = datetime(2026, 7, 1, 11, 35, tzinfo=ET)
        self.statuses = ("Submitted", "PreSubmitted", "PreSubmitted")
        self.resized: list[tuple[int, int]] = []
        self.positions: list[dict] = []
        self.cancelled: list[int] = []

    def get_account_summary(self):
        return [{"account": "DU12345", "tag": "NetLiquidation", "value": "100000"}]

    def get_open_orders(self):
        return list(self.open_orders)

    def get_positions(self):
        return list(self.positions)

    def get_market_price(self, _symbol):
        return {
            "bid": 100.98,
            "ask": 101.02,
            "last": 101,
            "timestamp": self.quote_timestamp.isoformat(),
        }

    def place_stop_limit_bracket_order(self, *_args, **_kwargs):
        self.placed += 1
        return tuple(
            SimpleNamespace(order_id=index + 10, status=status)
            for index, status in enumerate(self.statuses)
        )

    def resize_open_order(self, order_id, quantity):
        self.resized.append((order_id, quantity))
        return True

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return True


class PaperExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = InefficiencyReclaimStore(Path(self.temp.name) / "irs.db")
        self.candidate = armed_candidate()
        self.store.upsert_candidate(self.candidate, run_id="scan")
        self.broker = BrokerStub()
        self.policy = IRSExecutionPolicy(
            enabled=True,
            paper_trading=True,
            allow_live_trading=False,
            trading_mode="paper",
        )
        self.executor = InefficiencyReclaimPaperExecutor(
            self.broker,
            self.store,
            config=IRSConfig(),
            policy=self.policy,
        )
        self.now = datetime(2026, 7, 1, 11, 35, tzinfo=ET)
        self.account = AccountState(D("100000"), D("100000"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_valid_candidate_submits_one_stop_limit_bracket(self) -> None:
        result = self.executor.submit(
            self.candidate,
            as_of=self.now,
            account=self.account,
            run_id="execute",
        )
        self.assertTrue(result.submitted)
        self.assertEqual(result.state, SetupState.ORDER_SUBMITTED)
        self.assertEqual(self.broker.placed, 1)
        self.assertEqual(self.store.table_count("orders"), 1)
        self.assertEqual(self.store.get_setup(self.candidate.setup_id)["state"], "ORDER_SUBMITTED")

    def test_duplicate_scheduler_run_does_not_place_second_order(self) -> None:
        first = self.executor.submit(
            self.candidate, as_of=self.now, account=self.account, run_id="execute-1"
        )
        second = self.executor.submit(
            self.candidate, as_of=self.now, account=self.account, run_id="execute-2"
        )
        self.assertTrue(first.submitted)
        self.assertTrue(second.duplicate_prevented)
        self.assertEqual(self.broker.placed, 1)

    def test_live_or_disabled_policy_never_calls_broker(self) -> None:
        executor = InefficiencyReclaimPaperExecutor(
            self.broker,
            self.store,
            config=IRSConfig(),
            policy=IRSExecutionPolicy(
                enabled=True,
                paper_trading=False,
                allow_live_trading=False,
                trading_mode="paper",
            ),
        )
        result = executor.submit(
            self.candidate, as_of=self.now, account=self.account, run_id="execute"
        )
        self.assertFalse(result.submitted)
        self.assertIn("LIVE_TRADING_DISABLED", result.reasons)
        self.assertEqual(self.broker.placed, 0)

    def test_non_paper_account_is_rejected(self) -> None:
        self.broker.get_account_summary = lambda: [{"account": "U12345"}]
        result = self.executor.submit(
            self.candidate, as_of=self.now, account=self.account, run_id="execute"
        )
        self.assertFalse(result.submitted)
        self.assertIn("LIVE_TRADING_DISABLED", result.reasons)

    def test_stale_quote_blocks_submission(self) -> None:
        self.broker.quote_timestamp = self.now - timedelta(seconds=10)
        result = self.executor.submit(
            self.candidate, as_of=self.now, account=self.account, run_id="execute"
        )
        self.assertFalse(result.submitted)
        self.assertIn("STALE_QUOTE", result.reasons)
        self.assertEqual(self.broker.placed, 0)

    def test_quote_beyond_entry_limit_is_not_chased(self) -> None:
        self.broker.get_market_price = lambda _symbol: {
            "bid": 101.07,
            "ask": 101.08,
            "last": 101.08,
            "timestamp": self.now.isoformat(),
        }
        result = self.executor.submit(
            self.candidate, as_of=self.now, account=self.account, run_id="execute"
        )
        self.assertFalse(result.submitted)
        self.assertIn("ENTRY_OVEREXTENDED", result.reasons)
        self.assertEqual(self.broker.placed, 0)

    def test_partial_fill_resizes_both_children(self) -> None:
        submitted = self.executor.submit(
            self.candidate, as_of=self.now, account=self.account, run_id="execute"
        )
        result = self.executor.reconcile_partial_fill(
            signal_id=self.candidate.signal_id,
            order_ref=submitted.order_ref,
            filled_quantity=4,
            as_of=self.now + timedelta(seconds=1),
            run_id="fill",
        )
        self.assertEqual(result.state, SetupState.ORDER_PARTIALLY_FILLED)
        self.assertEqual(self.broker.resized, [(11, 4), (12, 4)])
        self.assertFalse(result.global_entry_lock)

    def test_reconnect_reconciles_protected_position_without_reentry(self) -> None:
        submitted = self.executor.submit(
            self.candidate, as_of=self.now, account=self.account, run_id="execute"
        )
        self.store.record_fill(
            order_ref=submitted.order_ref,
            execution_id="fill-reconnect",
            quantity=4,
            price=D("101"),
            filled_at=self.now,
        )
        self.broker.positions = [{"symbol": "TEST", "position": 4}]
        self.broker.open_orders = [
            {"order_ref": f"{submitted.order_ref}-SL", "quantity": 4},
            {"order_ref": f"{submitted.order_ref}-TP", "quantity": 4},
        ]
        result = self.executor.reconcile_after_reconnect()
        self.assertTrue(result.safe)
        self.assertFalse(result.global_entry_lock)
        self.assertEqual(result.reconciled_order_refs, (submitted.order_ref,))
        self.assertEqual(self.broker.placed, 1)

    def test_reconnect_missing_protection_locks_new_entries_and_alerts_paper(self) -> None:
        class Alerter:
            def __init__(self):
                self.messages = []

            def send(self, message):
                self.messages.append(message)

            def send_error(self, message):
                self.messages.append(message)

        alerter = Alerter()
        executor = InefficiencyReclaimPaperExecutor(
            self.broker,
            self.store,
            config=IRSConfig(),
            policy=self.policy,
            alerter=alerter,
        )
        submitted = executor.submit(
            self.candidate, as_of=self.now, account=self.account, run_id="execute"
        )
        self.broker.positions = [{"symbol": "TEST", "position": 4}]
        self.broker.open_orders = [
            {"order_ref": f"{submitted.order_ref}-SL", "quantity": 4}
        ]
        result = executor.reconcile_after_reconnect()
        self.assertFalse(result.safe)
        self.assertTrue(result.global_entry_lock)
        self.assertIn(f"PROTECTION_MISMATCH:{submitted.order_ref}", result.reasons)
        self.assertTrue(alerter.messages)
        self.assertTrue(all("PAPER" in message for message in alerter.messages))

    def test_eod_cancels_expired_unfilled_bracket(self) -> None:
        submitted = self.executor.submit(
            self.candidate, as_of=self.now, account=self.account, run_id="execute"
        )
        self.broker.open_orders = [
            {"order_ref": submitted.order_ref, "order_id": 10},
            {"order_ref": f"{submitted.order_ref}-SL", "order_id": 11},
            {"order_ref": f"{submitted.order_ref}-TP", "order_id": 12},
        ]
        result = self.executor.cancel_expired_orders(
            as_of=self.candidate.expires_at + timedelta(seconds=1),
            run_id="eod",
        )
        self.assertEqual(result.cancelled_order_refs, (submitted.order_ref,))
        self.assertFalse(result.global_entry_lock)
        self.assertEqual(self.broker.cancelled, [10, 11, 12])
        self.assertEqual(
            self.store.get_setup(self.candidate.setup_id)["state"],
            SetupState.CANCELLED.value,
        )

    def test_broker_child_rejection_locks_new_entries(self) -> None:
        self.broker.statuses = ("Submitted", "Inactive", "PreSubmitted")
        result = self.executor.submit(
            self.candidate, as_of=self.now, account=self.account, run_id="execute"
        )
        self.assertFalse(result.submitted)
        self.assertTrue(result.global_entry_lock)
        self.assertEqual(self.store.table_count("orders"), 0)


if __name__ == "__main__":
    unittest.main()
