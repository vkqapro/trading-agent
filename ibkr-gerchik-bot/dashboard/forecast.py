"""Read-only risk scenario forecasting for the dashboard."""

from __future__ import annotations

import math
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, Iterable

import pandas as pd

from src.strategy.levels import Level
from src.strategy.strategy_router import route_strategies
from src.config import LOGGER

FORECAST_ENGINE_VERSION = 5
MAX_PROJECTED_ENTRY_DISTANCE_PCT = 0.20
# When ATR is unavailable we cannot use the ATR distance window, so fall back to
# a percentage gate on both ends.
MIN_PROJECTED_ENTRY_DISTANCE_PCT = 0.001
# Replayed intraday bars older than this are treated as stale (covers an
# overnight gap; a Monday replay of Friday's bars is conservatively flagged).
STALE_BARS_AFTER_HOURS = 24.0


@dataclass(frozen=True)
class ForecastScenario:
    account_equity: float
    cash_available: float
    daily_realized_pnl: float
    assumed_spread_pct: float
    risk_per_trade: float
    max_daily_loss_pct: float
    min_reward_risk_ratio: float
    max_positions: int
    max_spread_pct: float
    max_open_risk_pct: float
    max_position_value: float
    min_projected_entry_distance_atr: float = 1.0
    max_projected_entry_distance_atr: float = 2.0


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def calculate_open_risk(positions: Iterable[dict[str, Any]]) -> float:
    total = 0.0
    for position in positions:
        quantity = _number(position.get("quantity"))
        entry = _number(position.get("entry") or position.get("avg_cost"))
        stop = _number(position.get("stop_loss") or position.get("stop"))
        if quantity > 0 and entry > 0 and stop > 0:
            total += abs(entry - stop) * quantity
    return total


def _candidate(
    *,
    symbol: str,
    strategy: str,
    source: str,
    signal: str,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    level: float,
    level_type: str,
    confidence: float = 0.0,
    current_price: float = 0.0,
    daily_atr: float = 0.0,
) -> dict[str, Any] | None:
    risk_per_share = abs(entry - stop)
    reward_per_share = abs(target - entry)
    if entry <= 0 or stop <= 0 or target <= 0 or risk_per_share <= 0:
        return None
    if direction == "long" and not (stop < entry < target):
        return None
    if direction == "short" and not (target < entry < stop):
        return None
    entry_distance = abs(entry - current_price) if current_price > 0 else 0.0
    entry_distance_pct = entry_distance / current_price if current_price > 0 else 0.0
    entry_distance_atr = entry_distance / daily_atr if daily_atr > 0 else None
    return {
        "symbol": symbol,
        "strategy": strategy,
        "source": source,
        "signal": signal,
        "direction": direction,
        "entry": round(entry, 4),
        "stop": round(stop, 4),
        "target": round(target, 4),
        "level": round(level, 4),
        "level_type": level_type,
        "reward_risk": round(reward_per_share / risk_per_share, 2),
        "risk_per_share": round(risk_per_share, 4),
        "confidence": round(confidence, 2),
        "current_price": round(current_price, 4) if current_price > 0 else None,
        "entry_distance": round(entry_distance, 4),
        "entry_distance_pct": round(entry_distance_pct, 4),
        "entry_distance_atr": (
            round(entry_distance_atr, 2) if entry_distance_atr is not None else None
        ),
    }


