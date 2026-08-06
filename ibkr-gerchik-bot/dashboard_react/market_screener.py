from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import pandas as pd


@dataclass(frozen=True)
class ScreenerParams:
    timeframe: str = "1D"
    anchor_date: str | None = None
    risk_pct: float = 0.005
    equity: float = 100_000.0
    rr: float = 2.0
    score_min: float = 0.0
    strategies: tuple[str, ...] = ("LP1", "LP2", "PRB1", "PRB2")
    side_filter: str = "ALL"
    pivot_k: int = 5
    min_touches: int = 2
    level_tolerance_atr: float = 0.10
    open_tolerance_atr: float = 0.15
    entry_atr_pct: float = 0.50
    lp1_delta_break: float = 0.05
    lp1_delta_close: float = 0.03
    lp2_delta_break: float = 0.05
    lp2_delta_close: float = 0.03
    prb1_delta_break: float = 0.05
    prb1_delta_close: float = 0.05
    prb2_delta_break: float = 0.05
    prb2_delta_close: float = 0.05
    prb2_hold_epsilon_atr: float = 0.05
    range_cap: float = 2.5
    two_bar_range_cap: float = 4.0
    lp1_volume_mult: float = 1.0
    lp2_volume_mult: float = 1.0
    prb_volume_mult: float = 1.2
    stop_buffer_atr: float = 0.25
    stop_variant: str = "strategy_default"
    gap_max_atr: float = 1.2
    chase_max_atr: float = 0.30
    lp2_overextended_atr: float = 1.2
    approach_lookback: int = 4
    approach_min_atr: float = 0.05
    slippage_per_share: float = 0.01
    fees_per_share: float = 0.005
    require_complete_quality: bool = False


