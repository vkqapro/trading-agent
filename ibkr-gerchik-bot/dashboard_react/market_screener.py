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
    entry_atr_pct: float = 0.50
    delta_break: float = 0.05
    delta_close: float = 0.03
    range_cap: float = 2.5
    two_bar_range_cap: float = 4.0
    volume_mult: float = 1.2
    lp2_volume_mult: float = 1.0
    stop_buffer_atr: float = 0.25
    gap_max_atr: float = 1.2


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
    return data.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date").reset_index(drop=True)


def _apply_anchor_date(frame: pd.DataFrame, anchor_date: str | None) -> pd.DataFrame:
    if not anchor_date or frame.empty:
        return frame
    anchor = pd.to_datetime(anchor_date, errors="coerce")
    if pd.isna(anchor):
        return frame
    data = frame.copy()
    dates = pd.to_datetime(data["date"], errors="coerce")
    return data.loc[dates.dt.date <= pd.Timestamp(anchor).date()].reset_index(drop=True)


def _wilder(values: pd.Series, period: int) -> pd.Series:
    out: list[float | None] = []
    prev: float | None = None
    for value in values:
        if not math.isfinite(float(value)):
            out.append(prev)
            continue
        if prev is None:
            prev = float(value)
        else:
            prev = (prev * (period - 1) + float(value)) / period
        out.append(prev)
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
    data["atr_clean_14"] = _wilder(tr.clip(lower=lower, upper=upper).fillna(tr), 14)
    data["avg_volume_20"] = data["volume"].rolling(20, min_periods=1).mean()
    data["median_volume_20"] = data["volume"].rolling(20, min_periods=1).median()
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


def _has_event_in_window(latest_date: pd.Timestamp, dates: list[pd.Timestamp], before: int, after: int) -> bool:
    latest = pd.Timestamp(latest_date).normalize()
    return any(-before <= (latest - date).days <= after for date in dates)


