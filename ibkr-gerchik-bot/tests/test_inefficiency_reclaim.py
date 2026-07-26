from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from src.strategy.inefficiency_reclaim import (
    AccountState,
    Bar,
    ConfirmationType,
    Direction,
    IRSConfig,
    InefficiencyZone,
    MarketRegime,
    Quote,
    RiskStatus,
    StrategyContext,
    StructuralLevel,
    ZoneType,
    build_low_overlap_zone,
    build_strict_gap_zone,
    calculate_entry_trigger,
    calculate_overlap_ratio,
    calculate_relative_volume,
    calculate_robust_atr,
    calculate_structural_r,
    choose_confirmation,
    detect_displacement,
    detect_micro_break_of_structure,
    detect_sweep_and_reclaim,
    detect_two_bar_reclaim,
    evaluate_hard_gates,
    evaluate_retracement,
    size_position,
)


ET = ZoneInfo("America/New_York")
D = Decimal


def make_bar(
    index: int,
    *,
    timeframe: str = "1 hour",
    open_: str = "100.0",
    high: str = "100.6",
    low: str = "99.6",
    close: str = "100.2",
    volume: int = 1_000_000,
    complete: bool = True,
) -> Bar:
    return Bar(
        symbol="TEST",
        timeframe=timeframe,
        timestamp=datetime(2026, 7, 1, 9, 30, tzinfo=ET) + timedelta(hours=index),
        open=D(open_),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=volume,
        is_complete=complete,
    )


def make_zone(direction: Direction = Direction.LONG) -> InefficiencyZone:
    created = datetime(2026, 7, 1, 10, 30, tzinfo=ET)
    return InefficiencyZone(
        zone_id="zone-test",
        symbol="TEST",
        direction=direction,
        zone_type=ZoneType.LOW_OVERLAP_DISPLACEMENT,
        source_timeframe="1 hour",
        created_at=created,
        displacement_bar_time=created,
        zone_low=D("101"),
        zone_high=D("102"),
        zone_mid=D("101.5"),
        zone_width=D("1"),
        zone_width_atr=D("0.4"),
        displacement_atr_multiple=D("1.7"),
        body_ratio=D("0.75"),
        close_location=D("0.85"),
        relative_volume=D("1.6"),
        source_bar_ids=("a", "b"),
        structure_reference_id="balance_high:100",
        expires_at=created + timedelta(hours=7),
    )


class RobustATRTests(unittest.TestCase):
    def test_robust_atr_filters_large_and_tiny_outliers(self) -> None:
        bars = [
            make_bar(
                index,
                high=str(100.5 + (0.02 if index % 2 else 0)),
                low="99.5",
                close="100",
            )
            for index in range(19)
        ]
        bars.append(make_bar(19, high="120", low="80", close="100"))
        config = IRSConfig(robust_atr_min_values=12)

        atr = calculate_robust_atr(bars, config)

        self.assertIsNotNone(atr)
        self.assertLess(atr, D("1.2"))
        self.assertGreater(atr, D("0.9"))

    def test_robust_atr_mad_zero_uses_winsorized_fallback(self) -> None:
        bars = [make_bar(index, high="100.5", low="99.5", close="100") for index in range(20)]
        self.assertEqual(calculate_robust_atr(bars), D("1.0"))

    def test_robust_atr_rejects_insufficient_values(self) -> None:
        self.assertIsNone(calculate_robust_atr([make_bar(index) for index in range(8)]))