def _num(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _round(value: Any, digits: int = 4) -> float | None:
    number = _num(value)
    return round(number, digits) if number is not None else None


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    data = frame.copy()
    if "t" in data.columns and "date" not in data.columns:
        data = data.rename(columns={"t": "date"})
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        data[column] = pd.to_numeric(data.get(column), errors="coerce")
    adjusted_columns = ("adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close")
    if all(column in data.columns for column in adjusted_columns):
        for source, target in zip(adjusted_columns, ("open", "high", "low", "close")):
            adjusted = pd.to_numeric(data[source], errors="coerce")
            data[target] = adjusted.where(adjusted.notna(), data[target])
        data["split_adjusted"] = True
    elif "split_adjustment_factor" in data.columns:
        factor = pd.to_numeric(data["split_adjustment_factor"], errors="coerce")
        valid_factor = factor.where(factor > 0, 1.0).fillna(1.0)
        for column in ("open", "high", "low", "close"):
            data[column] = data[column] * valid_factor
        data["split_adjusted"] = factor.notna()
    return (
        data.dropna(subset=["date", "open", "high", "low", "close", "volume"])
        .loc[lambda rows: rows["volume"] >= 0]
        .sort_values("date")
        .drop_duplicates(subset=["date"], keep="last")
        .reset_index(drop=True)
    )


def _apply_anchor_date(frame: pd.DataFrame, anchor_date: str | None) -> pd.DataFrame:
    if not anchor_date or frame.empty:
        return frame
    anchor = pd.to_datetime(anchor_date, errors="coerce")
    if pd.isna(anchor):
        return frame
    data = frame.copy()
    dates = pd.to_datetime(data["date"], errors="coerce")
    return data.loc[dates.dt.date <= pd.Timestamp(anchor).date()].reset_index(drop=True)


def _date_value(value: Any) -> object | None:
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _is_current_session_date(value: Any) -> bool:
    current = _date_value(value)
    return current == datetime.now().astimezone().date() if current is not None else False


def _wilder(values: pd.Series, period: int) -> pd.Series:
    out: list[float | None] = [None] * len(values)
    seed: list[float] = []
    prev: float | None = None
    for index, value in enumerate(values):
        number = _num(value)
        if number is None:
            continue
        if prev is None:
            seed.append(number)
            if len(seed) < period:
                continue
            prev = sum(seed[-period:]) / period
        else:
            prev = (prev * (period - 1) + number) / period
        out[index] = prev
    return pd.Series(out, index=values.index, dtype="float64")


def _add_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    prev_close = data["close"].shift(1)
    tr = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - prev_close).abs(),
            (data["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    median = tr.rolling(20, min_periods=5).median()
    mad = (tr - median).abs().rolling(20, min_periods=5).median()
    lower = (median - 3.5 * mad).clip(lower=0)
    upper = median + 3.5 * mad
    clipped_tr = tr.clip(lower=lower, upper=upper).fillna(tr)
    data["atr_clean_14"] = _wilder(clipped_tr, 14)
    data["avg_volume_20"] = data["volume"].rolling(20, min_periods=1).mean()
    data["median_volume_20"] = data["volume"].rolling(20, min_periods=5).median().shift(1)
    data["median_volume_20"] = data["median_volume_20"].fillna(data["volume"].expanding().median().shift(1)).fillna(data["volume"])
    data["median_dollar_volume_20"] = (data["close"] * data["volume"]).rolling(20, min_periods=1).median()
    data["range"] = data["high"] - data["low"]
    data["gap_atr"] = ((data["open"] - prev_close).abs() / data["atr_clean_14"]).replace([math.inf, -math.inf], None)
    if {"bid", "ask"}.issubset(data.columns):
        bid = pd.to_numeric(data["bid"], errors="coerce")
        ask = pd.to_numeric(data["ask"], errors="coerce")
        mid = (bid + ask) / 2
        data["signal_spread_pct"] = ((ask - bid).abs() / mid).where(mid > 0)
    elif "spread_pct" in data.columns:
        data["signal_spread_pct"] = pd.to_numeric(data["spread_pct"], errors="coerce")
    elif "signal_spread_pct" in data.columns:
        data["signal_spread_pct"] = pd.to_numeric(data["signal_spread_pct"], errors="coerce")
    else:
        data["signal_spread_pct"] = None
    data["median_spread_pct_20"] = pd.to_numeric(data["signal_spread_pct"], errors="coerce").rolling(20, min_periods=1).median()
    return data


def _event_dates(value: Any) -> list[pd.Timestamp]:
    dates: list[pd.Timestamp] = []
    if isinstance(value, dict):
        for item in value.values():
            dates.extend(_event_dates(item))
        return dates
    if isinstance(value, (list, tuple, set)):
        for item in value:
            dates.extend(_event_dates(item))
        return dates
    if isinstance(value, str):
        for match in re.findall(r"\d{4}-\d{2}-\d{2}", value):
            parsed = pd.to_datetime(match, errors="coerce")
            if not pd.isna(parsed):
                dates.append(pd.Timestamp(parsed).normalize())
    return dates


def _plan_event_dates(plan: dict[str, Any], tokens: tuple[str, ...]) -> list[pd.Timestamp]:
    out: list[pd.Timestamp] = []
    for key, value in plan.items():
        lower = str(key).lower()
        if any(token in lower for token in tokens):
            out.extend(_event_dates(value))
    return out


def _business_day_offset(left: pd.Timestamp, right: pd.Timestamp) -> int:
    left_date = pd.Timestamp(left).normalize()
    right_date = pd.Timestamp(right).normalize()
    if left_date == right_date:
        return 0
    start, end = sorted((left_date, right_date))
    sessions = max(0, len(pd.bdate_range(start, end)) - 1)
    return sessions if left_date > right_date else -sessions


def _has_event_in_window(latest_date: pd.Timestamp, dates: list[pd.Timestamp], before: int, after: int) -> bool:
    latest = pd.Timestamp(latest_date).normalize()
    return any(-before <= _business_day_offset(latest, date) <= after for date in dates)


def _pivot_levels(
    data: pd.DataFrame,
    *,
    k: int,
    min_touches: int,
    tolerance_atr: float = 0.10,
) -> list[dict[str, Any]]:
    if len(data) < (k * 2 + 5):
        return []
    latest_atr = _num(data["atr_clean_14"].iloc[-1]) or max(float(data["close"].iloc[-1]) * 0.01, 0.01)
    tolerance = max(latest_atr * tolerance_atr, float(data["close"].iloc[-1]) * 0.0005)
    pivots: list[dict[str, Any]] = []
    for idx in range(k, len(data) - k):
        window = data.iloc[idx - k : idx + k + 1]
        row = data.iloc[idx]
        high = float(row["high"])
        low = float(row["low"])
        if high >= float(window["high"].max()):
            pivots.append({"price": high, "kind": "resistance", "date": row["date"]})
        if low <= float(window["low"].min()):
            pivots.append({"price": low, "kind": "support", "date": row["date"]})

    levels: list[dict[str, Any]] = []
    for pivot in sorted(pivots, key=lambda item: item["price"]):
        bucket = next(
            (
                level
                for level in levels
                if level["kind"] == pivot["kind"] and abs(level["price"] - pivot["price"]) <= tolerance
            ),
            None,
        )
        if bucket is None:
            levels.append({**pivot, "touches": 1, "dates": [pivot["date"]]})
        else:
            bucket["price"] = (bucket["price"] * bucket["touches"] + pivot["price"]) / (bucket["touches"] + 1)
            bucket["touches"] += 1
            bucket["dates"].append(pivot["date"])

    active = []
    for level in levels:
        if level["touches"] < min_touches:
            continue
        level_price = float(level["price"])
        strength = min(1.0, 0.35 + 0.12 * level["touches"])
        active.append(
            {
                "price": level_price,
                "kind": level["kind"],
                "touches": level["touches"],
                "strength": strength,
                "tolerance": tolerance,
                "last_touch": str(pd.Timestamp(level["dates"][-1]).date()),
            }
        )
    return active


def _passes_filters(data: pd.DataFrame, plan: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    latest = data.iloc[-1]
    close = float(latest["close"])
    volume = _num(latest.get("avg_volume_20")) or 0.0
    dollar_volume = _num(latest.get("median_dollar_volume_20")) or 0.0
    gap_atr = _num(latest.get("gap_atr"))
    signal_spread = _num(latest.get("signal_spread_pct"))
    median_spread = _num(latest.get("median_spread_pct_20"))
    earnings = plan.get("earnings_event") if isinstance(plan, dict) else {}
    latest_date = pd.Timestamp(latest["date"])
    split_dates = _plan_event_dates(plan, ("split",))
    dividend_dates = _plan_event_dates(plan, ("dividend", "ex_div"))
    earnings_dates = _plan_event_dates(plan, ("earnings", "report_date"))
    listing_status = str(plan.get("listing_status") or plan.get("status") or "").strip().lower()
    listing_status_ok = None if not listing_status else listing_status in {"active", "listed", "tradable"}
    latest_split_adjusted = str(latest.get("split_adjusted", "")).strip().lower() in {"1", "true", "yes"}
    split_adjusted = True if bool(plan.get("split_adjusted")) or latest_split_adjusted else None
    corporate_actions_checked = bool(plan.get("corporate_actions_checked")) or bool(split_dates or dividend_dates)
    earnings_checked = "earnings_event" in plan or bool(earnings_dates)
    today = pd.Timestamp(datetime.now().astimezone()).date()
    historical_anchor = latest_date.date() < today
    reasons = []
    if not (5 <= close <= 500):
        reasons.append("price_out_of_range")
    if volume < 700_000 and dollar_volume < 10_000_000:
        reasons.append("illiquid")
    if median_spread is not None and median_spread > 0.0035:
        reasons.append("bad_spread")
    if signal_spread is not None and signal_spread > 0.005:
        reasons.append("bad_spread")
    if isinstance(plan, dict) and plan.get("news_blocked") and not historical_anchor:
        reasons.append("news_risk")
    if listing_status_ok is False:
        reasons.append("inactive_listing")
    if split_dates and _has_event_in_window(latest_date, split_dates, before=5, after=5):
        reasons.append("split_window")
    if dividend_dates and _has_event_in_window(latest_date, dividend_dates, before=3, after=1):
        reasons.append("dividend_window")
    if earnings_dates and _has_event_in_window(latest_date, earnings_dates, before=1, after=1):
        reasons.append("earnings_risk")
    elif isinstance(earnings, dict) and earnings and not earnings_dates and not historical_anchor:
        reasons.append("earnings_risk")
    reasons = list(dict.fromkeys(reasons))
    spread_ok = None
    if median_spread is not None or signal_spread is not None:
        spread_ok = "bad_spread" not in reasons
    corporate_action_ok = None
    if corporate_actions_checked:
        corporate_action_ok = not any(reason in reasons for reason in ("split_window", "dividend_window"))
    event_ok = None if not earnings_checked else not any(reason in reasons for reason in ("news_risk", "earnings_risk"))
    missing_quality_data = []
    if spread_ok is None:
        missing_quality_data.append("spread")
    if corporate_action_ok is None:
        missing_quality_data.append("corporate_actions")
    if listing_status_ok is None:
        missing_quality_data.append("listing_status")
    if event_ok is None:
        missing_quality_data.append("earnings_calendar")
    if split_adjusted is not True:
        missing_quality_data.append("split_adjustment")
    metrics = {
        "close": close,
        "avg_volume_20": volume,
        "median_dollar_volume_20": dollar_volume,
        "gap_atr": gap_atr,
        "volume_ok": volume >= 700_000 or dollar_volume >= 10_000_000,
        "median_spread_pct_20": median_spread,
        "signal_spread_pct": signal_spread,
        "spread_ok": spread_ok,
        "corporate_action_ok": corporate_action_ok,
        "event_ok": event_ok,
        "listing_status_ok": listing_status_ok,
        "split_adjusted": split_adjusted,
        "missing_quality_data": missing_quality_data,
        "data_quality_complete": not missing_quality_data,
    }
    return not reasons, reasons, metrics


def _stop_price(side: str, row: pd.Series, atr: float, buffer_atr: float) -> float:
    if side == "LONG":
        return float(row["low"]) - buffer_atr * atr
    return float(row["high"]) + buffer_atr * atr


def _side_prices(
    side: str,
    level_price: float,
    atr: float,
    params: ScreenerParams,
    *,
    signal_row: pd.Series,
    structure_rows: list[pd.Series] | None = None,
    day1_row: pd.Series | None = None,
) -> tuple[float, list[tuple[str, float]]]:
    mult = 1 if side == "LONG" else -1
    entry = level_price + mult * params.entry_atr_pct * atr
    stops = [
        ("behind_level", level_price - mult * params.stop_buffer_atr * atr),
        ("behind_signal_bar", _stop_price(side, signal_row, atr, params.stop_buffer_atr)),
    ]
    if structure_rows and len(structure_rows) > 1:
        if side == "LONG":
            stops.append(("behind_two_bar_structure", min(float(row["low"]) for row in structure_rows) - params.stop_buffer_atr * atr))
        else:
            stops.append(("behind_two_bar_structure", max(float(row["high"]) for row in structure_rows) + params.stop_buffer_atr * atr))
    if day1_row is not None:
        stops.append(("behind_day1", _stop_price(side, day1_row, atr, params.stop_buffer_atr)))
    return entry, stops


def _score_signal(
    level: dict[str, Any],
    data: pd.DataFrame,
    entry: float,
    *,
    signal_row: pd.Series,
    pattern_quality: float,
    metrics: dict[str, Any] | None = None,
) -> float:
    latest = data.iloc[-1]
    close = float(latest["close"])
    atr = max(float(latest["atr_clean_14"]), 0.0001)
    level_strength = float(level.get("strength") or 0.0)
    relative_volume = min(1.0, (_num(signal_row.get("volume")) or 0.0) / max((_num(signal_row.get("median_volume_20")) or 1.0), 1.0) / 2.0)
    distance_quality = max(0.0, 1.0 - abs(entry - close) / max(atr * 2.0, 0.0001))
    liquidity_quality = min(1.0, (_num(latest.get("median_dollar_volume_20")) or 0.0) / 50_000_000)
    event_quality = 1.0
    if metrics:
        quality_checks = (
            metrics.get("spread_ok"),
            metrics.get("corporate_action_ok"),
            metrics.get("event_ok"),
            metrics.get("listing_status_ok"),
        )
        if any(value is False for value in quality_checks):
            event_quality = 0.0
        elif any(value is None for value in quality_checks):
            event_quality = 0.75
    score = (
        0.30 * level_strength
        + 0.20 * relative_volume
        + 0.20 * distance_quality
        + 0.15 * max(0.0, min(1.0, pattern_quality))
        + 0.10 * liquidity_quality
        + 0.05 * event_quality
    )
    return round(max(0.0, min(1.0, score)), 4)


def _preferred_stop_variant(strategy: str) -> str:
    return {
        "LP1": "behind_signal_bar",
        "LP2": "behind_two_bar_structure",
        "PRB1": "behind_level",
        "PRB2": "behind_day1",
    }.get(strategy, "behind_level")


def _execution_details(
    *,
    side: str,
    entry_row: pd.Series,
    previous_row: pd.Series,
    planned_entry: float,
    atr: float,
    params: ScreenerParams,
    chase_status: str,
    forced_status: str | None = None,
) -> dict[str, Any]:
    mult = 1 if side == "LONG" else -1
    open_price = float(entry_row["open"])
    gap_atr = abs(open_price - float(previous_row["close"])) / max(atr, 0.0001)
    chase_atr = mult * (open_price - planned_entry) / max(atr, 0.0001)
    triggered = float(entry_row["high"]) >= planned_entry if side == "LONG" else float(entry_row["low"]) <= planned_entry
    effective_entry = planned_entry
    if triggered:
        effective_entry = max(planned_entry, open_price) if side == "LONG" else min(planned_entry, open_price)

    status = "READY"
    if forced_status:
        status = forced_status
    elif gap_atr > params.gap_max_atr:
        status = "missed_due_to_gap"
    elif chase_atr > params.chase_max_atr:
        status = chase_status
    elif not triggered:
        status = "pending_entry"

    return {
        "status": status,
        "gap_atr": gap_atr,
        "chase_atr": chase_atr,
        "entry_triggered": triggered,
        "effective_entry": effective_entry,
    }


def _make_signal(
    *,
    symbol: str,
    strategy: str,
    side: str,
    level: dict[str, Any],
    data: pd.DataFrame,
    params: ScreenerParams,
    reasoning: str,
    signal_index: int = -1,
    entry_index: int = -1,
    structure_indexes: list[int] | None = None,
    day1_index: int | None = None,
    pattern_quality: float = 0.5,
    metrics: dict[str, Any] | None = None,
    volume_filter_pass: bool = True,
    approach: dict[str, Any] | None = None,
    chase_status: str = "chased_gap_skip",
    forced_execution_status: str | None = None,
    entry_day_partial: bool = False,
) -> dict[str, Any]:
    latest = data.iloc[entry_index]
    signal_row = data.iloc[signal_index]
    atr = float(latest["atr_clean_14"])
    level_price = float(level["price"])
    structure_rows = [data.iloc[index] for index in (structure_indexes or [])]
    day1_row = data.iloc[day1_index] if day1_index is not None else None
    planned_entry, stops = _side_prices(
        side,
        level_price,
        atr,
        params,
        signal_row=signal_row,
        structure_rows=structure_rows,
        day1_row=day1_row,
    )
    execution = _execution_details(
        side=side,
        entry_row=latest,
        previous_row=data.iloc[entry_index - 1] if entry_index != 0 else signal_row,
        planned_entry=planned_entry,
        atr=atr,
        params=params,
        chase_status=chase_status,
        forced_status=forced_execution_status,
    )
    entry = float(execution["effective_entry"])
    if side == "LONG":
        valid_stops = [(name, price) for name, price in stops if price < entry]
    else:
        valid_stops = [(name, price) for name, price in stops if price > entry]
    requested_stop = str(params.stop_variant or "strategy_default").strip().lower()
    preferred_stop = _preferred_stop_variant(strategy) if requested_stop in {"", "auto", "strategy_default"} else requested_stop
    stop_choice = next((item for item in valid_stops if item[0] == preferred_stop), None)
    stop_variant, stop = stop_choice or min(valid_stops or stops, key=lambda item: abs(entry - item[1]))
    per_share_risk = abs(entry - stop)
    execution_cost_per_share = max(0.0, params.slippage_per_share) + max(0.0, params.fees_per_share)
    sized_per_share_risk = per_share_risk + execution_cost_per_share
    shares = math.floor(params.equity * params.risk_pct / sized_per_share_risk) if sized_per_share_risk > 0 else 0
    mult = 1 if side == "LONG" else -1
    take_profit = entry + mult * params.rr * per_share_risk
    score = _score_signal(level, data, entry, signal_row=signal_row, pattern_quality=pattern_quality, metrics=metrics)
    status = str(execution["status"])
    if shares < 1:
        status = "capital_insufficient"
    elif not valid_stops:
        status = "invalid_stop"
    elif status == "READY" and params.require_complete_quality and metrics and not metrics.get("data_quality_complete", False):
        status = "review_required"
    median_spread = metrics.get("median_spread_pct_20") if metrics else None
    signal_spread = metrics.get("signal_spread_pct") if metrics else None
    spread_ok = metrics.get("spread_ok") if metrics else None
    corporate_action_ok = metrics.get("corporate_action_ok") if metrics else None
    event_ok = metrics.get("event_ok") if metrics else None
    listing_status_ok = metrics.get("listing_status_ok") if metrics else None
    pattern_indexes = list(structure_indexes or [signal_index])
    if signal_index not in pattern_indexes:
        pattern_indexes.append(signal_index)
    if day1_index is not None and day1_index not in pattern_indexes:
        pattern_indexes.append(day1_index)
    normalized_pattern_indexes = sorted({_position_for_index(data, index) for index in pattern_indexes})
    pattern_bar_dates = [str(pd.Timestamp(data.iloc[index]["date"]).date()) for index in normalized_pattern_indexes]
    return {
        "ticker": symbol,
        "strategy": strategy,
        "side": side,
        "level_type": level["kind"],
        "level_origin_type": level.get("origin_kind") or level["kind"],
        "level_price": _round(level_price),
        "signal_bar_date": str(pd.Timestamp(signal_row["date"]).date()),
        "entry_day": str(pd.Timestamp(latest["date"]).date()),
        "entry_day_status": "IN_PROGRESS" if entry_day_partial else "COMPLETE",
        "pattern_bar_dates": pattern_bar_dates,
        "day1_date": str(pd.Timestamp(day1_row["date"]).date()) if day1_row is not None else None,
        "entry_price": _round(entry),
        "planned_entry_price": _round(planned_entry),
        "entry_atr_pct": params.entry_atr_pct,
        "entry_triggered": bool(execution["entry_triggered"]),
        "entry_gap_atr": _round(execution["gap_atr"], 3),
        "entry_chase_atr": _round(execution["chase_atr"], 3),
        "stop_variant": stop_variant,
        "stop_price": _round(stop),
        "stop_options": [{"variant": name, "price": _round(price)} for name, price in stops],
        "take_profit_price": _round(take_profit),
        "rr": params.rr,
        "atr_clean_14": _round(atr),
        "avg_volume_20": int(_num(latest.get("avg_volume_20")) or 0),
        "median_spread_pct_20": _round(median_spread * 100, 4) if median_spread is not None else None,
        "signal_spread_pct": _round(signal_spread * 100, 4) if signal_spread is not None else None,
        "volume_filter_pass": volume_filter_pass,
        "spread_filter_pass": spread_ok,
        "spread_filter_status": "unknown" if spread_ok is None else "pass" if spread_ok else "fail",
        "corporate_action_filter_pass": corporate_action_ok,
        "corporate_action_filter_status": "unknown" if corporate_action_ok is None else "pass" if corporate_action_ok else "fail",
        "earnings_filter_pass": event_ok,
        "listing_status_filter_pass": listing_status_ok,
        "split_adjusted": metrics.get("split_adjusted") if metrics else None,
        "data_quality_complete": metrics.get("data_quality_complete", False) if metrics else False,
        "missing_quality_data": list(metrics.get("missing_quality_data") or []) if metrics else [],
        "position_size_shares": shares,
        "price_risk_per_share": _round(per_share_risk),
        "execution_cost_per_share": _round(execution_cost_per_share),
        "sized_risk_per_share": _round(sized_per_share_risk),
        "risk_per_trade_pct": params.risk_pct * 100,
        "score": score,
        "pattern_quality": _round(pattern_quality, 3),
        "expected_R_multiple": params.rr,
        "liquidity_quality": min(1.0, (_num(latest.get("median_dollar_volume_20")) or 0.0) / 50_000_000),
        "status": status,
        "approach_direction": (approach or {}).get("direction"),
        "approach_change_atr": _round((approach or {}).get("change_atr"), 3),
        "reasoning": reasoning,
    }


def _bar_position(row: pd.Series) -> float:
    span = max(float(row["high"]) - float(row["low"]), 0.0001)
    return (float(row["close"]) - float(row["low"])) / span


def _position_for_index(data: pd.DataFrame, index: int) -> int:
    return index if index >= 0 else len(data) + index


def _approach_profile(
    data: pd.DataFrame,
    pattern_start_index: int,
    level_price: float,
    atr: float,
    params: ScreenerParams,
) -> dict[str, Any]:
    pattern_pos = _position_for_index(data, pattern_start_index)
    start = max(0, pattern_pos - max(2, params.approach_lookback))
    closes = pd.to_numeric(data.iloc[start:pattern_pos]["close"], errors="coerce").dropna()
    if len(closes) < 2:
        return {"direction": "UNKNOWN", "change_atr": 0.0, "last_close": None, "level_side": "UNKNOWN"}
    change_atr = (float(closes.iloc[-1]) - float(closes.iloc[0])) / max(atr, 0.0001)
    if change_atr >= params.approach_min_atr:
        direction = "UP"
    elif change_atr <= -params.approach_min_atr:
        direction = "DOWN"
    else:
        direction = "FLAT"
    last_close = float(closes.iloc[-1])
    level_side = "ABOVE" if last_close > level_price else "BELOW" if last_close < level_price else "AT"
    return {"direction": direction, "change_atr": change_atr, "last_close": last_close, "level_side": level_side}


def _level_for_role(level: dict[str, Any], kind: str) -> dict[str, Any]:
    return {**level, "origin_kind": level.get("origin_kind") or level.get("kind"), "kind": kind}


def _volume_ok(row: pd.Series, multiplier: float) -> bool:
    baseline = max(_num(row.get("median_volume_20")) or 0.0, 1.0)
    return float(row["volume"]) >= multiplier * baseline


def _detect_signals(
    symbol: str,
    data: pd.DataFrame,
    levels: list[dict[str, Any]],
    params: ScreenerParams,
    metrics: dict[str, Any] | None = None,
    entry_day_partial: bool | None = None,
) -> list[dict[str, Any]]:
    if len(data) < 4:
        return []
    entry_day = data.iloc[-1]
    if entry_day_partial is None:
        entry_day_partial = _is_current_session_date(entry_day.get("date"))
    signal_bar = data.iloc[-2]
    first_two_bar = data.iloc[-3]
    atr = _num(entry_day.get("atr_clean_14"))
    if atr is None or atr <= 0:
        return []
    out: list[dict[str, Any]] = []
    for level in levels:
        level_price = float(level["price"])
        tol = max(float(level.get("tolerance") or 0.0), params.level_tolerance_atr * atr)
        open_tol = max(tol, params.open_tolerance_atr * atr)
        lp1_approach = _approach_profile(data, -2, level_price, atr, params)
        multi_approach = _approach_profile(data, -3, level_price, atr, params)

        if "LP1" in params.strategies:
            body = abs(float(signal_bar["close"]) - float(signal_bar["open"]))
            lower_wick = min(float(signal_bar["open"]), float(signal_bar["close"])) - float(signal_bar["low"])
            upper_wick = float(signal_bar["high"]) - max(float(signal_bar["open"]), float(signal_bar["close"]))
            bar_range_ok = float(signal_bar["range"]) <= params.range_cap * atr
            volume_ok = _volume_ok(signal_bar, params.lp1_volume_mult)
            approach_long = lp1_approach["direction"] == "DOWN" and float(lp1_approach["last_close"] or -math.inf) >= level_price - open_tol
            approach_short = lp1_approach["direction"] == "UP" and float(lp1_approach["last_close"] or math.inf) <= level_price + open_tol
            if approach_long and volume_ok and signal_bar["open"] >= level_price - open_tol and signal_bar["low"] < level_price - params.lp1_delta_break * atr and signal_bar["close"] > level_price + params.lp1_delta_close * atr and bar_range_ok and lower_wick >= body and _bar_position(signal_bar) >= 0.60:
                quality = min(1.0, 0.45 + 0.35 * _bar_position(signal_bar) + 0.20 * min(1.0, lower_wick / max(body, 0.0001)))
                out.append(_make_signal(symbol=symbol, strategy="LP1", side="LONG", level=_level_for_role(level, "support"), data=data, params=params, reasoning="LP1 long: price approached support from above, made a one-bar false break, and reclaimed the level before the entry day.", signal_index=-2, entry_index=-1, pattern_quality=quality, metrics=metrics, volume_filter_pass=volume_ok, approach=lp1_approach, chase_status="missed_due_to_gap", entry_day_partial=entry_day_partial))
            if approach_short and volume_ok and signal_bar["open"] <= level_price + open_tol and signal_bar["high"] > level_price + params.lp1_delta_break * atr and signal_bar["close"] < level_price - params.lp1_delta_close * atr and bar_range_ok and upper_wick >= body and _bar_position(signal_bar) <= 0.40:
                quality = min(1.0, 0.45 + 0.35 * (1.0 - _bar_position(signal_bar)) + 0.20 * min(1.0, upper_wick / max(body, 0.0001)))
                out.append(_make_signal(symbol=symbol, strategy="LP1", side="SHORT", level=_level_for_role(level, "resistance"), data=data, params=params, reasoning="LP1 short: price approached resistance from below, made a one-bar false break, and rejected the level before the entry day.", signal_index=-2, entry_index=-1, pattern_quality=quality, metrics=metrics, volume_filter_pass=volume_ok, approach=lp1_approach, chase_status="missed_due_to_gap", entry_day_partial=entry_day_partial))

        if "LP2" in params.strategies:
            structure_range = max(float(first_two_bar["high"]), float(signal_bar["high"])) - min(float(first_two_bar["low"]), float(signal_bar["low"]))
            volume_ok = _volume_ok(signal_bar, params.lp2_volume_mult)
            approach_long = multi_approach["direction"] == "DOWN" and float(multi_approach["last_close"] or -math.inf) >= level_price - open_tol
            approach_short = multi_approach["direction"] == "UP" and float(multi_approach["last_close"] or math.inf) <= level_price + open_tol
            if approach_long and volume_ok and first_two_bar["low"] < level_price - params.lp2_delta_break * atr and first_two_bar["close"] < level_price and signal_bar["close"] > level_price + params.lp2_delta_close * atr and structure_range <= params.two_bar_range_cap * atr:
                quality = min(1.0, 0.55 + 0.20 * _bar_position(signal_bar) + 0.25 * min(1.0, (level_price - float(first_two_bar["low"])) / max(atr, 0.0001)))
                overextended = float(signal_bar["close"]) - level_price > params.lp2_overextended_atr * atr
                out.append(_make_signal(symbol=symbol, strategy="LP2", side="LONG", level=_level_for_role(level, "support"), data=data, params=params, reasoning="LP2 long: the first bar closed below support, the second reclaimed it, and entry is evaluated on the following day.", signal_index=-2, entry_index=-1, structure_indexes=[-3, -2], pattern_quality=quality, metrics=metrics, volume_filter_pass=volume_ok, approach=multi_approach, chase_status="missed_due_to_gap", forced_execution_status="overextended" if overextended else None, entry_day_partial=entry_day_partial))
            if approach_short and volume_ok and first_two_bar["high"] > level_price + params.lp2_delta_break * atr and first_two_bar["close"] > level_price and signal_bar["close"] < level_price - params.lp2_delta_close * atr and structure_range <= params.two_bar_range_cap * atr:
                quality = min(1.0, 0.55 + 0.20 * (1.0 - _bar_position(signal_bar)) + 0.25 * min(1.0, (float(first_two_bar["high"]) - level_price) / max(atr, 0.0001)))
                overextended = level_price - float(signal_bar["close"]) > params.lp2_overextended_atr * atr
                out.append(_make_signal(symbol=symbol, strategy="LP2", side="SHORT", level=_level_for_role(level, "resistance"), data=data, params=params, reasoning="LP2 short: the first bar closed above resistance, the second rejected it, and entry is evaluated on the following day.", signal_index=-2, entry_index=-1, structure_indexes=[-3, -2], pattern_quality=quality, metrics=metrics, volume_filter_pass=volume_ok, approach=multi_approach, chase_status="missed_due_to_gap", forced_execution_status="overextended" if overextended else None, entry_day_partial=entry_day_partial))

        if "PRB1" in params.strategies:
            volume_ok = _volume_ok(signal_bar, params.prb_volume_mult)
            approach_long = lp1_approach["direction"] == "UP" and float(lp1_approach["last_close"] or math.inf) <= level_price + open_tol
            approach_short = lp1_approach["direction"] == "DOWN" and float(lp1_approach["last_close"] or -math.inf) >= level_price - open_tol
            if approach_long and volume_ok and signal_bar["open"] <= level_price + open_tol and signal_bar["high"] > level_price + params.prb1_delta_break * atr and signal_bar["close"] > level_price + params.prb1_delta_close * atr and entry_day["open"] >= level_price - tol:
                quality = min(1.0, 0.60 + 0.20 * _bar_position(signal_bar) + 0.20 * min(1.0, (float(signal_bar["close"]) - level_price) / max(atr, 0.0001)))
                out.append(_make_signal(symbol=symbol, strategy="PRB1", side="LONG", level=_level_for_role(level, "resistance"), data=data, params=params, reasoning="PRB1 long: price approached resistance from below, the prior bar closed above it with volume, and the entry day opened holding the level.", signal_index=-2, entry_index=-1, pattern_quality=quality, metrics=metrics, volume_filter_pass=volume_ok, approach=lp1_approach, entry_day_partial=entry_day_partial))
            if approach_short and volume_ok and signal_bar["open"] >= level_price - open_tol and signal_bar["low"] < level_price - params.prb1_delta_break * atr and signal_bar["close"] < level_price - params.prb1_delta_close * atr and entry_day["open"] <= level_price + tol:
                quality = min(1.0, 0.60 + 0.20 * (1.0 - _bar_position(signal_bar)) + 0.20 * min(1.0, (level_price - float(signal_bar["close"])) / max(atr, 0.0001)))
                out.append(_make_signal(symbol=symbol, strategy="PRB1", side="SHORT", level=_level_for_role(level, "support"), data=data, params=params, reasoning="PRB1 short: price approached support from above, the prior bar closed below it with volume, and the entry day opened holding below the level.", signal_index=-2, entry_index=-1, pattern_quality=quality, metrics=metrics, volume_filter_pass=volume_ok, approach=lp1_approach, entry_day_partial=entry_day_partial))

        if "PRB2" in params.strategies:
            volume_ok = _volume_ok(first_two_bar, params.prb_volume_mult)
            approach_long = multi_approach["direction"] == "UP" and float(multi_approach["last_close"] or math.inf) <= level_price + open_tol
            approach_short = multi_approach["direction"] == "DOWN" and float(multi_approach["last_close"] or -math.inf) >= level_price - open_tol
            bars_in_range = float(first_two_bar["range"]) <= params.two_bar_range_cap * atr and float(signal_bar["range"]) <= params.two_bar_range_cap * atr
            long_entry_hold = entry_day["low"] >= level_price if entry_day_partial else entry_day["low"] >= level_price and entry_day["close"] >= level_price - params.prb2_hold_epsilon_atr * atr
            if approach_long and volume_ok and bars_in_range and first_two_bar["open"] <= level_price + open_tol and first_two_bar["high"] > level_price + params.prb2_delta_break * atr and first_two_bar["close"] > level_price + params.prb2_delta_close * atr and signal_bar["low"] >= level_price - tol and signal_bar["close"] >= level_price - params.prb2_hold_epsilon_atr * atr and entry_day["open"] >= level_price - tol and long_entry_hold:
                quality = min(1.0, 0.62 + 0.18 * _bar_position(first_two_bar) + 0.20 * min(1.0, (float(signal_bar["low"]) - (level_price - tol)) / max(tol, 0.0001)))
                out.append(_make_signal(symbol=symbol, strategy="PRB2", side="LONG", level=_level_for_role(level, "resistance"), data=data, params=params, reasoning="PRB2 long: the breakout bar closed above resistance with volume, day 1 held it, and day 2 opened above the level.", signal_index=-3, entry_index=-1, day1_index=-2, pattern_quality=quality, metrics=metrics, volume_filter_pass=volume_ok, approach=multi_approach, entry_day_partial=entry_day_partial))
            short_entry_hold = entry_day["high"] <= level_price if entry_day_partial else entry_day["high"] <= level_price and entry_day["close"] <= level_price + params.prb2_hold_epsilon_atr * atr
            if approach_short and volume_ok and bars_in_range and first_two_bar["open"] >= level_price - open_tol and first_two_bar["low"] < level_price - params.prb2_delta_break * atr and first_two_bar["close"] < level_price - params.prb2_delta_close * atr and signal_bar["high"] <= level_price + tol and signal_bar["close"] <= level_price + params.prb2_hold_epsilon_atr * atr and entry_day["open"] <= level_price + tol and short_entry_hold:
                quality = min(1.0, 0.62 + 0.18 * (1.0 - _bar_position(first_two_bar)) + 0.20 * min(1.0, ((level_price + tol) - float(signal_bar["high"])) / max(tol, 0.0001)))
                out.append(_make_signal(symbol=symbol, strategy="PRB2", side="SHORT", level=_level_for_role(level, "support"), data=data, params=params, reasoning="PRB2 short: the breakdown bar closed below support with volume, day 1 held below it, and day 2 opened below the level.", signal_index=-3, entry_index=-1, day1_index=-2, pattern_quality=quality, metrics=metrics, volume_filter_pass=volume_ok, approach=multi_approach, entry_day_partial=entry_day_partial))

    if params.side_filter in {"LONG", "SHORT"}:
        out = [signal for signal in out if signal["side"] == params.side_filter]
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for signal in out:
        key = (signal["strategy"], signal["side"], signal["signal_bar_date"], round(float(signal["level_price"]), 3))
        if key not in unique or float(signal.get("score") or 0.0) > float(unique[key].get("score") or 0.0):
            unique[key] = signal
    return [signal for signal in unique.values() if float(signal.get("score") or 0.0) >= params.score_min]


def run_market_screener(
    *,
    symbols: list[str],
    bars_loader: Callable[[str, str], pd.DataFrame],
    watchlist: dict[str, Any],
    params: ScreenerParams,
) -> dict[str, Any]:
    signals: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    near_misses: list[dict[str, Any]] = []
    reason_log: list[dict[str, Any]] = []
    levels_by_symbol: dict[str, list[dict[str, Any]]] = {}
    reason_counts: Counter[str] = Counter()
    requested_anchor = _date_value(params.anchor_date)
    anchor_warnings: list[dict[str, Any]] = []
    latest_available_dates: list[object] = []
    in_progress_symbols: list[str] = []

    for symbol in symbols:
        raw_frame = _normalize_frame(bars_loader(symbol, params.timeframe))
        raw_dates = {_date_value(value) for value in raw_frame.get("date", [])}
        raw_dates.discard(None)
        latest_raw_date = max(raw_dates) if raw_dates else None
        if latest_raw_date is not None:
            latest_available_dates.append(latest_raw_date)
        if requested_anchor is not None and requested_anchor not in raw_dates:
            anchor_warnings.append(
                {
                    "ticker": symbol,
                    "requested_anchor_date": str(requested_anchor),
                    "latest_available_date": str(latest_raw_date) if latest_raw_date is not None else None,
                    "reason": "anchor_data_unavailable",
                }
            )
        frame = _add_metrics(_apply_anchor_date(raw_frame, params.anchor_date))
        if frame.empty or len(frame) < 40:
            row = {"ticker": symbol, "reason": "insufficient_data"}
            rejected.append(row)
            reason_log.append(row)
            reason_counts["insufficient_data"] += 1
            continue
        entry_day_partial = _is_current_session_date(frame.iloc[-1]["date"])
        if entry_day_partial:
            in_progress_symbols.append(symbol)
        plan = watchlist.get(symbol, {}) if isinstance(watchlist, dict) else {}
        ok, filter_reasons, metrics = _passes_filters(frame, plan if isinstance(plan, dict) else {})
        levels = _pivot_levels(
            frame,
            k=params.pivot_k,
            min_touches=params.min_touches,
            tolerance_atr=params.level_tolerance_atr,
        )
        levels_by_symbol[symbol] = levels[:8]
        if not levels:
            row = {"ticker": symbol, "reason": "no_level"}
            rejected.append(row)
            reason_log.append(row)
            reason_counts["no_level"] += 1
            continue
        if not ok:
            reason = filter_reasons[0]
            row = {"ticker": symbol, "reason": reason, "reasons": filter_reasons}
            rejected.append(row)
            reason_log.append(row)
            reason_counts.update(filter_reasons)
            continue
        found = _detect_signals(symbol, frame, levels, params, metrics=metrics, entry_day_partial=entry_day_partial)
        if found:
            signals.extend(found)
            continue
        latest_close = float(frame["close"].iloc[-1])
        latest_atr = max(float(frame["atr_clean_14"].iloc[-1]), 0.0001)
        nearest = min(levels, key=lambda level: abs(float(level["price"]) - latest_close) / latest_atr)
        row = {
            "ticker": symbol,
            "reason": "broken_pattern",
            "nearest_level": _round(nearest["price"]),
            "level_type": nearest["kind"],
            "distance_atr": _round(abs(float(nearest["price"]) - latest_close) / latest_atr, 3),
            "levels_found": len(levels),
        }
        near_misses.append(row)
        reason_log.append(row)
        reason_counts["broken_pattern"] += 1

    signals.sort(key=lambda item: (-float(item.get("score") or 0.0), -float(item.get("expected_R_multiple") or 0.0), -float(item.get("liquidity_quality") or 0.0), item.get("ticker", "")))
    anchor_status = "UNSPECIFIED"
    if requested_anchor is not None:
        if anchor_warnings and in_progress_symbols:
            anchor_status = "PARTIAL"
        elif anchor_warnings:
            anchor_status = "MISSING"
        elif in_progress_symbols:
            anchor_status = "IN_PROGRESS"
        else:
            anchor_status = "AVAILABLE"
    elif in_progress_symbols:
        anchor_status = "IN_PROGRESS"
    anchor_message = None
    if anchor_warnings:
        latest = max(latest_available_dates) if latest_available_dates else None
        anchor_message = (
            f"No stored bar for requested anchor {requested_anchor}; latest available data is {latest}."
            if latest is not None
            else f"No stored bar for requested anchor {requested_anchor}."
        )
    elif in_progress_symbols:
        anchor_message = "Anchor includes an in-progress session; opening price is usable, but the candle is not complete."
    return {
        "scan_timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "timeframe": params.timeframe,
        "anchor_date": params.anchor_date,
        "anchor_status": anchor_status,
        "latest_available_date": str(max(latest_available_dates)) if latest_available_dates else None,
        "anchor_message": anchor_message,
        "anchor_warnings": anchor_warnings,
        "universe_size": len(symbols),
        "signals_found": len(signals),
        "signals": signals,
        "rejected": rejected,
        "near_misses": sorted(near_misses, key=lambda item: float(item.get("distance_atr") or 999))[:50],
        "levels_by_symbol": levels_by_symbol,
        "reason_counts": dict(reason_counts.most_common()),
        "reason_log": reason_log,
        "params": {
            "risk_pct": params.risk_pct,
            "anchor_date": params.anchor_date,
            "equity": params.equity,
            "rr": params.rr,
            "score_min": params.score_min,
            "strategies": list(params.strategies),
            "side_filter": params.side_filter,
            "pivot_k": params.pivot_k,
            "min_touches": params.min_touches,
            "level_tolerance_atr": params.level_tolerance_atr,
            "open_tolerance_atr": params.open_tolerance_atr,
            "entry_atr_pct": params.entry_atr_pct,
            "lp1_delta_break": params.lp1_delta_break,
            "lp1_delta_close": params.lp1_delta_close,
            "lp2_delta_break": params.lp2_delta_break,
            "lp2_delta_close": params.lp2_delta_close,
            "prb1_delta_break": params.prb1_delta_break,
            "prb1_delta_close": params.prb1_delta_close,
            "prb2_delta_break": params.prb2_delta_break,
            "prb2_delta_close": params.prb2_delta_close,
            "prb2_hold_epsilon_atr": params.prb2_hold_epsilon_atr,
            "range_cap": params.range_cap,
            "two_bar_range_cap": params.two_bar_range_cap,
            "lp1_volume_mult": params.lp1_volume_mult,
            "lp2_volume_mult": params.lp2_volume_mult,
            "prb_volume_mult": params.prb_volume_mult,
            "stop_buffer_atr": params.stop_buffer_atr,
            "stop_variant": params.stop_variant,
            "gap_max_atr": params.gap_max_atr,
            "chase_max_atr": params.chase_max_atr,
            "lp2_overextended_atr": params.lp2_overextended_atr,
            "approach_lookback": params.approach_lookback,
            "approach_min_atr": params.approach_min_atr,
            "slippage_per_share": params.slippage_per_share,
            "fees_per_share": params.fees_per_share,
            "require_complete_quality": params.require_complete_quality,
        },
    }