def _pivot_levels(data: pd.DataFrame, *, k: int, min_touches: int) -> list[dict[str, Any]]:
    if len(data) < (k * 2 + 5):
        return []
    latest_atr = _num(data["atr_clean_14"].iloc[-1]) or max(float(data["close"].iloc[-1]) * 0.01, 0.01)
    tolerance = max(latest_atr * 0.10, float(data["close"].iloc[-1]) * 0.0005)
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

    close = float(data["close"].iloc[-1])
    active = []
    for level in levels:
        if level["touches"] < min_touches:
            continue
        level_price = float(level["price"])
        invalid_break = 0.30 * latest_atr
        if level["kind"] == "support" and close < level_price - invalid_break:
            continue
        if level["kind"] == "resistance" and close > level_price + invalid_break:
            continue
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
    today = pd.Timestamp(datetime.now().astimezone()).date()
    historical_anchor = latest_date.date() < today
    reasons = []
    if not (5 <= close <= 500):
        reasons.append("price_out_of_range")
    if volume < 700_000 and dollar_volume < 10_000_000:
        reasons.append("illiquid")
    if gap_atr is not None and gap_atr > 1.2:
        reasons.append("gap_skip")
    if median_spread is not None and median_spread > 0.0035:
        reasons.append("bad_spread")
    if signal_spread is not None and signal_spread > 0.005:
        reasons.append("bad_spread")
    if isinstance(plan, dict) and plan.get("news_blocked") and not historical_anchor:
        reasons.append("news_risk")
    if listing_status and listing_status not in {"active", "listed", "tradable"}:
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
    if split_dates or dividend_dates:
        corporate_action_ok = not any(reason in reasons for reason in ("split_window", "dividend_window"))
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
        "event_ok": not any(reason in reasons for reason in ("news_risk", "earnings_risk")),
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
    pattern_quality: float,
    metrics: dict[str, Any] | None = None,
) -> float:
    latest = data.iloc[-1]
    close = float(latest["close"])
    atr = max(float(latest["atr_clean_14"]), 0.0001)
    level_strength = float(level.get("strength") or 0.0)
    relative_volume = min(1.0, (_num(latest.get("volume")) or 0.0) / max((_num(latest.get("median_volume_20")) or 1.0), 1.0) / 2.0)
    distance_quality = max(0.0, 1.0 - abs(entry - close) / max(atr * 2.0, 0.0001))
    liquidity_quality = min(1.0, (_num(latest.get("median_dollar_volume_20")) or 0.0) / 50_000_000)
    event_quality = 1.0
    if metrics:
        if metrics.get("spread_ok") is False or metrics.get("corporate_action_ok") is False or metrics.get("event_ok") is False:
            event_quality = 0.0
        elif metrics.get("spread_ok") is None or metrics.get("corporate_action_ok") is None:
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
) -> dict[str, Any]:
    latest = data.iloc[entry_index]
    signal_row = data.iloc[signal_index]
    atr = float(latest["atr_clean_14"])
    level_price = float(level["price"])
    structure_rows = [data.iloc[index] for index in (structure_indexes or [])]
    day1_row = data.iloc[day1_index] if day1_index is not None else None
    entry, stops = _side_prices(
        side,
        level_price,
        atr,
        params,
        signal_row=signal_row,
        structure_rows=structure_rows,
        day1_row=day1_row,
    )
    if side == "LONG":
        valid_stops = [(name, price) for name, price in stops if price < entry]
    else:
        valid_stops = [(name, price) for name, price in stops if price > entry]
    stop_variant, stop = min(valid_stops or stops, key=lambda item: abs(entry - item[1]))
    per_share_risk = abs(entry - stop)
    shares = math.floor(params.equity * params.risk_pct / per_share_risk) if per_share_risk > 0 else 0
    mult = 1 if side == "LONG" else -1
    take_profit = entry + mult * params.rr * per_share_risk
    score = _score_signal(level, data, entry, pattern_quality=pattern_quality, metrics=metrics)
    status = "READY" if shares >= 1 and valid_stops else "capital_insufficient" if shares < 1 else "invalid_stop"
    median_spread = metrics.get("median_spread_pct_20") if metrics else None
    signal_spread = metrics.get("signal_spread_pct") if metrics else None
    spread_ok = metrics.get("spread_ok") if metrics else None
    corporate_action_ok = metrics.get("corporate_action_ok") if metrics else None
    event_ok = metrics.get("event_ok") if metrics else None
    return {
        "ticker": symbol,
        "strategy": strategy,
        "side": side,
        "level_type": level["kind"],
        "level_price": _round(level_price),
        "signal_bar_date": str(pd.Timestamp(signal_row["date"]).date()),
        "entry_day": str(pd.Timestamp(latest["date"]).date()),
        "entry_price": _round(entry),
        "entry_atr_pct": params.entry_atr_pct,
        "stop_variant": stop_variant,
        "stop_price": _round(stop),
        "stop_options": [{"variant": name, "price": _round(price)} for name, price in stops],
        "take_profit_price": _round(take_profit),
        "rr": params.rr,
        "atr_clean_14": _round(atr),
        "avg_volume_20": int(_num(latest.get("avg_volume_20")) or 0),
        "median_spread_pct_20": _round(median_spread * 100, 4) if median_spread is not None else None,
        "signal_spread_pct": _round(signal_spread * 100, 4) if signal_spread is not None else None,
        "volume_filter_pass": True,
        "spread_filter_pass": spread_ok,
        "spread_filter_status": "unknown" if spread_ok is None else "pass" if spread_ok else "fail",
        "corporate_action_filter_pass": corporate_action_ok,
        "corporate_action_filter_status": "unknown" if corporate_action_ok is None else "pass" if corporate_action_ok else "fail",
        "earnings_filter_pass": event_ok,
        "position_size_shares": shares,
        "risk_per_trade_pct": params.risk_pct * 100,
        "score": score,
        "pattern_quality": _round(pattern_quality, 3),
        "expected_R_multiple": params.rr,
        "liquidity_quality": min(1.0, (_num(latest.get("median_dollar_volume_20")) or 0.0) / 50_000_000),
        "status": status,
        "reasoning": reasoning,
    }


def _bar_position(row: pd.Series) -> float:
    span = max(float(row["high"]) - float(row["low"]), 0.0001)
    return (float(row["close"]) - float(row["low"])) / span


def _gap_ok(entry_row: pd.Series, previous_row: pd.Series, atr: float, params: ScreenerParams) -> bool:
    gap_atr = abs(float(entry_row["open"]) - float(previous_row["close"])) / max(atr, 0.0001)
    return gap_atr <= params.gap_max_atr