class DisplacementAndZoneTests(unittest.TestCase):
    def _displacement_bars(self, bearish: bool = False) -> list[Bar]:
        bars = [make_bar(index) for index in range(20)]
        if bearish:
            bars.append(
                make_bar(20, open_="100.3", high="100.4", low="97.0", close="97.2", volume=1_800_000)
            )
        else:
            bars.append(
                make_bar(20, open_="100.0", high="103.4", low="99.9", close="103.2", volume=1_800_000)
            )
        return bars

    def test_bullish_and_bearish_displacement_are_mirrors(self) -> None:
        bullish = detect_displacement(self._displacement_bars(), -1, D("1"))
        bearish = detect_displacement(self._displacement_bars(True), -1, D("1"))

        self.assertTrue(bullish.passed, bullish.failed_conditions)
        self.assertTrue(bearish.passed, bearish.failed_conditions)
        self.assertEqual(bullish.direction, Direction.LONG)
        self.assertEqual(bearish.direction, Direction.SHORT)
        self.assertEqual(
            bullish.metrics["relative_volume"].quantize(D("0.01")),
            bearish.metrics["relative_volume"].quantize(D("0.01")),
        )

    def test_relative_volume_excludes_current_bar(self) -> None:
        bars = [make_bar(index, volume=100) for index in range(20)]
        bars.append(make_bar(20, volume=1000))
        self.assertEqual(calculate_relative_volume(bars, 20), D("10"))

    def test_overlap_uses_current_range_as_denominator(self) -> None:
        previous = make_bar(0, high="101", low="99")
        current = make_bar(1, high="102", low="100")
        self.assertEqual(calculate_overlap_ratio(current, previous), D("0.5"))

    def test_strict_gap_zone_uses_t_minus_two_and_t_boundaries(self) -> None:
        bars = [make_bar(index) for index in range(20)]
        bars.extend(
            [
                make_bar(20, high="100", low="99", close="99.8"),
                make_bar(21, open_="100.2", high="101", low="100", close="100.8"),
                make_bar(22, open_="101.3", high="104.5", low="101.2", close="104.3", volume=1_800_000),
            ]
        )
        displacement = detect_displacement(bars, -1, D("1"))

        zone, reasons = build_strict_gap_zone(bars, -1, displacement, D("2"))

        self.assertEqual(reasons, ())
        self.assertIsNotNone(zone)
        self.assertEqual(zone.zone_low, D("100"))
        self.assertEqual(zone.zone_high, D("101.2"))
        self.assertEqual(zone.zone_type, ZoneType.STRICT_THREE_BAR_GAP)

    def test_zone_rejects_width_outside_atr_limits(self) -> None:
        bars = self._displacement_bars()
        displacement = detect_displacement(bars, -1, D("1"))
        zone, reasons = build_low_overlap_zone(bars, -1, displacement, D("1"))
        self.assertIsNone(zone)
        self.assertIn("ZONE_TOO_WIDE", reasons)

    def test_incomplete_displacement_bar_is_ignored(self) -> None:
        bars = self._displacement_bars()
        bars[-1] = make_bar(
            20,
            open_="100",
            high="103.4",
            low="99.9",
            close="103.2",
            volume=1_800_000,
            complete=False,
        )
        result = detect_displacement(bars, -1, D("1"))
        self.assertFalse(result.passed)


