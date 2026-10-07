"""Canonical Web Screener adapter for LP/PRB production scans."""
from __future__ import annotations

import math
from pathlib import Path
import sys
from typing import Any, Mapping

import numpy as np
import pandas as pd

_ENGINE_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (_ENGINE_ROOT, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from dashboard_react.market_screener import ScreenerParams, _add_metrics, _detect_signals, _passes_filters, _pivot_levels  # noqa: E402
from strategies import market_screener_lp_prb_strategy_2 as authoritative  # noqa: E402
from ..contracts import ScanStatus, StrategyScanResult  # noqa: E402
from ..explainability import ANNOTATION_LABELS, pattern_definition  # noqa: E402

_STRATEGIES = {"lp1": "LP1", "lp2": "LP2", "prb1": "PRB1", "prb2": "PRB2"}
_MINIMUM_BARS = 23


def _value(value: Any) -> Any:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return value


def _frame_for_web(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    if isinstance(data.index, pd.DatetimeIndex):
        data = data.reset_index().rename(columns={data.index.name or "index": "date"})
    data = data.rename(columns={column: column.lower() for column in data.columns})
    return data[["date", "open", "high", "low", "close", "volume"]]


def _provenance(frame: pd.DataFrame, supplied: Mapping[str, Any] | None) -> dict[str, Any]:
    supplied = dict(supplied or {})
    return {"first_bar_timestamp": supplied.get("first_bar_timestamp") or str(frame.iloc[0]["date"]), "last_bar_timestamp": supplied.get("last_bar_timestamp") or str(frame.iloc[-1]["date"]), "last_bar_closed": bool(supplied.get("last_bar_closed", False)), "bars_used": int(supplied.get("bars_used", len(frame))), "data_source": supplied.get("data_source", "canonical partial daily frame"), "input_hash": supplied.get("input_hash")}


def _canonical_result(*, symbol: str, strategy: str, run_id: str, strategy_version: str, as_of: str | None, frame: pd.DataFrame, signal: Mapping[str, Any] | None, provenance: Mapping[str, Any] | None) -> StrategyScanResult:
    name = _STRATEGIES[strategy]
    data = _provenance(frame, provenance)
    last = frame.iloc[-1]
    if signal is None:
        return StrategyScanResult(
            run_id=run_id, symbol=symbol, strategy_id=strategy, strategy_version=strategy_version,
            direction="LONG", timeframe="1D", as_of=as_of or data["last_bar_timestamp"],
            status=ScanStatus.NO_SIGNAL, matched=False,
            signal={"pattern": name, "signal_bar": None, "signal_bar_date": None, "signal_bar_index": None, "triggered": False},
            market={"close": _value(last.get("close")), "atr": _value(last.get("atr_clean_14"))},
            level={"price": None, "level_type": None, "zone_low": None, "zone_high": None},
            trade_plan={"entry": None, "stop": None, "target": None, "rr": None, "reward_risk": None},
            quality={"score": None}, evidence={"canonical_evaluator": "dashboard_react.market_screener._detect_signals"},
            pattern_definition=pattern_definition(strategy), rejection_evidence=(), reason_codes=("NO_SIGNAL",), data=data,
        )
    entry, stop, target = (_value(signal.get(k)) for k in ("entry_price", "stop_price", "take_profit_price"))
    status = ScanStatus.ENTRY_SIGNAL if signal.get("status") == "READY" else ScanStatus.REJECTED
    return StrategyScanResult(
        run_id=run_id, symbol=symbol, strategy_id=strategy, strategy_version=strategy_version,
        direction="LONG", timeframe="1D", as_of=as_of or data["last_bar_timestamp"],
        status=status, matched=status is ScanStatus.ENTRY_SIGNAL,
        signal={"pattern": name, "signal_bar": signal.get("signal_bar_date"), "signal_bar_date": signal.get("signal_bar_date"), "signal_bar_index": -2, "triggered": bool(signal.get("entry_triggered"))},
        market={"close": _value(last.get("close")), "atr": _value(signal.get("atr_clean_14"))},
        level={"price": _value(signal.get("level_price")), "level_type": signal.get("level_type"), "zone_low": None, "zone_high": None},
        trade_plan={"entry": entry, "stop": stop, "target": target, "rr": _value(signal.get("rr")), "reward_risk": _value(signal.get("rr"))},
        quality={"score": _value(signal.get("score"))}, evidence={"canonical_evaluator": "dashboard_react.market_screener._detect_signals", "entry_day_status": signal.get("entry_day_status")},
        pattern_definition=pattern_definition(strategy), pattern_bars={f"bar_{i}": {"date": d} for i, d in enumerate(signal.get("pattern_bar_dates") or [], 1)},
        trigger_evidence={key: signal.get(key) for key in ("entry_triggered", "entry_gap_atr", "entry_chase_atr", "volume_filter_pass")},
        rejection_evidence=(), reason_codes=(), data=data,
    )


def evaluate_symbol_canonical(symbol: str, strategy_id: str, frames: Mapping[str, pd.DataFrame] | pd.DataFrame, direction: str = "LONG", as_of: str | None = None, params: Mapping[str, Any] | None = None, *, run_id: str = "unassigned", strategy_version: str = "market-screener-lp-prb-strategy-2", provenance: Mapping[str, Any] | None = None) -> StrategyScanResult:
    # Historical closed-only artifacts retain the legacy explainability contract;
    # live/current snapshots always take the canonical Web path below.
    if provenance and bool(provenance.get("last_bar_closed")):
        return evaluate_symbol(symbol, strategy_id, frames, direction, as_of, params, run_id=run_id, strategy_version=strategy_version, provenance=provenance)
    key = str(strategy_id).lower()
    if key not in _STRATEGIES or direction.upper() != "LONG":
        raise ValueError(f"Unsupported LP/PRB strategy or direction: {strategy_id}/{direction}")
    data = _frame_for_web(frames.get("1D") if isinstance(frames, Mapping) else frames).sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    if as_of:
        cutoff = pd.Timestamp(as_of)
        dates = pd.to_datetime(data["date"], errors="coerce")
        if cutoff.tzinfo is not None and getattr(dates.dt, "tz", None) is None:
            cutoff = cutoff.tz_localize(None)
        data = data.loc[dates <= cutoff].reset_index(drop=True)
    if len(data) < _MINIMUM_BARS:
        return _canonical_result(symbol=symbol, strategy=key, run_id=run_id, strategy_version=strategy_version, as_of=as_of, frame=data, signal=None, provenance=provenance)
    values = dict(params or {})
    screener_params = ScreenerParams(strategies=(_STRATEGIES[key],), side_filter="LONG", **{k: v for k, v in values.items() if k in ScreenerParams.__dataclass_fields__ and k not in {"strategies", "side_filter", "anchor_date"}})
    prepared = _add_metrics(data)
    filters_ok, _, metrics = _passes_filters(prepared, {}, screener_params)
    levels = _pivot_levels(prepared, k=screener_params.pivot_k, min_touches=screener_params.min_touches, tolerance_atr=screener_params.level_tolerance_atr)
    partial = bool(as_of and str(pd.Timestamp(prepared.iloc[-1]["date"]).date()) == str(pd.Timestamp(as_of).date()))
    signals = _detect_signals(symbol, prepared, levels, screener_params, metrics=metrics, entry_day_partial=partial) if filters_ok else []
    signal = next((item for item in signals if item.get("strategy") == _STRATEGIES[key] and item.get("side") == "LONG"), None)
    return _canonical_result(symbol=symbol, strategy=key, run_id=run_id, strategy_version=strategy_version, as_of=as_of, frame=prepared, signal=signal, provenance=provenance)


def _pattern_bars(frame: pd.DataFrame, row: pd.Series) -> dict[str, Any]:
    raw = row.get("pattern_bar_indices")
    if not isinstance(raw, Mapping): return {}
    bars = {}
    for key, meta in raw.items():
        if not isinstance(meta, Mapping) or meta.get("index") is None: continue
        index = int(meta["index"])
        if 0 <= index < len(frame):
            candle = frame.iloc[index]
            bars[str(key)] = {"date": pd.Timestamp(frame.index[index]).date().isoformat(), "timestamp": pd.Timestamp(frame.index[index]).isoformat(), "index": index, "role": str(meta.get("role") or "pattern_bar"), "open": _value(candle.get("Open")), "high": _value(candle.get("High")), "low": _value(candle.get("Low")), "close": _value(candle.get("Close")), "volume": _value(candle.get("Volume"))}
    return bars


def _chart_annotations(pattern_bars: Mapping[str, Any], *, level: Any, entry: Any, stop: Any, target: Any) -> tuple[dict[str, Any], ...]:
    annotations = []
    for key, bar in pattern_bars.items():
        role = str(bar.get("role") or "pattern_bar")
        annotations.append({"date": bar.get("date"), "timestamp": bar.get("timestamp"), "index": bar.get("index"), "bar_key": key, "role": role, "label": ANNOTATION_LABELS.get(role, role.replace("_", " ").title()), "type": "bar_marker"})
    for label, price, kind in (("Level", level, "horizontal_level"), ("Entry", entry, "trade_line"), ("Stop", stop, "trade_line"), ("Target", target, "trade_line")):
        if price is not None: annotations.append({"price": price, "label": label, "type": kind})
    return tuple(annotations)


def _legacy_result(symbol: str, strategy_id: str, frame: pd.DataFrame, direction: str, as_of: str | None, params: Mapping[str, Any] | None, run_id: str, strategy_version: str, provenance: Mapping[str, Any] | None) -> StrategyScanResult:
    key = str(strategy_id).lower()
    if direction.upper() != "LONG": raise ValueError("Strategy Scanner v0.2 supports LONG only")
    frame = frame.sort_index()
    if as_of:
        cutoff = pd.Timestamp(as_of)
        if frame.index.tz is not None and cutoff.tzinfo is None: cutoff = cutoff.tz_localize(frame.index.tz)
        elif frame.index.tz is None and cutoff.tzinfo is not None: cutoff = cutoff.tz_localize(None)
        frame = frame.loc[frame.index <= cutoff]
    data = _provenance(frame.reset_index().rename(columns={frame.index.name or "index": "date"}), provenance)
    if len(frame) < _MINIMUM_BARS:
        return StrategyScanResult(run_id, symbol, key, strategy_version, "LONG", "1D", data["last_bar_timestamp"], ScanStatus.INSUFFICIENT_DATA, False, {"pattern": _STRATEGIES[key], "signal_bar": None, "signal_bar_date": None, "signal_bar_index": None, "triggered": False}, {"close": _value(frame.iloc[-1].get("Close")), "atr": None}, {"price": None, "level_type": None, "zone_low": None, "zone_high": None}, {"entry": None, "stop": None, "target": None, "rr": None, "reward_risk": None}, {"score": None}, {}, pattern_definition(key), {}, {}, ("insufficient_bars",), (), ("MINIMUM_BARS_NOT_MET",), (), data, (f"requires at least {_MINIMUM_BARS} bars; received {len(frame)}",))
    overrides = dict(params or {})
    p = authoritative.BASELINE
    unknown = set(overrides) - set(authoritative.Params.__dataclass_fields__)
    if unknown: raise ValueError(f"Unknown strategy parameters: {sorted(unknown)}")
    from dataclasses import replace
    p = replace(p, **overrides, side="Long Only", lp1=key == "lp1", lp2=key == "lp2", prb1=key == "prb1", prb2=key == "prb2")
    prepared = authoritative.prepare(frame, p); row = prepared.iloc[-1]
    candidate = bool(_value(row.get("candidate")) or False); matched = bool(_value(row.get("signal")) or False)
    rejections = tuple(part for part in str(row.get("reject") or "").split(",") if part)
    status = ScanStatus.ENTRY_SIGNAL if matched else (ScanStatus.REJECTED if candidate else ScanStatus.NO_SIGNAL)
    entry, stop, target = (_value(row.get(name)) for name in ("planned_entry", "stop", "target"))
    rr = abs(target - entry) / abs(entry - stop) if None not in (entry, stop, target) and entry != stop else None
    signal_date = _value(row.get("signal_date")); pattern_bars = _pattern_bars(frame, row)
    trig = row.get("trigger_evidence"); rej = row.get("rejection_evidence")
    trigger = dict(trig) if isinstance(trig, Mapping) else {}; rejection = tuple(str(x) for x in rej) if isinstance(rej, (list, tuple)) else ()
    evidence = {name: _value(row.get(name)) for name in ("candidate", "side", "triggered", "gap_atr", "chase_atr", "overextended", "regime_ok", "rsi_ok", "qty", "ema50", "ema200", "rsi")}
    evidence["authoritative_function"] = "market_screener_lp_prb_strategy_2.prepare"; evidence["strategy_params"] = {name: _value(getattr(p, name)) for name in authoritative.Params.__dataclass_fields__}; evidence["explainability_schema_version"] = "1.0"
    annotations = _chart_annotations(pattern_bars, level=_value(row.get("level")), entry=entry, stop=stop, target=target)
    return StrategyScanResult(run_id, symbol, key, strategy_version, "LONG", "1D", data["last_bar_timestamp"], status, matched, {"pattern": _value(row.get("pattern")) or _STRATEGIES[key], "signal_bar": signal_date, "signal_bar_date": signal_date, "signal_bar_index": _value(row.get("signal_bar_index")), "triggered": bool(_value(row.get("triggered")) or False)}, {"close": _value(row.get("Close")), "atr": _value(row.get("atr")), "ema50": _value(row.get("ema50")), "ema200": _value(row.get("ema200")), "rsi": _value(row.get("rsi"))}, {"price": _value(row.get("level")), "level_type": _value(row.get("level_type")), "zone_low": None, "zone_high": None}, {"entry": entry, "stop": stop, "target": target, "rr": rr, "reward_risk": rr, "quantity": _value(row.get("qty"))}, {"score": _value(row.get("score"))}, evidence, pattern_definition(key), pattern_bars, trigger, rejection, annotations, tuple(x.upper() for x in rejections), rejections, data, ())


def evaluate_symbol(symbol: str, strategy_id: str, frames: Mapping[str, pd.DataFrame] | pd.DataFrame, direction: str = "LONG", as_of: str | None = None, params: Mapping[str, Any] | None = None, *, run_id: str = "unassigned", strategy_version: str = "market-screener-lp-prb-strategy-2", provenance: Mapping[str, Any] | None = None) -> StrategyScanResult:
    return _legacy_result(symbol, strategy_id, frames.get("1D") if isinstance(frames, Mapping) else frames, direction, as_of, params, run_id, strategy_version, provenance)