def _detect_signals(
    symbol: str,
    data: pd.DataFrame,
    levels: list[dict[str, Any]],
    params: ScreenerParams,
    metrics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if len(data) < 3:
        return []
    breakbar = data.iloc[-3] if len(data) >= 4 else None
    prev = data.iloc[-2]
    latest = data.iloc[-1]
    atr = _num(latest.get("atr_clean_14"))
    if atr is None or atr <= 0:
        return []
    out: list[dict[str, Any]] = []
    for level in levels:
        level_price = float(level["price"])
        tol = max(float(level.get("tolerance") or 0.0), 0.10 * atr)
        support = level["kind"] == "support"
        resistance = level["kind"] == "resistance"
        bar_range_ok = float(latest["range"]) <= params.range_cap * atr
        prev_volume_ok = float(prev["volume"]) >= params.volume_mult * max(float(prev.get("median_volume_20") or 0.0), 1.0)
        lp2_volume_ok = float(latest["volume"]) >= params.lp2_volume_mult * max(float(latest.get("median_volume_20") or 0.0), 1.0)

        if "LP1" in params.strategies and support:
            body = abs(float(latest["close"]) - float(latest["open"]))
            lower_wick = min(float(latest["open"]), float(latest["close"])) - float(latest["low"])
            quality = min(1.0, 0.45 + 0.35 * (_bar_position(latest)) + 0.20 * min(1.0, lower_wick / max(body, 0.0001)))
            if latest["open"] >= level_price - tol and latest["low"] < level_price - params.delta_break * atr and latest["close"] > level_price + params.delta_close * atr and bar_range_ok and lower_wick >= body and _bar_position(latest) >= 0.60:
                out.append(_make_signal(symbol=symbol, strategy="LP1", side="LONG", level=level, data=data, params=params, reasoning="LP1 long: support false break and close reclaimed the level.", signal_index=-1, entry_index=-1, pattern_quality=quality, metrics=metrics))
        if "LP1" in params.strategies and resistance:
            body = abs(float(latest["close"]) - float(latest["open"]))
            upper_wick = float(latest["high"]) - max(float(latest["open"]), float(latest["close"]))
            quality = min(1.0, 0.45 + 0.35 * (1.0 - _bar_position(latest)) + 0.20 * min(1.0, upper_wick / max(body, 0.0001)))
            if latest["open"] <= level_price + tol and latest["high"] > level_price + params.delta_break * atr and latest["close"] < level_price - params.delta_close * atr and bar_range_ok and upper_wick >= body and _bar_position(latest) <= 0.40:
                out.append(_make_signal(symbol=symbol, strategy="LP1", side="SHORT", level=level, data=data, params=params, reasoning="LP1 short: resistance false break and close rejected below the level.", signal_index=-1, entry_index=-1, pattern_quality=quality, metrics=metrics))

        if "LP2" in params.strategies and support:
            structure_range = max(float(prev["high"]), float(latest["high"])) - min(float(prev["low"]), float(latest["low"]))
            false_side_close = float(prev["close"]) < level_price
            if prev["low"] < level_price - params.delta_break * atr and false_side_close and latest["close"] > level_price + params.delta_close * atr and structure_range <= params.two_bar_range_cap * atr and lp2_volume_ok:
                quality = min(1.0, 0.55 + 0.20 * _bar_position(latest) + 0.25 * min(1.0, (level_price - float(prev["low"])) / max(atr, 0.0001)))
                out.append(_make_signal(symbol=symbol, strategy="LP2", side="LONG", level=level, data=data, params=params, reasoning="LP2 long: first bar closed on the false-break side and current bar reclaimed support.", signal_index=-1, entry_index=-1, structure_indexes=[-2, -1], pattern_quality=quality, metrics=metrics))
        if "LP2" in params.strategies and resistance:
            structure_range = max(float(prev["high"]), float(latest["high"])) - min(float(prev["low"]), float(latest["low"]))
            false_side_close = float(prev["close"]) > level_price
            if prev["high"] > level_price + params.delta_break * atr and false_side_close and latest["close"] < level_price - params.delta_close * atr and structure_range <= params.two_bar_range_cap * atr and lp2_volume_ok:
                quality = min(1.0, 0.55 + 0.20 * (1.0 - _bar_position(latest)) + 0.25 * min(1.0, (float(prev["high"]) - level_price) / max(atr, 0.0001)))
                out.append(_make_signal(symbol=symbol, strategy="LP2", side="SHORT", level=level, data=data, params=params, reasoning="LP2 short: first bar closed on the false-break side and current bar rejected resistance.", signal_index=-1, entry_index=-1, structure_indexes=[-2, -1], pattern_quality=quality, metrics=metrics))

        if "PRB1" in params.strategies and resistance:
            if prev["open"] <= level_price + tol and prev["high"] > level_price + params.delta_break * atr and prev["close"] > level_price + params.delta_close * atr and prev_volume_ok and latest["open"] >= level_price - tol and _gap_ok(latest, prev, atr, params):
                quality = min(1.0, 0.60 + 0.20 * _bar_position(prev) + 0.20 * min(1.0, (float(prev["close"]) - level_price) / max(atr, 0.0001)))
                out.append(_make_signal(symbol=symbol, strategy="PRB1", side="LONG", level=level, data=data, params=params, reasoning="PRB1 long: prior bar broke resistance, closed above it with volume, and the zero day opened holding the level.", signal_index=-2, entry_index=-1, pattern_quality=quality, metrics=metrics))
        if "PRB1" in params.strategies and support:
            if prev["open"] >= level_price - tol and prev["low"] < level_price - params.delta_break * atr and prev["close"] < level_price - params.delta_close * atr and prev_volume_ok and latest["open"] <= level_price + tol and _gap_ok(latest, prev, atr, params):
                quality = min(1.0, 0.60 + 0.20 * (1.0 - _bar_position(prev)) + 0.20 * min(1.0, (level_price - float(prev["close"])) / max(atr, 0.0001)))
                out.append(_make_signal(symbol=symbol, strategy="PRB1", side="SHORT", level=level, data=data, params=params, reasoning="PRB1 short: prior bar broke support, closed below it with volume, and the zero day opened holding the level.", signal_index=-2, entry_index=-1, pattern_quality=quality, metrics=metrics))

        if breakbar is not None and "PRB2" in params.strategies and resistance:
            break_volume_ok = float(breakbar["volume"]) >= params.volume_mult * max(float(breakbar.get("median_volume_20") or 0.0), 1.0)
            if breakbar["open"] <= level_price + tol and breakbar["high"] > level_price + params.delta_break * atr and breakbar["close"] > level_price + params.delta_close * atr and break_volume_ok and prev["low"] >= level_price - tol and prev["close"] >= level_price and latest["open"] >= level_price - tol and _gap_ok(latest, prev, atr, params):
                quality = min(1.0, 0.62 + 0.18 * _bar_position(breakbar) + 0.20 * min(1.0, (float(prev["low"]) - (level_price - tol)) / max(tol, 0.0001)))
                out.append(_make_signal(symbol=symbol, strategy="PRB2", side="LONG", level=level, data=data, params=params, reasoning="PRB2 long: breakout day closed above resistance, day1 held the level, and entry day opened above the level.", signal_index=-3, entry_index=-1, day1_index=-2, pattern_quality=quality, metrics=metrics))
        if breakbar is not None and "PRB2" in params.strategies and support:
            break_volume_ok = float(breakbar["volume"]) >= params.volume_mult * max(float(breakbar.get("median_volume_20") or 0.0), 1.0)
            if breakbar["open"] >= level_price - tol and breakbar["low"] < level_price - params.delta_break * atr and breakbar["close"] < level_price - params.delta_close * atr and break_volume_ok and prev["high"] <= level_price + tol and prev["close"] <= level_price and latest["open"] <= level_price + tol and _gap_ok(latest, prev, atr, params):
                quality = min(1.0, 0.62 + 0.18 * (1.0 - _bar_position(breakbar)) + 0.20 * min(1.0, ((level_price + tol) - float(prev["high"])) / max(tol, 0.0001)))
                out.append(_make_signal(symbol=symbol, strategy="PRB2", side="SHORT", level=level, data=data, params=params, reasoning="PRB2 short: breakdown day closed below support, day1 held below the level, and entry day opened below the level.", signal_index=-3, entry_index=-1, day1_index=-2, pattern_quality=quality, metrics=metrics))

    if params.side_filter in {"LONG", "SHORT"}:
        out = [signal for signal in out if signal["side"] == params.side_filter]
    return [signal for signal in out if float(signal.get("score") or 0.0) >= params.score_min]


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

    for symbol in symbols:
        frame = _add_metrics(_apply_anchor_date(_normalize_frame(bars_loader(symbol, params.timeframe)), params.anchor_date))
        if frame.empty or len(frame) < 40:
            row = {"ticker": symbol, "reason": "insufficient_data"}
            rejected.append(row)
            reason_log.append(row)
            reason_counts["insufficient_data"] += 1
            continue
        plan = watchlist.get(symbol, {}) if isinstance(watchlist, dict) else {}
        ok, filter_reasons, metrics = _passes_filters(frame, plan if isinstance(plan, dict) else {})
        levels = _pivot_levels(frame, k=params.pivot_k, min_touches=params.min_touches)
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
        found = _detect_signals(symbol, frame, levels, params, metrics=metrics)
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
    return {
        "scan_timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "timeframe": params.timeframe,
        "anchor_date": params.anchor_date,
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
            "entry_atr_pct": params.entry_atr_pct,
            "delta_break": params.delta_break,
            "delta_close": params.delta_close,
            "range_cap": params.range_cap,
            "two_bar_range_cap": params.two_bar_range_cap,
            "volume_mult": params.volume_mult,
            "lp2_volume_mult": params.lp2_volume_mult,
            "stop_buffer_atr": params.stop_buffer_atr,
            "gap_max_atr": params.gap_max_atr,
        },
    }