class RetraceAndConfirmationTests(unittest.TestCase):
    def _bar_15m(
        self,
        offset: int,
        open_: str,
        high: str,
        low: str,
        close: str,
        volume: int = 200_000,
    ) -> Bar:
        zone = make_zone()
        return Bar(
            symbol="TEST",
            timeframe="15 mins",
            timestamp=zone.created_at + timedelta(minutes=15 * offset),
            open=D(open_),
            high=D(high),
            low=D(low),
            close=D(close),
            volume=volume,
        )

    def test_retrace_depth_and_declining_volume(self) -> None:
        result = evaluate_retracement(
            make_zone(),
            [
                self._bar_15m(1, "102.5", "102.6", "101.5", "101.9", 300_000),
                self._bar_15m(2, "101.9", "102.0", "101.45", "101.8", 250_000),
            ],
            displacement_volume=1_000_000,
            atr_15m=D("1"),
            config=IRSConfig(min_retrace_wait_1h_bars=0),
        )
        self.assertTrue(result.detected)
        self.assertTrue(result.preferred)
        self.assertEqual(result.depth, D("0.55"))
        self.assertLess(result.pullback_volume_ratio, D("0.75"))

    def test_two_adverse_closes_invalidate(self) -> None:
        result = evaluate_retracement(
            make_zone(),
            [
                self._bar_15m(1, "101", "101.1", "100.7", "100.8"),
                self._bar_15m(2, "100.8", "100.9", "100.5", "100.6"),
            ],
            displacement_volume=1_000_000,
            atr_15m=D("1"),
            config=IRSConfig(min_retrace_wait_1h_bars=0),
        )
        self.assertTrue(result.invalidated)
        self.assertIn("ZONE_ACCEPTED_AGAINST_TRADE", result.reasons)

    def test_retrace_inside_first_hour_is_not_eligible(self) -> None:
        result = evaluate_retracement(
            make_zone(),
            [
                self._bar_15m(1, "102.5", "102.6", "101.5", "101.9"),
                self._bar_15m(2, "101.9", "102.0", "101.4", "101.8"),
                self._bar_15m(3, "101.8", "102.0", "101.4", "101.9"),
                self._bar_15m(4, "101.9", "102.1", "101.5", "102.0"),
            ],
            displacement_volume=1_000_000,
            atr_15m=D("1"),
        )
        self.assertFalse(result.detected)
        self.assertTrue(result.metrics["waiting_for_minimum_retrace_delay"])

    def test_sweep_and_reclaim_has_first_priority(self) -> None:
        bars = [self._bar_15m(1, "101.7", "102.0", "101.4", "101.9")]
        event = choose_confirmation(make_zone(), bars, D("1"))
        self.assertIsNotNone(event)
        self.assertEqual(event.confirmation_type, ConfirmationType.SWEEP_AND_RECLAIM)

    def test_two_bar_reclaim(self) -> None:
        bars = [
            self._bar_15m(1, "102.1", "102.2", "101.3", "101.4", 200_000),
            self._bar_15m(2, "101.4", "102.4", "101.3", "102.3", 200_000),
        ]
        event = detect_two_bar_reclaim(make_zone(), bars, D("2"))
        self.assertIsNotNone(event)
        self.assertEqual(event.confirmation_type, ConfirmationType.TWO_BAR_RECLAIM)

    def test_micro_break_uses_only_confirmed_pivot(self) -> None:
        bars = [
            self._bar_15m(1, "101.4", "101.8", "101.2", "101.6"),
            self._bar_15m(2, "101.6", "102.1", "101.4", "101.9"),
            self._bar_15m(3, "101.9", "102.0", "101.5", "101.7"),
            self._bar_15m(4, "101.7", "102.5", "101.6", "102.4"),
        ]
        event = detect_micro_break_of_structure(make_zone(), bars, D("1"))
        self.assertIsNotNone(event)
        self.assertEqual(event.metrics["pivot_available_at"], bars[2].timestamp)

    def test_future_bar_does_not_change_existing_confirmation(self) -> None:
        bars = [self._bar_15m(1, "101.7", "102.0", "101.4", "101.9")]
        before = detect_sweep_and_reclaim(make_zone(), bars, D("1"))
        after = detect_sweep_and_reclaim(
            make_zone(),
            [*bars, self._bar_15m(2, "102", "103", "101.9", "102.8")],
            D("1"),
        )
        self.assertEqual(before.bar_id, after.bar_id)


