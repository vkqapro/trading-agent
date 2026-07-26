from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from src.backtest.inefficiency_reclaim import (
    BacktestCosts,
    TradeLabel,
    build_backtest_report,
    replay_candidate,
    resolve_exit_sequence,
    walk_forward_report,
)
from src.strategy.inefficiency_reclaim import (
    Bar,
    ConfirmationEvent,
    ConfirmationType,
    Direction,
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


def candidate() -> StrategyCandidate:
    created = datetime(2026, 7, 1, 10, 30, tzinfo=ET)
    confirmation_time = datetime(2026, 7, 1, 11, 30, tzinfo=ET)
    zone = InefficiencyZone(
        zone_id="zone-backtest",
        symbol="TEST",
        direction=Direction.LONG,
        zone_type=ZoneType.STRICT_THREE_BAR_GAP,
        source_timeframe="1 hour",
        created_at=created,
        displacement_bar_time=created,
        zone_low=D("99.5"),
        zone_high=D("100.5"),
        zone_mid=D("100"),
        zone_width=D("1"),
        zone_width_atr=D("0.4"),
        displacement_atr_multiple=D("1.7"),
        body_ratio=D("0.75"),
        close_location=D("0.85"),
        relative_volume=D("1.6"),
        source_bar_ids=("a", "b", "c"),
        structure_reference_id="balance_high:99",
        expires_at=created + timedelta(hours=7),
    )
    confirmation = ConfirmationEvent(
        ConfirmationType.SWEEP_AND_RECLAIM,
        confirmation_time,
        "confirmation",
        D("100.8"),
        D("99.7"),
        D("100"),
        D("0.9"),
        {},
    )
    breakdown = ScoreBreakdown(
        D("15"), D("14"), D("9"), D("14"), D("10"), D("14"), D("10"), D("10")
    )
    return StrategyCandidate(
        signal_id="signal-backtest",
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
        order_plan=OrderPlan(
            entry_stop=D("101"),
            entry_limit=D("101.05"),
            stop=D("99"),
            target=D("103.5"),
            risk_per_share=D("2.065"),
            reward_per_share=D("2.485"),
            structural_r=D("1.2"),
            estimated_costs=D("0.015"),
            quantity=10,
            risk_cash=D("250"),
            required_buying_power=D("1010.5"),
        ),
        expires_at=confirmation_time + timedelta(minutes=30),
        hard_rejections=(),
        soft_warnings=(),
        diagnostics={},
        explanation="PAPER.",
    )


def bar(
    timestamp: datetime,
    *,
    timeframe: str = "15 mins",
    open_: str = "101",
    high: str = "104",
    low: str = "98",
    close: str = "102",
    complete: bool = True,
) -> Bar:
    return Bar(
        symbol="TEST",
        timeframe=timeframe,
        timestamp=timestamp,
        open=D(open_),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=100_000,
        is_complete=complete,
    )


class BacktestReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candidate = candidate()
        self.parent_time = datetime(2026, 7, 1, 11, 45, tzinfo=ET)

    def test_same_parent_bar_uses_5m_sequence(self) -> None:
        parent = bar(self.parent_time)
        five = [
            bar(self.parent_time - timedelta(minutes=10), timeframe="5 mins", high="102", low="100", close="101.5"),
            bar(self.parent_time - timedelta(minutes=5), timeframe="5 mins", high="104", low="101", close="103.6"),
        ]
        label = resolve_exit_sequence(
            direction=Direction.LONG,
            parent_bar=parent,
            stop=D("99"),
            target=D("103.5"),
            bars_5m=five,
            bars_1m=[],
        )
        self.assertEqual(label, TradeLabel.WIN)

    def test_same_parent_bar_without_lower_timeframe_is_ambiguous(self) -> None:
        label = resolve_exit_sequence(
            direction=Direction.LONG,
            parent_bar=bar(self.parent_time),
            stop=D("99"),
            target=D("103.5"),
            bars_5m=[],
            bars_1m=[],
        )
        self.assertEqual(label, TradeLabel.AMBIGUOUS)

    def test_ambiguous_trade_is_counted_as_conservative_loss(self) -> None:
        result = replay_candidate(
            self.candidate,
            future_15m=[bar(self.parent_time)],
            bars_5m=[],
            bars_1m=[],
        )
        self.assertEqual(result.label, TradeLabel.AMBIGUOUS)
        self.assertEqual(result.conservative_label, TradeLabel.LOSS)
        self.assertLess(result.net_r, 0)

    def test_stop_limit_gap_can_produce_no_fill(self) -> None:
        result = replay_candidate(
            self.candidate,
            future_15m=[
                bar(
                    self.parent_time,
                    open_="102.5",
                    high="104",
                    low="102",
                    close="103",
                )
            ],
        )
        self.assertEqual(result.label, TradeLabel.NO_FILL)

    def test_incomplete_future_bar_cannot_fill(self) -> None:
        result = replay_candidate(
            self.candidate,
            future_15m=[bar(self.parent_time, complete=False)],
        )
        self.assertEqual(result.label, TradeLabel.EXPIRED)

    def test_costs_reduce_net_r_and_pnl(self) -> None:
        parent = bar(self.parent_time, high="104", low="100", close="103.6")
        cheap = replay_candidate(
            self.candidate,
            future_15m=[parent],
            costs=BacktestCosts(D("0"), D("0"), D("0")),
        )
        expensive = replay_candidate(
            self.candidate,
            future_15m=[parent],
            costs=BacktestCosts(D("0.02"), D("0.05"), D("0.05")),
        )
        self.assertEqual(cheap.label, TradeLabel.WIN)
        self.assertLess(expensive.net_r, cheap.net_r)
        self.assertLess(expensive.pnl, cheap.pnl)

    def test_report_has_required_slices_and_walk_forward(self) -> None:
        trade = replay_candidate(
            self.candidate,
            future_15m=[
                bar(self.parent_time, high="104", low="100", close="103.6")
            ],
        )
        report = build_backtest_report([trade])
        walk = walk_forward_report([trade, trade, trade])
        self.assertIn("expectancy_r", report)
        self.assertIn("maximum_drawdown", report)
        self.assertIn("by_score", report)
        self.assertIn("by_zone_type", report)
        self.assertIn("oos", walk)
        self.assertFalse(walk["parameters_fitted_per_ticker"])


if __name__ == "__main__":
    unittest.main()
