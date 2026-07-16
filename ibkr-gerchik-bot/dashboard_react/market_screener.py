from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import pandas as pd


@dataclass(frozen=True)
class ScreenerParams:
    timeframe: str = "1D"
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
    volume_mult: float = 1.2


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
    return data


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
    earnings = plan.get("earnings_event") if isinstance(plan, dict) else {}
    reasons = []
    if not (5 <= close <= 500):
        reasons.append("price_out_of_range")
    if volume < 700_000 and dollar_volume < 10_000_000:
        reasons.append("illiquid")
    if gap_atr is not None and gap_atr > 1.2:
        reasons.append("gap_skip")
    if isinstance(plan, dict) and plan.get("news_blocked"):
        reasons.append("news_risk")
    if isinstance(earnings, dict) and earnings:
        reasons.append("earnings_risk")
    metrics = {
        "close": close,
        "avg_volume_20": volume,
        "median_dollar_volume_20": dollar_volume,
        "gap_atr": gap_atr,
        "volume_ok": volume >= 700_000 or dollar_volume >= 10_000_000,
        "spread_ok": True,
        "event_ok": not any(reason in reasons for reason in ("news_risk", "earnings_risk")),
    }
    return not reasons, reasons, metrics


def _side_prices(side: str, level_price: float, atr: float, params: ScreenerParams) -> tuple[float, list[tuple[str, float]]]:
    mult = 1 if side == "LONG" else -1
    entry = level_price + mult * params.entry_atr_pct * atr
    stops = [
        ("behind_level", level_price - mult * 0.25 * atr),
        ("behind_signal_bar", level_price - mult * 0.60 * atr),
    ]
    return entry, stops


def _score_signal(level: dict[str, Any], data: pd.DataFrame, entry: float) -> float:
    latest = data.iloc[-1]
    close = float(latest["close"])
    atr = max(float(latest["atr_clean_14"]), 0.0001)
    level_strength = float(level.get("strength") or 0.0)
    relative_volume = min(1.0, (_num(latest.get("volume")) or 0.0) / max((_num(latest.get("median_volume_20")) or 1.0), 1.0) / 2.0)
    distance_quality = max(0.0, 1.0 - abs(entry - close) / max(atr * 2.0, 0.0001))
    return round(max(0.0, min(1.0, 0.42 * level_strength + 0.28 * relative_volume + 0.30 * distance_quality)), 4)


def _make_signal(
    *,
    symbol: str,
    strategy: str,
    side: str,
    level: dict[str, Any],
    data: pd.DataFrame,
    params: ScreenerParams,
    reasoning: str,
) -> dict[str, Any]:
    latest = data.iloc[-1]
    atr = float(latest["atr_clean_14"])
    level_price = float(level["price"])
    entry, stops = _side_prices(side, level_price, atr, params)
    stop_variant, stop = min(stops, key=lambda item: abs(entry - item[1]))
    per_share_risk = abs(entry - stop)
    shares = math.floor(params.equity * params.risk_pct / per_share_risk) if per_share_risk > 0 else 0
    mult = 1 if side == "LONG" else -1
    take_profit = entry + mult * params.rr * per_share_risk
    score = _score_signal(level, data, entry)
    status = "READY" if shares >= 1 else "capital_insufficient"
    return {
        "ticker": symbol,
        "strategy": strategy,
        "side": side,
        "level_type": level["kind"],
        "level_price": _round(level_price),
        "signal_bar_date": str(pd.Timestamp(latest["date"]).date()),
        "entry_day": str(pd.Timestamp(latest["date"]).date()),
        "entry_price": _round(entry),
        "entry_atr_pct": params.entry_atr_pct,
        "stop_variant": stop_variant,
        "stop_price": _round(stop),
        "take_profit_price": _round(take_profit),
        "rr": params.rr,
        "atr_clean_14": _round(atr),
        "avg_volume_20": int(_num(latest.get("avg_volume_20")) or 0),
        "median_spread_pct_20": None,
        "volume_filter_pass": True,
        "spread_filter_pass": True,
        "corporate_action_filter_pass": True,
        "earnings_filter_pass": True,
        "position_size_shares": shares,
        "risk_per_trade_pct": params.risk_pct * 100,
        "score": score,
        "expected_R_multiple": params.rr,
        "liquidity_quality": min(1.0, (_num(latest.get("median_dollar_volume_20")) or 0.0) / 50_000_000),
        "status": status,
        "reasoning": reasoning,
    }