def projected_level_candidates(watchlist: dict[str, Any]) -> list[dict[str, Any]]:
    """Build conditional level-trigger setups from the saved premarket plan."""
    candidates: list[dict[str, Any]] = []
    for symbol, raw_plan in sorted(watchlist.items()):
        if not isinstance(raw_plan, dict) or raw_plan.get("news_blocked"):
            continue
        spacing = raw_plan.get("level_spacing")
        spacing = spacing if isinstance(spacing, dict) else {}
        current_price = _number(spacing.get("current_price"))
        daily_atr = _number(raw_plan.get("daily_atr"))
        if current_price <= 0:
            continue
        levels = raw_plan.get("levels")
        if not isinstance(levels, list):
            continue
        for raw_level in levels:
            if not isinstance(raw_level, dict):
                continue
            price = _number(raw_level.get("price"))
            zone_low = _number(raw_level.get("zone_low"), price)
            zone_high = _number(raw_level.get("zone_high"), price)
            if price <= 0 or zone_low <= 0 or zone_high <= 0:
                continue
            buffer = max(price * 0.0002, daily_atr * 0.01, 0.01)
            strength = _number(raw_level.get("strength_score") or raw_level.get("strength"))
            level_type = str(raw_level.get("type") or "level")
            upper = _number(raw_level.get("nearest_upper_level"))
            lower = _number(raw_level.get("nearest_lower_level"))

            if current_price >= zone_high and upper > zone_high:
                item = _candidate(
                    symbol=symbol,
                    strategy="level_retest_forecast",
                    source="PROJECTED",
                    signal="BUY",
                    direction="long",
                    entry=zone_high + buffer,
                    stop=zone_low - buffer,
                    target=upper,
                    level=price,
                    level_type=level_type,
                    confidence=strength,
                    current_price=current_price,
                    daily_atr=daily_atr,
                )
                if item:
                    candidates.append(item)
            elif current_price <= zone_low and 0 < lower < zone_low:
                item = _candidate(
                    symbol=symbol,
                    strategy="level_retest_forecast",
                    source="PROJECTED",
                    signal="SELL",
                    direction="short",
                    entry=zone_low - buffer,
                    stop=zone_high + buffer,
                    target=lower,
                    level=price,
                    level_type=level_type,
                    confidence=strength,
                    current_price=current_price,
                    daily_atr=daily_atr,
                )
                if item:
                    candidates.append(item)
            else:
                if upper > zone_high:
                    item = _candidate(
                        symbol=symbol,
                        strategy="level_breakout_forecast",
                        source="PROJECTED",
                        signal="BUY",
                        direction="long",
                        entry=zone_high + buffer,
                        stop=zone_low - buffer,
                        target=upper,
                        level=price,
                        level_type=level_type,
                        confidence=strength,
                        current_price=current_price,
                        daily_atr=daily_atr,
                    )
                    if item:
                        candidates.append(item)
                if 0 < lower < zone_low:
                    item = _candidate(
                        symbol=symbol,
                        strategy="level_breakout_forecast",
                        source="PROJECTED",
                        signal="SELL",
                        direction="short",
                        entry=zone_low - buffer,
                        stop=zone_high + buffer,
                        target=lower,
                        level=price,
                        level_type=level_type,
                        confidence=strength,
                        current_price=current_price,
                        daily_atr=daily_atr,
                    )
                    if item:
                        candidates.append(item)
    return candidates


