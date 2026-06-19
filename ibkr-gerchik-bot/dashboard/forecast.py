"""Read-only risk scenario forecasting for the dashboard."""

from __future__ import annotations

import math
import logging
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable

import pandas as pd

from src.strategy.levels import Level
from src.strategy.strategy_router import route_strategies
from src.config import LOGGER

FORECAST_ENGINE_VERSION = 1


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
) -> dict[str, Any] | None:
    risk_per_share = abs(entry - stop)
    reward_per_share = abs(target - entry)
    if entry <= 0 or stop <= 0 or target <= 0 or risk_per_share <= 0:
        return None
    if direction == "long" and not (stop < entry < target):
        return None
    if direction == "short" and not (target < entry < stop):
        return None
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
                    )
                    if item:
                        candidates.append(item)
    return candidates


def replay_active_candidates(
    watchlist: dict[str, Any],
    bars_loader: Callable[[str, str], pd.DataFrame],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Replay saved bars through the strategy router without persisting decisions."""
    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
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
        previous_level = LOGGER.level
        try:
            LOGGER.setLevel(logging.WARNING)
            levels = [Level(**item) for item in raw_levels if isinstance(item, dict)]
            signals = route_strategies(
                symbol,
                bars,
                levels,
                news_context={"risk_level": "LOW"},
                persist=False,
            )
        except (TypeError, ValueError, KeyError) as exc:
            warnings.append(f"{symbol}: replay unavailable ({exc})")
            continue
        finally:
            LOGGER.setLevel(previous_level)
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
                candidates.append(item)
    return candidates, warnings


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
    max_open_risk = max(0.0, scenario.account_equity * scenario.max_open_risk_pct)
    accepted_risk = 0.0
    accepted_notional = 0.0
    accepted_reward = 0.0
    cash_remaining = max(0.0, scenario.cash_available)
    accepted_symbols: set[str] = set()
    rows: list[dict[str, Any]] = []

    ordered = sorted(
        candidates,
        key=lambda item: (
            0 if item.get("source") == "ACTIVE REPLAY" else 1,
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
        reasons: list[str] = []
        quantity = (
            math.floor((scenario.account_equity * scenario.risk_per_trade) / risk_per_share)
            if scenario.account_equity > 0 and risk_per_share > 0
            else 0
        )
        risk_amount = quantity * risk_per_share
        notional = quantity * entry
        potential_reward = quantity * reward_per_share

        if remaining_daily_loss <= 0:
            reasons.append("daily_loss_limit_reached")
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
                "status": "FORECAST TRADE" if accepted else "FILTERED",
                "reason": "Eligible under scenario" if accepted else _reason_label(reasons),
            }
        )

    accepted_rows = [row for row in rows if row["status"] == "FORECAST TRADE"]
    return {
        "scenario": asdict(scenario),
        "rows": rows,
        "accepted": accepted_rows,
        "summary": {
            "possible_signals": len(candidates),
            "active_signals": sum(item.get("source") == "ACTIVE REPLAY" for item in candidates),
            "projected_setups": sum(item.get("source") == "PROJECTED" for item in candidates),
            "forecast_trades": len(accepted_rows),
            "capital_required": round(accepted_notional, 2),
            "new_open_risk": round(accepted_risk, 2),
            "total_open_risk": round(current_open_risk + accepted_risk, 2),
            "potential_reward": round(accepted_reward, 2),
            "max_daily_loss": round(max_daily_loss, 2),
            "remaining_daily_loss": round(remaining_daily_loss, 2),
            "max_open_risk": round(max_open_risk, 2),
            "cash_remaining": round(cash_remaining, 2),
        },
    }
