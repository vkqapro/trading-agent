"""Cost-aware, lower-timeframe IRS execution replay."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from src.strategy.inefficiency_reclaim import (
    Bar,
    Direction,
    StrategyCandidate,
    completed_bars,
)


ZERO = Decimal("0")


class TradeLabel(str, Enum):
    WIN = "WIN"
    LOSS = "LOSS"
    AMBIGUOUS = "AMBIGUOUS"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    NO_FILL = "NO_FILL"
    REJECTED = "REJECTED"
    DATA_ERROR = "DATA_ERROR"


@dataclass(frozen=True)
class BacktestCosts:
    commission_per_share: Decimal = Decimal("0.005")
    spread_per_share: Decimal = Decimal("0.01")
    slippage_per_share: Decimal = Decimal("0.01")

    @property
    def round_trip_per_share(self) -> Decimal:
        return self.commission_per_share * 2 + self.spread_per_share + self.slippage_per_share


@dataclass(frozen=True)
class BacktestTrade:
    signal_id: str
    symbol: str
    direction: Direction
    zone_type: str
    confirmation_type: str | None
    score: Decimal
    signal_time: datetime
    entry_time: datetime | None
    exit_time: datetime | None
    entry_price: Decimal | None
    exit_price: Decimal | None
    label: TradeLabel
    conservative_label: TradeLabel
    gross_r: Decimal
    net_r: Decimal
    pnl: Decimal
    quantity: int
    mae_r: Decimal
    mfe_r: Decimal
    holding_minutes: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key, value in list(payload.items()):
            if isinstance(value, Decimal):
                payload[key] = float(value)
            elif isinstance(value, datetime):
                payload[key] = value.isoformat()
            elif isinstance(value, Enum):
                payload[key] = value.value
        return payload


def _bar_contains_time(parent: Bar, child: Bar, duration: timedelta) -> bool:
    return parent.timestamp - duration < child.timestamp <= parent.timestamp


def _exit_touches(
    direction: Direction,
    bar: Bar,
    stop: Decimal,
    target: Decimal,
) -> tuple[bool, bool]:
    stop_hit = bar.low <= stop if direction is Direction.LONG else bar.high >= stop
    target_hit = bar.high >= target if direction is Direction.LONG else bar.low <= target
    return stop_hit, target_hit


def resolve_exit_sequence(
    *,
    direction: Direction,
    parent_bar: Bar,
    stop: Decimal,
    target: Decimal,
    bars_5m: Sequence[Bar] | None,
    bars_1m: Sequence[Bar] | None,
) -> TradeLabel:
    stop_hit, target_hit = _exit_touches(direction, parent_bar, stop, target)
    if stop_hit and not target_hit:
        return TradeLabel.LOSS
    if target_hit and not stop_hit:
        return TradeLabel.WIN
    if not stop_hit and not target_hit:
        return TradeLabel.CANCELLED

    five = [
        bar
        for bar in completed_bars(bars_5m or ())
        if _bar_contains_time(parent_bar, bar, timedelta(minutes=15))
    ]
    for child in five:
        child_stop, child_target = _exit_touches(direction, child, stop, target)
        if child_stop and child_target:
            one = [
                bar
                for bar in completed_bars(bars_1m or ())
                if _bar_contains_time(child, bar, timedelta(minutes=5))
            ]
            for minute in one:
                minute_stop, minute_target = _exit_touches(direction, minute, stop, target)
                if minute_stop and minute_target:
                    return TradeLabel.AMBIGUOUS
                if minute_stop:
                    return TradeLabel.LOSS
                if minute_target:
                    return TradeLabel.WIN
            return TradeLabel.AMBIGUOUS
        if child_stop:
            return TradeLabel.LOSS
        if child_target:
            return TradeLabel.WIN
    return TradeLabel.AMBIGUOUS


def _entry_fill(
    direction: Direction,
    bar: Bar,
    stop_trigger: Decimal,
    limit_price: Decimal,
    costs: BacktestCosts,
) -> Decimal | None:
    if direction is Direction.LONG:
        triggered = bar.high >= stop_trigger
        tradable_at_limit = bar.low <= limit_price
        estimated = stop_trigger + costs.spread_per_share / 2 + costs.slippage_per_share
        return min(limit_price, estimated) if triggered and tradable_at_limit and estimated <= limit_price else None
    triggered = bar.low <= stop_trigger
    tradable_at_limit = bar.high >= limit_price
    estimated = stop_trigger - costs.spread_per_share / 2 - costs.slippage_per_share
    return max(limit_price, estimated) if triggered and tradable_at_limit and estimated >= limit_price else None


def replay_candidate(
    candidate: StrategyCandidate,
    *,
    future_15m: Sequence[Bar],
    bars_5m: Sequence[Bar] | None = None,
    bars_1m: Sequence[Bar] | None = None,
    costs: BacktestCosts = BacktestCosts(),
) -> BacktestTrade:
    signal_time = (
        candidate.confirmation.timestamp
        if candidate.confirmation is not None
        else candidate.zone.created_at
    )
    if candidate.hard_rejections or candidate.order_plan is None:
        return BacktestTrade(
            candidate.signal_id,
            candidate.symbol,
            candidate.direction,
            candidate.zone.zone_type.value,
            candidate.confirmation.confirmation_type.value if candidate.confirmation else None,
            candidate.score,
            signal_time,
            None,
            None,
            None,
            None,
            TradeLabel.REJECTED,
            TradeLabel.REJECTED,
            ZERO,
            ZERO,
            ZERO,
            0,
            ZERO,
            ZERO,
            0,
            "candidate hard-rejected before replay",
        )
    plan = candidate.order_plan
    bars = [
        bar
        for bar in completed_bars(future_15m)
        if bar.timestamp > signal_time
        and (candidate.expires_at is None or bar.timestamp <= candidate.expires_at)
    ]
    if not bars:
        return BacktestTrade(
            candidate.signal_id,
            candidate.symbol,
            candidate.direction,
            candidate.zone.zone_type.value,
            candidate.confirmation.confirmation_type.value if candidate.confirmation else None,
            candidate.score,
            signal_time,
            None,
            None,
            None,
            None,
            TradeLabel.EXPIRED,
            TradeLabel.EXPIRED,
            ZERO,
            ZERO,
            ZERO,
            plan.quantity,
            ZERO,
            ZERO,
            0,
            "no completed bar before trigger expiration",
        )

    fill: Decimal | None = None
    fill_time: datetime | None = None
    fill_index = -1
    for index, bar in enumerate(bars):
        fill = _entry_fill(
            candidate.direction, bar, plan.entry_stop, plan.entry_limit, costs
        )
        if fill is not None:
            fill_time = bar.timestamp
            fill_index = index
            break
    if fill is None:
        return BacktestTrade(
            candidate.signal_id,
            candidate.symbol,
            candidate.direction,
            candidate.zone.zone_type.value,
            candidate.confirmation.confirmation_type.value if candidate.confirmation else None,
            candidate.score,
            signal_time,
            None,
            None,
            None,
            None,
            TradeLabel.NO_FILL,
            TradeLabel.NO_FILL,
            ZERO,
            ZERO,
            ZERO,
            plan.quantity,
            ZERO,
            ZERO,
            0,
            "stop-limit trigger did not produce an executable fill",
        )

    risk_per_share = abs(fill - plan.stop)
    if risk_per_share <= ZERO:
        return BacktestTrade(
            candidate.signal_id,
            candidate.symbol,
            candidate.direction,
            candidate.zone.zone_type.value,
            candidate.confirmation.confirmation_type.value if candidate.confirmation else None,
            candidate.score,
            signal_time,
            fill_time,
            None,
            fill,
            None,
            TradeLabel.DATA_ERROR,
            TradeLabel.LOSS,
            ZERO,
            ZERO,
            ZERO,
            plan.quantity,
            ZERO,
            ZERO,
            0,
            "invalid replay risk distance",
        )

    adverse = ZERO
    favorable = ZERO
    exit_label = TradeLabel.CANCELLED
    exit_price: Decimal | None = None
    exit_time: datetime | None = None
    for bar in bars[fill_index:]:
        if candidate.direction is Direction.LONG:
            adverse = max(adverse, fill - bar.low)
            favorable = max(favorable, bar.high - fill)
        else:
            adverse = max(adverse, bar.high - fill)
            favorable = max(favorable, fill - bar.low)
        stop_hit, target_hit = _exit_touches(
            candidate.direction, bar, plan.stop, plan.target
        )
        if not stop_hit and not target_hit:
            continue
        exit_label = resolve_exit_sequence(
            direction=candidate.direction,
            parent_bar=bar,
            stop=plan.stop,
            target=plan.target,
            bars_5m=bars_5m,
            bars_1m=bars_1m,
        )
        exit_time = bar.timestamp
        if exit_label is TradeLabel.WIN:
            exit_price = plan.target
        elif exit_label in {TradeLabel.LOSS, TradeLabel.AMBIGUOUS}:
            exit_price = plan.stop
        break

    if exit_price is None:
        final = bars[-1]
        exit_time = final.timestamp
        exit_price = final.close
        exit_label = TradeLabel.CANCELLED
    gross_reward = (
        exit_price - fill
        if candidate.direction is Direction.LONG
        else fill - exit_price
    )
    gross_r = gross_reward / risk_per_share
    net_reward = gross_reward - costs.round_trip_per_share
    net_r = net_reward / risk_per_share
    conservative = TradeLabel.LOSS if exit_label is TradeLabel.AMBIGUOUS else exit_label
    quantity = max(0, plan.quantity)
    holding_minutes = (
        int((exit_time - fill_time).total_seconds() // 60)
        if exit_time is not None and fill_time is not None
        else 0
    )
    return BacktestTrade(
        candidate.signal_id,
        candidate.symbol,
        candidate.direction,
        candidate.zone.zone_type.value,
        candidate.confirmation.confirmation_type.value if candidate.confirmation else None,
        candidate.score,
        signal_time,
        fill_time,
        exit_time,
        fill,
        exit_price,
        exit_label,
        conservative,
        gross_r.quantize(Decimal("0.0001")),
        net_r.quantize(Decimal("0.0001")),
        (net_reward * quantity).quantize(Decimal("0.01")),
        quantity,
        (adverse / risk_per_share).quantize(Decimal("0.0001")),
        (favorable / risk_per_share).quantize(Decimal("0.0001")),
        holding_minutes,
        "lower-timeframe sequence" if exit_label in {TradeLabel.WIN, TradeLabel.LOSS} else exit_label.value.lower(),
    )


def _maximum_drawdown(values: Sequence[Decimal]) -> Decimal:
    equity = ZERO
    peak = ZERO
    drawdown = ZERO
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _slice_stats(trades: Sequence[BacktestTrade]) -> dict[str, Any]:
    filled = [
        trade
        for trade in trades
        if trade.label not in {TradeLabel.REJECTED, TradeLabel.EXPIRED, TradeLabel.NO_FILL, TradeLabel.DATA_ERROR}
    ]
    conservative_wins = [trade for trade in filled if trade.conservative_label is TradeLabel.WIN]
    conservative_losses = [trade for trade in filled if trade.conservative_label is TradeLabel.LOSS]
    gross_profit = sum((max(ZERO, trade.pnl) for trade in filled), ZERO)
    gross_loss = abs(sum((min(ZERO, trade.pnl) for trade in filled), ZERO))
    win_values = [trade.pnl for trade in conservative_wins]
    loss_values = [trade.pnl for trade in conservative_losses]
    month_count = len({(trade.signal_time.year, trade.signal_time.month) for trade in trades})
    rejected_count = len([trade for trade in trades if trade.label is TradeLabel.REJECTED])
    return {
        "signals": len(trades),
        "approved_trades": len(filled),
        "rejection_rate": rejected_count / len(trades) if trades else 0.0,
        "fill_rate": len(filled) / len(trades) if trades else 0.0,
        "win_rate": len(conservative_wins) / len(filled) if filled else 0.0,
        "tp_before_sl": len([trade for trade in filled if trade.label is TradeLabel.WIN]) / len(filled) if filled else 0.0,
        "expectancy_r": float(statistics.fmean(float(trade.net_r) for trade in filled)) if filled else 0.0,
        "expectancy_dollars": float(statistics.fmean(float(trade.pnl) for trade in filled)) if filled else 0.0,
        "profit_factor": float(gross_profit / gross_loss) if gross_loss > ZERO else None,
        "maximum_drawdown": float(_maximum_drawdown([trade.pnl for trade in filled])),
        "average_mae_r": float(statistics.fmean(float(trade.mae_r) for trade in filled)) if filled else 0.0,
        "average_mfe_r": float(statistics.fmean(float(trade.mfe_r) for trade in filled)) if filled else 0.0,
        "average_holding_minutes": float(statistics.fmean(trade.holding_minutes for trade in filled)) if filled else 0.0,
        "average_win": float(statistics.fmean(win_values)) if win_values else 0.0,
        "median_win": float(statistics.median(win_values)) if win_values else 0.0,
        "average_loss": float(statistics.fmean(loss_values)) if loss_values else 0.0,
        "median_loss": float(statistics.median(loss_values)) if loss_values else 0.0,
        "signals_per_month": len(trades) / month_count if month_count else 0.0,
        "trades_per_month": len(filled) / month_count if month_count else 0.0,
        "ambiguous": len([trade for trade in trades if trade.label is TradeLabel.AMBIGUOUS]),
        "no_fill": len([trade for trade in trades if trade.label is TradeLabel.NO_FILL]),
        "rejected": rejected_count,
        "losses": len(conservative_losses),
    }


def build_backtest_report(trades: Sequence[BacktestTrade]) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda trade: (trade.signal_time, trade.signal_id))
    groups: dict[str, dict[str, list[BacktestTrade]]] = {
        "by_score": defaultdict(list),
        "by_direction": defaultdict(list),
        "by_year": defaultdict(list),
        "by_zone_type": defaultdict(list),
        "by_confirmation_type": defaultdict(list),
    }
    for trade in ordered:
        lower = int(math.floor(float(trade.score) / 5) * 5)
        groups["by_score"][f"{lower}-{lower + 4}"].append(trade)
        groups["by_direction"][trade.direction.value].append(trade)
        groups["by_year"][str(trade.signal_time.year)].append(trade)
        groups["by_zone_type"][trade.zone_type].append(trade)
        groups["by_confirmation_type"][trade.confirmation_type or "NONE"].append(trade)
    report = _slice_stats(ordered)
    for group_name, values in groups.items():
        report[group_name] = {
            key: _slice_stats(items) for key, items in sorted(values.items())
        }
    report["trades"] = [trade.to_dict() for trade in ordered]
    return report


def walk_forward_report(trades: Sequence[BacktestTrade]) -> dict[str, Any]:
    """Chronological thirds with unchanged parameters; no ticker fitting."""
    ordered = sorted(trades, key=lambda trade: (trade.signal_time, trade.signal_id))
    if not ordered:
        return {"development": _slice_stats([]), "validation": _slice_stats([]), "oos": _slice_stats([])}
    first = max(1, len(ordered) // 3)
    second = max(first + 1, 2 * len(ordered) // 3)
    return {
        "development": _slice_stats(ordered[:first]),
        "validation": _slice_stats(ordered[first:second]),
        "oos": _slice_stats(ordered[second:]),
        "parameters_fitted_per_ticker": False,
    }


def run_backtest(
    candidates: Sequence[StrategyCandidate],
    *,
    bars_loader: Callable[[StrategyCandidate, str], Sequence[Bar]],
    costs: BacktestCosts = BacktestCosts(),
    config_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    trades = [
        replay_candidate(
            candidate,
            future_15m=bars_loader(candidate, "15m"),
            bars_5m=bars_loader(candidate, "5m"),
            bars_1m=bars_loader(candidate, "1m"),
            costs=costs,
        )
        for candidate in sorted(
            candidates,
            key=lambda item: (
                item.confirmation.timestamp if item.confirmation else item.zone.created_at,
                item.signal_id,
            ),
        )
    ]
    report = build_backtest_report(trades)
    report["walk_forward"] = walk_forward_report(trades)
    report["costs"] = {
        "commission_per_share": float(costs.commission_per_share),
        "spread_per_share": float(costs.spread_per_share),
        "slippage_per_share": float(costs.slippage_per_share),
    }
    report["config_snapshot"] = dict(config_snapshot or {})
    report["strategy_versions"] = sorted(
        {candidate.strategy_version for candidate in candidates}
    )
    report["ambiguous_headline_policy"] = "LOSS"
    return report