def replay_active_candidates(
    watchlist: dict[str, Any],
    bars_loader: Callable[[str, str], pd.DataFrame],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Replay saved bars through the strategy router without persisting decisions.

    Bars are flagged stale when their most recent timestamp is older than
    ``STALE_BARS_AFTER_HOURS``, so replayed setups computed on a prior session's
    data are surfaced rather than silently trusted.
    """
    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
    now = datetime.now()
    # Quiet the bot logger once for the whole replay rather than per symbol.
    previous_level = LOGGER.level
    LOGGER.setLevel(logging.WARNING)
    try:
        for symbol, raw_plan in sorted(watchlist.items()):
            if not isinstance(raw_plan, dict) or raw_plan.get("news_blocked"):
                continue
            raw_levels = raw_plan.get("levels")
            if not isinstance(raw_levels, list) or not raw_levels:
                continue
            bars = bars_loader(symbol, "intraday_5m")
            if bars.empty:
                warnings.append(f"{symbol}: saved intraday bars unavailable")
                continue
            bars_last, bars_age_hours, stale = _bars_freshness(bars, now)
            if stale:
                warnings.append(
                    f"{symbol}: replaying stale bars "
                    f"(last {bars_last or 'unknown'}, {bars_age_hours:.1f}h old)"
                )
            try:
                levels = [Level(**item) for item in raw_levels if isinstance(item, dict)]
                signals = route_strategies(
                    symbol, bars, levels, news_context={"risk_level": "LOW"}, persist=False,
                )
            except (TypeError, ValueError, KeyError) as exc:
                warnings.append(f"{symbol}: replay unavailable ({exc})")
                continue
            for signal in signals:
                item = _candidate(
                    symbol=symbol,
                    strategy=signal.strategy,
                    source="ACTIVE REPLAY",
                    signal=signal.signal,
                    direction=signal.direction,
                    entry=_number(signal.entry),
                    stop=_number(signal.stop),
                    target=_number(signal.target),
                    level=_number(signal.level_price),
                    level_type=str(signal.level_type),
                    confidence=_number(signal.confidence),
                )
                if item:
                    item["bars_last"] = bars_last
                    item["bars_age_hours"] = round(bars_age_hours, 1)
                    item["stale"] = stale
                    candidates.append(item)
    finally:
        LOGGER.setLevel(previous_level)
    return candidates, warnings


def _bars_freshness(bars: pd.DataFrame, now: datetime) -> tuple[str | None, float, bool]:
    """Return (last_bar_iso, age_hours, is_stale) for a bars frame."""
    if bars is None or bars.empty or "date" not in bars.columns:
        return None, 0.0, False
    try:
        last = pd.to_datetime(bars["date"]).max()
        if pd.isna(last):
            return None, 0.0, False
        last_dt = last.to_pydatetime()
        age_hours = max(0.0, (now - last_dt).total_seconds() / 3600.0)
        return last_dt.isoformat(timespec="minutes"), age_hours, age_hours > STALE_BARS_AFTER_HOURS
    except (ValueError, TypeError, AttributeError):
        return None, 0.0, False


def _reason_label(reasons: list[str]) -> str:
    labels = {
        "daily_loss_limit_reached": "Daily loss limit reached",
        "duplicate_position": "Position already open",
        "max_positions_reached": "Maximum positions reached",
        "open_risk_limit_exceeded": "Maximum open risk exceeded",
        "position_value_invalid": "Position value or cash limit exceeded",
        "reward_risk_too_low": "Reward:risk below minimum",
        "spread_too_wide": "Assumed spread exceeds limit",
        "position_size_zero": "Position size is zero",
        "entry_too_far_from_market": "Entry is too far from the current market",
        "entry_too_close_to_market": "Entry is closer than the minimum ATR distance",
    }
    return "; ".join(labels.get(reason, reason.replace("_", " ").title()) for reason in reasons)


def run_forecast(
    candidates: list[dict[str, Any]],
    scenario: ForecastScenario,
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply scenario risk limits and allocate the best candidates sequentially."""
    current_symbols = {
        str(position.get("symbol", "")).upper()
        for position in positions
        if str(position.get("symbol", "")).strip()
    }
    current_open_risk = calculate_open_risk(positions)
    max_daily_loss = max(0.0, scenario.account_equity * scenario.max_daily_loss_pct)
    daily_loss_used = max(0.0, -scenario.daily_realized_pnl)
    remaining_daily_loss = max(0.0, max_daily_loss - daily_loss_used)
    # A zero/disabled limit (max_daily_loss == 0) is not a triggered kill switch.
    daily_loss_tripped = max_daily_loss > 0 and remaining_daily_loss <= 0
    max_open_risk = max(0.0, scenario.account_equity * scenario.max_open_risk_pct)
    accepted_risk = 0.0
    accepted_notional = 0.0
    accepted_reward = 0.0
    cash_remaining = max(0.0, scenario.cash_available)
    accepted_symbols: set[str] = set()
    rows: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {}

    ordered = sorted(
        candidates,
        key=lambda item: (
            0 if item.get("source") == "ACTIVE REPLAY" else 1,
            _number(item.get("entry_distance_atr"), 999.0),
            _number(item.get("entry_distance_pct"), 999.0),
            -_number(item.get("reward_risk")),
            -_number(item.get("confidence")),
            str(item.get("symbol", "")),
        ),
    )
    for item in ordered:
        symbol = str(item.get("symbol", "")).upper()
        entry = _number(item.get("entry"))
        risk_per_share = _number(item.get("risk_per_share"))
        reward_per_share = abs(_number(item.get("target")) - entry)
        reward_risk = _number(item.get("reward_risk"))
        entry_distance_pct = _number(item.get("entry_distance_pct"))
        entry_distance_atr = item.get("entry_distance_atr")
        reasons: list[str] = []
        quantity = (
            math.floor((scenario.account_equity * scenario.risk_per_trade) / risk_per_share)
            if scenario.account_equity > 0 and risk_per_share > 0
            else 0
        )
        risk_amount = quantity * risk_per_share
        notional = quantity * entry
        potential_reward = quantity * reward_per_share

        if daily_loss_tripped:
            reasons.append("daily_loss_limit_reached")
        if item.get("source") == "PROJECTED":
            has_atr = entry_distance_atr is not None
            too_far_pct = not has_atr and entry_distance_pct > MAX_PROJECTED_ENTRY_DISTANCE_PCT
            # When ATR is unavailable, guard "too close" with a percentage floor.
            too_close_pct = not has_atr and 0 < entry_distance_pct < MIN_PROJECTED_ENTRY_DISTANCE_PCT
            too_close_atr = has_atr and _number(entry_distance_atr) < scenario.min_projected_entry_distance_atr
            too_far_atr = has_atr and _number(entry_distance_atr) > scenario.max_projected_entry_distance_atr
            if too_close_atr or too_close_pct:
                reasons.append("entry_too_close_to_market")
            if too_far_pct or too_far_atr:
                reasons.append("entry_too_far_from_market")
        if symbol in current_symbols or symbol in accepted_symbols:
            reasons.append("duplicate_position")
        if len(positions) + len(accepted_symbols) >= scenario.max_positions:
            reasons.append("max_positions_reached")
        if reward_risk < scenario.min_reward_risk_ratio:
            reasons.append("reward_risk_too_low")
        if scenario.assumed_spread_pct > scenario.max_spread_pct:
            reasons.append("spread_too_wide")
        if quantity <= 0:
            reasons.append("position_size_zero")
        if quantity > 0 and (
            notional > scenario.max_position_value or notional > cash_remaining
        ):
            reasons.append("position_value_invalid")
        if current_open_risk + accepted_risk + risk_amount > max_open_risk:
            reasons.append("open_risk_limit_exceeded")

        setup_reason_codes = {
            "entry_too_close_to_market",
            "entry_too_far_from_market",
            "reward_risk_too_low",
            "spread_too_wide",
            "position_size_zero",
        }
        setup_qualified = not any(reason in setup_reason_codes for reason in reasons)
        accepted = not reasons
        if accepted:
            accepted_symbols.add(symbol)
            accepted_risk += risk_amount
            accepted_notional += notional
            accepted_reward += potential_reward
            cash_remaining -= notional

        rows.append(
            {
                **item,
                "quantity": quantity,
                "position_value": round(notional, 2),
                "risk_amount": round(risk_amount, 2),
                "potential_reward": round(potential_reward, 2),
                "setup_qualified": setup_qualified,
                "reason_codes": list(reasons),
                "status": "FORECAST TRADE" if accepted else "FILTERED",
                "reason": "Eligible under scenario" if accepted else _reason_label(reasons),
            }
        )
        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    accepted_rows = [row for row in rows if row["status"] == "FORECAST TRADE"]
    setup_qualified_count = sum(bool(row.get("setup_qualified")) for row in rows)
    risk_budget_per_trade = max(0.0, scenario.account_equity * scenario.risk_per_trade)
    remaining_open_risk = max(0.0, max_open_risk - current_open_risk)
    # Exact open-risk capacity: greedily fit setup-qualified trades' *actual*
    # sized risk under the remaining budget (isolates the open-risk lever).
    open_risk_capacity = 0
    running_capacity_risk = 0.0
    for row in rows:
        if not row.get("setup_qualified"):
            continue
        actual_risk = _number(row.get("risk_amount"))
        if actual_risk <= 0:
            continue
        if running_capacity_risk + actual_risk <= remaining_open_risk + 1e-9:
            running_capacity_risk += actual_risk
            open_risk_capacity += 1
        else:
            break
    position_capacity = max(0, scenario.max_positions - len(positions))
    binding_limit = ""
    if daily_loss_tripped:
        binding_limit = "daily loss kill switch"
    elif len(accepted_rows) >= position_capacity and position_capacity <= open_risk_capacity:
        binding_limit = "maximum positions"
    elif reason_counts.get("open_risk_limit_exceeded", 0):
        binding_limit = "maximum open risk"
    elif reason_counts.get("position_value_invalid", 0):
        binding_limit = "cash / position value"
    elif setup_qualified_count == 0:
        binding_limit = "setup filters"

    return {
        "scenario": asdict(scenario),
        "rows": rows,
        "accepted": accepted_rows,
        "summary": {
            "possible_signals": len(candidates),
            "active_signals": sum(item.get("source") == "ACTIVE REPLAY" for item in candidates),
            "projected_setups": sum(item.get("source") == "PROJECTED" for item in candidates),
            "stale_signals": sum(bool(item.get("stale")) for item in candidates),
            "setup_qualified": setup_qualified_count,
            "forecast_trades": len(accepted_rows),
            "capital_required": round(accepted_notional, 2),
            "new_open_risk": round(accepted_risk, 2),
            "total_open_risk": round(current_open_risk + accepted_risk, 2),
            "potential_reward": round(accepted_reward, 2),
            "max_daily_loss": round(max_daily_loss, 2),
            "remaining_daily_loss": round(remaining_daily_loss, 2),
            "max_open_risk": round(max_open_risk, 2),
            "cash_remaining": round(cash_remaining, 2),
            "risk_budget_per_trade": round(risk_budget_per_trade, 2),
            "open_risk_capacity": open_risk_capacity,
            "position_capacity": position_capacity,
            "binding_limit": binding_limit,
            "reason_counts": reason_counts,
        },
    }