class PlanningAndGateTests(unittest.TestCase):
    def test_entry_rounding_and_symmetry(self) -> None:
        event = detect_sweep_and_reclaim(
            make_zone(),
            [
                Bar(
                    symbol="TEST",
                    timeframe="15 mins",
                    timestamp=make_zone().created_at + timedelta(minutes=15),
                    open=D("101.60"),
                    high=D("102.003"),
                    low=D("101.40"),
                    close=D("101.90"),
                    volume=200_000,
                )
            ],
            D("1"),
        )
        long_stop, long_limit = calculate_entry_trigger(Direction.LONG, event, D("1"))
        short_event = event.__class__(
            event.confirmation_type,
            event.timestamp,
            event.bar_id,
            D("102.60"),
            D("101.997"),
            event.boundary,
            event.score,
            event.metrics,
        )
        short_stop, short_limit = calculate_entry_trigger(Direction.SHORT, short_event, D("1"))
        self.assertEqual(long_stop, D("102.03"))
        self.assertEqual(long_limit, D("102.05"))
        self.assertEqual(short_stop, D("101.97"))
        self.assertEqual(short_limit, D("101.95"))

    def test_structural_r_includes_costs(self) -> None:
        no_cost = calculate_structural_r(Direction.LONG, D("100"), D("99"), D("102"))
        with_cost = calculate_structural_r(Direction.LONG, D("100"), D("99"), D("102"), D("0.10"))
        self.assertEqual(no_cost, D("2"))
        self.assertLess(with_cost, no_cost)

    def test_position_sizing_respects_risk_notional_and_buying_power(self) -> None:
        account = AccountState(equity=D("100000"), buying_power=D("5000"))
        quantity, risk_cash, buying_power = size_position(account, D("100"), D("99"))
        self.assertEqual(risk_cash, D("250"))
        self.assertEqual(quantity, 50)
        self.assertEqual(buying_power, D("5000"))

    def test_news_unknown_fails_closed(self) -> None:
        now = datetime(2026, 7, 1, 14, tzinfo=ET)
        daily = [
            Bar(
                symbol="TEST",
                timeframe="1 day",
                timestamp=now - timedelta(days=30 - index),
                open=D("100"),
                high=D("101"),
                low=D("99"),
                close=D("100"),
                volume=1_000_000,
            )
            for index in range(30)
        ]
        context = StrategyContext(
            symbol="TEST",
            as_of=now,
            daily_bars=daily,
            hourly_bars=(),
            bars_15m=(),
            quote=Quote(D("100"), D("100.05"), D("100.02"), now),
            market_regime=MarketRegime.TREND_LOW_VOL,
            news_status=RiskStatus.UNKNOWN,
            earnings_status=RiskStatus.CLEAR,
            corporate_action_status=RiskStatus.CLEAR,
            halt_status=RiskStatus.CLEAR,
            account_state=AccountState(D("100000"), D("100000")),
        )
        reasons = evaluate_hard_gates(context, Direction.LONG, D("90"), D("1.2"))
        self.assertIn("NEWS_STATUS_UNAVAILABLE", reasons)

    def test_stale_quote_and_existing_position_are_hard_rejects(self) -> None:
        now = datetime(2026, 7, 1, 14, tzinfo=ET)
        daily = [
            Bar(
                symbol="TEST",
                timeframe="1 day",
                timestamp=now - timedelta(days=30 - index),
                open=D("100"),
                high=D("101"),
                low=D("99"),
                close=D("100"),
                volume=1_000_000,
            )
            for index in range(30)
        ]
        context = StrategyContext(
            symbol="TEST",
            as_of=now,
            daily_bars=daily,
            hourly_bars=(),
            bars_15m=(),
            quote=Quote(D("100"), D("100.05"), D("100.02"), now - timedelta(seconds=10)),
            levels=(StructuralLevel(D("105"), "resistance"),),
            market_regime=MarketRegime.TREND_LOW_VOL,
            news_status=RiskStatus.CLEAR,
            earnings_status=RiskStatus.CLEAR,
            corporate_action_status=RiskStatus.CLEAR,
            halt_status=RiskStatus.CLEAR,
            account_state=AccountState(
                D("100000"),
                D("100000"),
                existing_symbols=("TEST",),
            ),
        )
        reasons = evaluate_hard_gates(context, Direction.LONG, D("90"), D("1.2"))
        self.assertIn("STALE_QUOTE", reasons)
        self.assertIn("POSITION_EXISTS", reasons)


if __name__ == "__main__":
    unittest.main()
