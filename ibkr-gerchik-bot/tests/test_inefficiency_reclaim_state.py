from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from src.storage.inefficiency_reclaim_store import (
    ImmutableZoneError,
    InefficiencyReclaimStore,
)
from src.strategy.inefficiency_reclaim import (
    Direction,
    InefficiencyZone,
    ScoreBreakdown,
    SetupState,
    StrategyCandidate,
    StrategyProfile,
    ZoneType,
)
from src.strategy.inefficiency_reclaim_state import (
    ALLOWED_TRANSITIONS,
    InvalidStateTransition,
    TERMINAL_STATES,
    advance_setup_state,
)


ET = ZoneInfo("America/New_York")
D = Decimal


def zone() -> InefficiencyZone:
    now = datetime(2026, 7, 1, 10, 30, tzinfo=ET)
    return InefficiencyZone(
        zone_id="zone-state-test",
        symbol="TEST",
        direction=Direction.LONG,
        zone_type=ZoneType.LOW_OVERLAP_DISPLACEMENT,
        source_timeframe="1 hour",
        created_at=now,
        displacement_bar_time=now,
        zone_low=D("100"),
        zone_high=D("101"),
        zone_mid=D("100.5"),
        zone_width=D("1"),
        zone_width_atr=D("0.4"),
        displacement_atr_multiple=D("1.7"),
        body_ratio=D("0.75"),
        close_location=D("0.85"),
        relative_volume=D("1.6"),
        source_bar_ids=("bar-a", "bar-b"),
        structure_reference_id="balance_high:99",
        expires_at=now + timedelta(hours=7),
    )


def candidate(state: SetupState = SetupState.WAITING_FOR_RETRACE) -> StrategyCandidate:
    breakdown = ScoreBreakdown(
        D("15"), D("15"), D("10"), D("15"), D("0"), D("0"), D("10"), D("0")
    )
    item_zone = zone()
    return StrategyCandidate(
        signal_id="signal-state-test",
        symbol="TEST",
        strategy="INEFFICIENCY_RECLAIM",
        profile=StrategyProfile.INTRADAY,
        direction=Direction.LONG,
        state=state,
        score=breakdown.total,
        score_breakdown=breakdown,
        zone=item_zone,
        displacement_metrics={"atr_multiple": D("1.7")},
        retrace_metrics={},
        confirmation=None,
        order_plan=None,
        expires_at=item_zone.expires_at,
        hard_rejections=(),
        soft_warnings=(),
        diagnostics={},
        explanation="PAPER.",
    )


class TransitionTableTests(unittest.TestCase):
    def test_every_declared_transition_is_allowed(self) -> None:
        timestamp = datetime(2026, 7, 1, 11, tzinfo=ET)
        for previous, targets in ALLOWED_TRANSITIONS.items():
            for new in targets:
                with self.subTest(previous=previous, new=new):
                    result = advance_setup_state(
                        setup_id="setup",
                        previous=previous,
                        new=new,
                        exchange_timestamp=timestamp,
                        reason="test",
                        source_bar_id="bar",
                        run_id="run",
                    )
                    self.assertEqual(result.new_state, new)

    def test_every_undeclared_nonterminal_transition_is_forbidden(self) -> None:
        timestamp = datetime(2026, 7, 1, 11, tzinfo=ET)
        for previous in SetupState:
            if previous in TERMINAL_STATES:
                continue
            allowed = ALLOWED_TRANSITIONS.get(previous, frozenset())
            for new in SetupState:
                if new in allowed:
                    continue
                with self.subTest(previous=previous, new=new):
                    with self.assertRaises(InvalidStateTransition):
                        advance_setup_state(
                            setup_id="setup",
                            previous=previous,
                            new=new,
                            exchange_timestamp=timestamp,
                            reason="test",
                            source_bar_id=None,
                            run_id="run",
                        )

    def test_terminal_state_cannot_jump_backward(self) -> None:
        for terminal in TERMINAL_STATES:
            with self.subTest(terminal=terminal):
                with self.assertRaises(InvalidStateTransition):
                    advance_setup_state(
                        setup_id="setup",
                        previous=terminal,
                        new=SetupState.SEARCHING,
                        exchange_timestamp=datetime(2026, 7, 1, 11, tzinfo=ET),
                        reason="forbidden",
                        source_bar_id=None,
                        run_id="run",
                    )


class PersistentStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = InefficiencyReclaimStore(Path(self.temp.name) / "irs.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_duplicate_scan_creates_one_zone_setup_and_signal(self) -> None:
        item = candidate()
        first = self.store.upsert_candidate(item, run_id="run-1")
        second = self.store.upsert_candidate(item, run_id="run-2")

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(self.store.table_count("inefficiency_zones"), 1)
        self.assertEqual(self.store.table_count("strategy_setups"), 1)
        self.assertEqual(self.store.table_count("strategy_signals"), 1)

    def test_new_confirmation_keeps_setup_and_adds_signal(self) -> None:
        first = candidate(SetupState.WAITING_FOR_CONFIRMATION)
        second = replace(
            first,
            signal_id="signal-state-test-confirmed",
            state=SetupState.ENTRY_ARMED,
        )
        self.assertTrue(self.store.upsert_candidate(first, run_id="run-1"))
        self.assertTrue(self.store.upsert_candidate(second, run_id="run-2"))
        self.assertEqual(first.setup_id, second.setup_id)
        self.assertEqual(self.store.table_count("inefficiency_zones"), 1)
        self.assertEqual(self.store.table_count("strategy_setups"), 1)
        self.assertEqual(self.store.table_count("strategy_signals"), 2)

    def test_zone_boundaries_cannot_move(self) -> None:
        item = candidate()
        self.store.upsert_candidate(item, run_id="run-1")
        moved = replace(item.zone, zone_low=D("99.5"))
        with self.assertRaises(ImmutableZoneError):
            self.store.upsert_zone(moved)

    def test_transition_is_idempotent_and_updates_setup(self) -> None:
        item = candidate()
        self.store.upsert_candidate(item, run_id="run-1")
        transition = advance_setup_state(
            setup_id=item.setup_id,
            previous=SetupState.WAITING_FOR_RETRACE,
            new=SetupState.RETRACE_DETECTED,
            exchange_timestamp=datetime(2026, 7, 1, 12, tzinfo=ET),
            reason="zone touched",
            source_bar_id="bar-c",
            run_id="run-2",
        )

        self.assertTrue(self.store.record_transition(transition))
        self.assertFalse(self.store.record_transition(transition))
        self.assertEqual(self.store.table_count("strategy_state_transitions"), 4)
        self.assertEqual(self.store.active_setups()[0]["state"], SetupState.RETRACE_DETECTED.value)

    def test_order_and_fill_ids_are_unique(self) -> None:
        item = candidate()
        self.store.upsert_candidate(item, run_id="run-1")
        now = datetime(2026, 7, 1, 12, tzinfo=ET)
        self.assertTrue(
            self.store.record_order(
                signal_id=item.signal_id,
                order_ref="IRS-signal-state-test",
                broker_order_id=42,
                status="Submitted",
                quantity=10,
                submitted_at=now,
                payload={},
            )
        )
        self.assertFalse(
            self.store.record_order(
                signal_id=item.signal_id,
                order_ref="IRS-signal-state-test",
                broker_order_id=43,
                status="Submitted",
                quantity=10,
                submitted_at=now,
                payload={},
            )
        )
        self.assertTrue(
            self.store.record_fill(
                order_ref="IRS-signal-state-test",
                execution_id="exec-1",
                quantity=4,
                price=D("101"),
                filled_at=now,
            )
        )
        self.assertFalse(
            self.store.record_fill(
                order_ref="IRS-signal-state-test",
                execution_id="exec-1",
                quantity=4,
                price=D("101"),
                filled_at=now,
            )
        )
        self.assertEqual(self.store.table_count("orders"), 1)
        self.assertEqual(self.store.table_count("fills"), 1)

    def test_risk_and_news_snapshots_are_idempotent(self) -> None:
        item = candidate()
        self.store.upsert_candidate(item, run_id="run-1")
        now = datetime(2026, 7, 1, 12, tzinfo=ET)
        self.assertTrue(
            self.store.record_risk_snapshot(
                signal_id=item.signal_id,
                captured_at=now,
                payload={"paper": True},
            )
        )
        self.assertFalse(
            self.store.record_risk_snapshot(
                signal_id=item.signal_id,
                captured_at=now,
                payload={"paper": True},
            )
        )
        self.assertTrue(
            self.store.record_news_check(
                signal_id=item.signal_id,
                symbol=item.symbol,
                checked_at=now,
                news_status="CLEAR",
                earnings_status="CLEAR",
                payload={"source": "test"},
            )
        )
        self.assertFalse(
            self.store.record_news_check(
                signal_id=item.signal_id,
                symbol=item.symbol,
                checked_at=now,
                news_status="CLEAR",
                earnings_status="CLEAR",
                payload={"source": "test"},
            )
        )
        self.assertEqual(self.store.table_count("risk_snapshots"), 1)
        self.assertEqual(self.store.table_count("news_checks"), 1)


if __name__ == "__main__":
    unittest.main()
