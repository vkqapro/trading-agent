"""Deterministic, direction-aware trade and portfolio metric calculations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from statistics import mean, median
from typing import Any

from .candle_reader import average_true_range
from .models import Candle, ResearchTrade, json_value


def _is_short(side: str) -> bool:
    return side.upper() in {"SELL", "SHORT"}


def _number(value: Any) -> Decimal | None:
    return value if isinstance(value, Decimal) else None


def _minutes_between(start: datetime | None, end: datetime | None) -> Decimal | None:
    if start is None or end is None:
        return None
    return Decimal(str((end - start).total_seconds() / 60))


def _first_touch(
    candles: list[Candle],
    *,
    side: str,
    stop: Decimal | None,
    target: Decimal | None,
) -> dict[str, Any]:
    short = _is_short(side)
    for candle in candles:
        stop_hit = stop is not None and ((candle.high >= stop) if short else (candle.low <= stop))
        target_hit = target is not None and ((candle.low <= target) if short else (candle.high >= target))
        if not stop_hit and not target_hit:
            continue
        if stop_hit and target_hit:
            return {"outcome": "AMBIGUOUS_SAME_BAR", "timestamp": candle.timestamp}
        return {"outcome": "STOP" if stop_hit else "TARGET", "timestamp": candle.timestamp}
    return {"outcome": None, "timestamp": None}


def derive_trade_metrics(trade: ResearchTrade, context: Mapping[str, Any]) -> dict[str, Any]:
    """Calculate only metrics supported by observed fills and saved candles."""
    entry = trade.entry_price
    exit_price = trade.exit_price
    quantity = trade.quantity
    stop = trade.planned_stop
    target = trade.planned_target
    risk_per_share = abs(entry - stop) if entry is not None and stop is not None and entry != stop else None
    planned_reward = abs(target - entry) if entry is not None and target is not None and target != entry else None
    planned_rr = planned_reward / risk_per_share if planned_reward is not None and risk_per_share else None
    calculated_pnl: Decimal | None = None
    if entry is not None and exit_price is not None and quantity is not None:
        calculated_pnl = ((entry - exit_price) if _is_short(trade.side) else (exit_price - entry)) * quantity
    pnl = trade.realized_pnl if trade.realized_pnl is not None else calculated_pnl
    if pnl is not None and trade.fees is not None and trade.realized_pnl is None:
        pnl -= trade.fees
    realized_r = pnl / (risk_per_share * quantity) if pnl is not None and risk_per_share and quantity else None
    signed_entry_deviation = entry - trade.planned_entry if entry is not None and trade.planned_entry is not None else None
    adverse_entry_deviation = (
        -signed_entry_deviation if _is_short(trade.side) else signed_entry_deviation
    ) if signed_entry_deviation is not None else None
    exit_deviation = exit_price - target if exit_price is not None and target is not None else None
    candles = list(context.get("candles") or [])
    market_available = bool(context.get("market_context_available"))
    mfe: Decimal | None = None
    mae: Decimal | None = None
    time_to_mfe: Decimal | None = None
    time_to_mae: Decimal | None = None
    touch = {"outcome": None, "timestamp": None}
    if market_available and entry is not None and candles:
        if _is_short(trade.side):
            favorable = min(candle.low for candle in candles)
            adverse = max(candle.high for candle in candles)
            mfe, mae = max(Decimal("0"), entry - favorable), max(Decimal("0"), adverse - entry)
            mfe_time = next(candle.timestamp for candle in candles if candle.low == favorable)
            mae_time = next(candle.timestamp for candle in candles if candle.high == adverse)
        else:
            favorable = max(candle.high for candle in candles)
            adverse = min(candle.low for candle in candles)
            mfe, mae = max(Decimal("0"), favorable - entry), max(Decimal("0"), entry - adverse)
            mfe_time = next(candle.timestamp for candle in candles if candle.high == favorable)
            mae_time = next(candle.timestamp for candle in candles if candle.low == adverse)
        time_to_mfe = _minutes_between(trade.entry_time, mfe_time)
        time_to_mae = _minutes_between(trade.entry_time, mae_time)
        touch = _first_touch(candles, side=trade.side, stop=stop, target=target)
    mfe_r = mfe / risk_per_share if mfe is not None and risk_per_share else None
    mae_r = mae / risk_per_share if mae is not None and risk_per_share else None
    atr = average_true_range(context.get("pre_entry_bars") or []) if market_available else None
    atr_pct = (atr / entry * Decimal("100")) if atr is not None and entry else None
    entry_minutes_after_open = None
    if trade.entry_time is not None:
        entry_minutes_after_open = Decimal(trade.entry_time.hour * 60 + trade.entry_time.minute - (9 * 60 + 30))
    performance_eligible = (
        trade.status_group == "completed"
        and pnl is not None
        and (trade.realized_pnl is not None or (entry is not None and exit_price is not None and quantity is not None))
    )
    candidates: list[str] = []
    if pnl is not None and pnl < 0:
        if adverse_entry_deviation is not None and adverse_entry_deviation > 0:
            candidates.append("ENTRY_QUALITY")
        if touch["outcome"] == "STOP":
            candidates.append("STOP_PLACEMENT")
        if mfe_r is not None and mfe_r >= Decimal("1"):
            candidates.append("EXIT_MANAGEMENT")
        if not candidates:
            candidates.append("NO_CLEAR_CAUSE")
    return json_value(
        {
            "performance_eligible": performance_eligible,
            "pnl": pnl,
            "pnl_source": "journal" if trade.realized_pnl is not None else "calculated_from_fills" if calculated_pnl is not None else None,
            "initial_risk_per_share": risk_per_share,
            "planned_reward_per_share": planned_reward,
            "planned_reward_risk": planned_rr,
            "realized_r": realized_r,
            "mfe": mfe,
            "mfe_r": mfe_r,
            "mae": mae,
            "mae_r": mae_r,
            "time_to_mfe_minutes": time_to_mfe,
            "time_to_mae_minutes": time_to_mae,
            "time_in_trade_minutes": _minutes_between(trade.entry_time, trade.exit_time),
            "first_stop_or_target_touch": json_value(touch),
            "entry_deviation": signed_entry_deviation,
            "adverse_entry_deviation": adverse_entry_deviation,
            "exit_deviation_from_target": exit_deviation,
            "atr": atr,
            "atr_pct": atr_pct,
            "entry_minutes_after_open": entry_minutes_after_open,
            "entry_day_of_week": trade.entry_time.strftime("%A") if trade.entry_time else None,
            "entry_session": "morning" if trade.entry_time and trade.entry_time.hour < 12 else "afternoon" if trade.entry_time else None,
            "market_context_available": market_available,
            "market_context_reason": context.get("reason"),
            "loss_classification_candidates": candidates,
            "classification_status": "EVIDENCE_ONLY_NOT_AN_INTERPRETATION",
        }
    )


def _decimal_values(records: Iterable[Mapping[str, Any]], field: str) -> list[Decimal]:
    values: list[Decimal] = []
    for record in records:
        value = record.get(field)
        try:
            if value is not None:
                values.append(Decimal(str(value)))
        except Exception:
            continue
    return values


def _streaks(pnls: Iterable[Decimal]) -> tuple[int, int]:
    win_streak = loss_streak = longest_win = longest_loss = 0
    for pnl in pnls:
        if pnl > 0:
            win_streak += 1
            loss_streak = 0
        elif pnl < 0:
            loss_streak += 1
            win_streak = 0
        else:
            win_streak = loss_streak = 0
        longest_win, longest_loss = max(longest_win, win_streak), max(longest_loss, loss_streak)
    return longest_win, longest_loss


def summarize(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Return portfolio or strategy aggregates for performance-eligible records."""
    pnls = _decimal_values(records, "pnl")
    rs = _decimal_values(records, "realized_r")
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    breakeven = [value for value in pnls if value == 0]
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = sum(losses, Decimal("0"))
    equity = Decimal("0")
    high_water = Decimal("0")
    max_drawdown = Decimal("0")
    curve: list[dict[str, Any]] = []
    for index, record in enumerate(records, 1):
        value = record.get("pnl")
        if value is None:
            continue
        pnl = Decimal(str(value))
        equity += pnl
        high_water = max(high_water, equity)
        max_drawdown = max(max_drawdown, high_water - equity)
        curve.append({"sequence": index, "trade_id": record.get("trade_id"), "exit_time": record.get("exit_time"), "equity": equity})
    longest_wins, longest_losses = _streaks(pnls)
    count = len(pnls)
    return json_value(
        {
            "closed_trades": len(records),
            "pnl_coverage_trades": count,
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": len(breakeven),
            "win_rate": (Decimal(len(wins)) / Decimal(count) * Decimal("100")) if count else None,
            "loss_rate": (Decimal(len(losses)) / Decimal(count) * Decimal("100")) if count else None,
            "net_pnl": sum(pnls, Decimal("0")) if pnls else None,
            "gross_profit": gross_profit if pnls else None,
            "gross_loss": gross_loss if pnls else None,
            "profit_factor": gross_profit / abs(gross_loss) if gross_loss else None,
            "average_win": sum(wins, Decimal("0")) / Decimal(len(wins)) if wins else None,
            "average_loss": sum(losses, Decimal("0")) / Decimal(len(losses)) if losses else None,
            "avg_r": sum(rs, Decimal("0")) / Decimal(len(rs)) if rs else None,
            "median_r": Decimal(str(median(rs))) if rs else None,
            "expectancy_r": sum(rs, Decimal("0")) / Decimal(len(rs)) if rs else None,
            "largest_winner": max(wins) if wins else None,
            "largest_loser": min(losses) if losses else None,
            "max_consecutive_wins": longest_wins,
            "max_consecutive_losses": longest_losses,
            "max_drawdown": max_drawdown if pnls else None,
            "equity_curve": curve,
        }
    )


def robust_stats(values: list[Decimal]) -> dict[str, Any] | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    lower = ordered[:midpoint]
    upper = ordered[midpoint + (len(ordered) % 2) :]
    q1 = Decimal(str(median(lower))) if lower else ordered[0]
    q3 = Decimal(str(median(upper))) if upper else ordered[-1]
    return json_value({"sample_size": len(values), "median": Decimal(str(median(values))), "mean": Decimal(str(mean(values))), "iqr": q3 - q1})