def _detect_signals(symbol: str, data: pd.DataFrame, levels: list[dict[str, Any]], params: ScreenerParams) -> list[dict[str, Any]]:
    if len(data) < 3:
        return []
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
        volume_ok = float(latest["volume"]) >= params.volume_mult * max(float(latest.get("median_volume_20") or 0.0), 1.0)

        if "LP1" in params.strategies and support:
            if latest["open"] >= level_price - tol and latest["low"] < level_price - params.delta_break * atr and latest["close"] > level_price + params.delta_close * atr and bar_range_ok:
                out.append(_make_signal(symbol=symbol, strategy="LP1", side="LONG", level=level, data=data, params=params, reasoning="LP1 long: support false break and close reclaimed the level."))
        if "LP1" in params.strategies and resistance:
            if latest["open"] <= level_price + tol and latest["high"] > level_price + params.delta_break * atr and latest["close"] < level_price - params.delta_close * atr and bar_range_ok:
                out.append(_make_signal(symbol=symbol, strategy="LP1", side="SHORT", level=level, data=data, params=params, reasoning="LP1 short: resistance false break and close rejected below the level."))

        if "LP2" in params.strategies and support:
            if prev["low"] < level_price - params.delta_break * atr and latest["close"] > level_price + params.delta_close * atr:
                out.append(_make_signal(symbol=symbol, strategy="LP2", side="LONG", level=level, data=data, params=params, reasoning="LP2 long: prior bar broke support and current bar reclaimed it."))
        if "LP2" in params.strategies and resistance:
            if prev["high"] > level_price + params.delta_break * atr and latest["close"] < level_price - params.delta_close * atr:
                out.append(_make_signal(symbol=symbol, strategy="LP2", side="SHORT", level=level, data=data, params=params, reasoning="LP2 short: prior bar broke resistance and current bar rejected it."))

        if "PRB1" in params.strategies and resistance:
            if latest["open"] <= level_price + tol and latest["high"] > level_price + params.delta_break * atr and latest["close"] > level_price + params.delta_close * atr and volume_ok:
                out.append(_make_signal(symbol=symbol, strategy="PRB1", side="LONG", level=level, data=data, params=params, reasoning="PRB1 long: resistance breakout closed above level with volume confirmation."))
        if "PRB1" in params.strategies and support:
            if latest["open"] >= level_price - tol and latest["low"] < level_price - params.delta_break * atr and latest["close"] < level_price - params.delta_close * atr and volume_ok:
                out.append(_make_signal(symbol=symbol, strategy="PRB1", side="SHORT", level=level, data=data, params=params, reasoning="PRB1 short: support breakdown closed below level with volume confirmation."))

        if "PRB2" in params.strategies and resistance:
            if prev["close"] > level_price and latest["low"] >= level_price - tol and latest["close"] > level_price:
                signal = _make_signal(symbol=symbol, strategy="PRB2", side="LONG", level=level, data=data, params=params, reasoning="PRB2 long: breakout held the resistance level on the second bar.")
                signal["stop_variant"] = "behind_day1"
                out.append(signal)
        if "PRB2" in params.strategies and support:
            if prev["close"] < level_price and latest["high"] <= level_price + tol and latest["close"] < level_price:
                signal = _make_signal(symbol=symbol, strategy="PRB2", side="SHORT", level=level, data=data, params=params, reasoning="PRB2 short: breakdown held the support level on the second bar.")
                signal["stop_variant"] = "behind_day1"
                out.append(signal)

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
    levels_by_symbol: dict[str, list[dict[str, Any]]] = {}
    reason_counts: Counter[str] = Counter()

    for symbol in symbols:
        frame = _add_metrics(_normalize_frame(bars_loader(symbol, params.timeframe)))
        if frame.empty or len(frame) < 40:
            rejected.append({"ticker": symbol, "reason": "insufficient_data"})
            reason_counts["insufficient_data"] += 1
            continue
        plan = watchlist.get(symbol, {}) if isinstance(watchlist, dict) else {}
        ok, filter_reasons, metrics = _passes_filters(frame, plan if isinstance(plan, dict) else {})
        levels = _pivot_levels(frame, k=params.pivot_k, min_touches=params.min_touches)
        levels_by_symbol[symbol] = levels[:8]
        if not levels:
            rejected.append({"ticker": symbol, "reason": "no_level"})
            reason_counts["no_level"] += 1
            continue
        if not ok:
            reason = filter_reasons[0]
            rejected.append({"ticker": symbol, "reason": reason, "reasons": filter_reasons})
            reason_counts.update(filter_reasons)
            continue
        found = _detect_signals(symbol, frame, levels, params)
        if found:
            signals.extend(found)
            continue
        latest_close = float(frame["close"].iloc[-1])
        latest_atr = max(float(frame["atr_clean_14"].iloc[-1]), 0.0001)
        nearest = min(levels, key=lambda level: abs(float(level["price"]) - latest_close) / latest_atr)
        near_misses.append(
            {
                "ticker": symbol,
                "reason": "broken_pattern",
                "nearest_level": _round(nearest["price"]),
                "level_type": nearest["kind"],
                "distance_atr": _round(abs(float(nearest["price"]) - latest_close) / latest_atr, 3),
                "levels_found": len(levels),
            }
        )
        reason_counts["broken_pattern"] += 1

    signals.sort(key=lambda item: (-float(item.get("score") or 0.0), -float(item.get("expected_R_multiple") or 0.0), -float(item.get("liquidity_quality") or 0.0), item.get("ticker", "")))
    return {
        "scan_timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "timeframe": params.timeframe,
        "universe_size": len(symbols),
        "signals_found": len(signals),
        "signals": signals,
        "rejected": rejected,
        "near_misses": sorted(near_misses, key=lambda item: float(item.get("distance_atr") or 999))[:50],
        "levels_by_symbol": levels_by_symbol,
        "reason_counts": dict(reason_counts.most_common()),
        "params": {
            "risk_pct": params.risk_pct,
            "equity": params.equity,
            "rr": params.rr,
            "score_min": params.score_min,
            "strategies": list(params.strategies),
            "side_filter": params.side_filter,
        },
    }
