"""Read-only access to persisted Trading Bot market data.

This module deliberately reads the same persisted bar store and watchlist
snapshot consumed by the dashboard. It never connects to IBKR or recalculates
levels.
"""
from __future__ import annotations

import math
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dashboard.data_access import load_watchlist
from src.config import SETTINGS
from src.data.bar_store import bar_metadata, index_snapshot, load_bars
from src.symbol_universe import normalize_stock_symbol

_MAX_CANDLES = max(1, int(os.getenv("TRADING_BOT_MCP_MAX_CANDLES", "500")))
_MAX_LEVELS = max(1, int(os.getenv("TRADING_BOT_MCP_MAX_LEVELS", "200")))
_ET = ZoneInfo("America/New_York")

TIMEFRAME_FILES = {
    "1D": "daily", "D": "daily", "daily": "daily",
    "1W": "weekly", "W": "weekly", "weekly": "weekly",
    "15M": "intraday_15m", "15m": "intraday_15m", "intraday_15m": "intraday_15m",
    "5M": "intraday_5m", "5m": "intraday_5m", "intraday_5m": "intraday_5m",
    "4H": "intraday_4h", "4h": "intraday_4h", "intraday_4h": "intraday_4h",
}

class DataError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _symbol(symbol: str) -> str:
    try:
        return normalize_stock_symbol(symbol)
    except (TypeError, ValueError) as exc:
        raise DataError("SYMBOL_NOT_FOUND", str(exc)) from exc


def _timeframe(value: str) -> tuple[str, str]:
    key = str(value or "1D").strip()
    file_key = TIMEFRAME_FILES.get(key) or TIMEFRAME_FILES.get(key.upper())
    if not file_key:
        raise DataError("INVALID_TIMEFRAME", f"Unsupported timeframe: {value}")
    return key.upper(), file_key


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=_ET)
        return stamp.isoformat()
    except (TypeError, ValueError):
        return str(value)


def _json_number(value: Any) -> float | int | None:
    try:
        number = float(value)
        if not math.isfinite(number):
            return None
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return None


def _frame_closed(value: Any, timeframe: str) -> bool:
    try:
        stamp = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
        if not isinstance(stamp, datetime):
            stamp = datetime.fromisoformat(str(value))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=_ET)
        stamp = stamp.astimezone(_ET)
        # Persisted daily rows for today represent the still-forming session.
        if timeframe in {"1D", "D", "DAILY"}:
            return stamp.date() < datetime.now(_ET).date()
        # Intraday data is stored as completed bars by the collector. A bar
        # stamped in the future is not considered closed.
        return stamp <= datetime.now(_ET)
    except (TypeError, ValueError, AttributeError):
        return True


def _bars(symbol: str, timeframe: str):
    normalized = _symbol(symbol)
    public_tf, file_tf = _timeframe(timeframe)
    frame = load_bars(normalized, file_tf)
    if frame.empty:
        raise DataError("NO_CANDLE_DATA", f"No stored candle data for {normalized} {public_tf}")
    return normalized, public_tf, file_tf, frame


def _plan(symbol: str) -> dict[str, Any]:
    plans = load_watchlist()
    plan = plans.get(symbol)
    if not isinstance(plan, dict):
        return {}
    return plan


def _price_and_timestamp(symbol: str) -> tuple[float | None, str | None]:
    for tf in ("intraday_15m", "intraday_5m", "daily"):
        frame = load_bars(symbol, tf)
        if not frame.empty:
            row = frame.iloc[-1]
            return _json_number(row.get("close")), _iso(row.get("date"))
    return None, None


def _freshness(timestamp: str | None) -> dict[str, Any]:
    if not timestamp:
        return {"source_timestamp": None, "data_age_seconds": None, "freshness": "unknown"}
    try:
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_ET)
        age = max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
        return {"source_timestamp": timestamp, "data_age_seconds": round(age, 3), "freshness": "current" if age < 86400 else "stale"}
    except ValueError:
        return {"source_timestamp": timestamp, "data_age_seconds": None, "freshness": "unknown"}


def _latest_closed_daily(symbol: str) -> tuple[float | None, str | None]:
    frame = load_bars(symbol, "daily")
    if frame.empty:
        return None, None
    for _, row in frame.iloc[::-1].iterrows():
        if _frame_closed(row.get("date"), "1D"):
            return _json_number(row.get("close")), _iso(row.get("date"))
    return None, None


def _level_provenance(plan: dict[str, Any]) -> dict[str, Any]:
    """Return persisted level-generation metadata without inventing timestamps."""
    spacing = plan.get("level_spacing")
    spacing = spacing if isinstance(spacing, dict) else {}
    reference = _json_number(spacing.get("current_price"))
    # Premarket stores the price used alongside the level-spacing calculation,
    # but does not persist a per-symbol generation timestamp. Keep that
    # limitation explicit rather than substituting a file mtime.
    return {
        "level_reference_price": reference,
        "level_reference_price_timestamp": None,
        "levels_as_of": None,
        "levels_age_seconds": None,
    }


def get_symbol_snapshot(symbol: str) -> dict[str, Any]:
    normalized = _symbol(symbol)
    plan = _plan(normalized)
    current_price, current_timestamp = _price_and_timestamp(normalized)
    latest_closed_price, latest_closed_timestamp = _latest_closed_daily(normalized)
    coverage = get_symbol_data_coverage(normalized, "1D", _internal=True)
    if current_price is None and not plan and not coverage["timeframes"]:
        raise DataError("SYMBOL_NOT_FOUND", f"No stored data for {normalized}")
    current_freshness = _freshness(current_timestamp)
    return {
        "symbol": normalized,
        "security_type": plan.get("security_type", SETTINGS.symbol_security_type(normalized)),
        # Backward-compatible alias: last_price means current_price only.
        "last_price": current_price,
        "current_price": current_price,
        "current_price_timestamp": current_timestamp,
        "latest_closed_price": latest_closed_price,
        "latest_closed_bar_timestamp": latest_closed_timestamp,
        "atr": {"daily": _json_number(plan.get("daily_atr")), "technical": _json_number(plan.get("technical_atr"))},
        "candles": {"earliest_available": coverage.get("earliest_bar"), "latest_available": coverage.get("latest_bar"), "latest_closed_bar": coverage.get("latest_closed_bar"), "available_count": coverage.get("bars_available", 0)},
        "levels": {"raw_count": len(plan.get("raw_levels") or []), "consolidated_count": len(plan.get("levels") or [])},
        "source_timestamp": current_timestamp,
        "data_age_seconds": current_freshness.get("data_age_seconds"),
        "current_price_age_seconds": current_freshness.get("data_age_seconds"),
        "freshness": current_freshness.get("freshness"),
        **_level_provenance(plan),
    }


def get_symbol_data_coverage(symbol: str, timeframe: str | None = None, _internal: bool = False) -> dict[str, Any]:
    normalized = _symbol(symbol)
    timeframes: list[str] = []
    requested = None
    if timeframe:
        requested, file_tf = _timeframe(timeframe)
        candidates = [(requested, file_tf)]
    else:
        candidates = [("1D", "daily"), ("1W", "weekly"), ("15M", "intraday_15m"), ("5M", "intraday_5m"), ("4H", "intraday_4h")]
    result: dict[str, Any] = {"symbol": normalized}
    for public_tf, file_tf in candidates:
        frame = load_bars(normalized, file_tf)
        if frame.empty:
            continue
        closed = [frame.iloc[i] for i in range(len(frame)) if _frame_closed(frame.iloc[i]["date"], public_tf)]
        timeframes.append(public_tf)
        if requested:
            result.update({"timeframe": public_tf, "earliest_bar": _iso(frame.iloc[0]["date"]), "latest_bar": _iso(frame.iloc[-1]["date"]), "latest_closed_bar": _iso(closed[-1]["date"]) if closed else None, "bars_available": len(frame)})
    result["timeframes"] = timeframes
    if requested and not timeframes:
        raise DataError("NO_CANDLE_DATA", f"No stored candle data for {normalized} {requested}")
    if _internal and not requested:
        return result
    return result


def _candle(row: Any, timeframe: str) -> dict[str, Any]:
    return {"timestamp": _iso(row.get("date")), "open": _json_number(row.get("open")), "high": _json_number(row.get("high")), "low": _json_number(row.get("low")), "close": _json_number(row.get("close")), "volume": _json_number(row.get("volume", 0)), "closed": _frame_closed(row.get("date"), timeframe)}


def get_symbol_history(symbol: str, timeframe: str = "1D", lookback_days: int | None = None, limit: int | None = None, start: str | None = None, end: str | None = None, include_incomplete: bool = False) -> dict[str, Any]:
    normalized, public_tf, _, frame = _bars(symbol, timeframe)
    if limit is not None and int(limit) > _MAX_CANDLES:
        raise DataError("REQUEST_LIMIT_EXCEEDED", f"Maximum candles is {_MAX_CANDLES}")
    if lookback_days is not None and int(lookback_days) < 0:
        raise DataError("INVALID_TIMEFRAME", "lookback_days must be non-negative")
    if start:
        frame = frame[frame["date"] >= start]
    if end:
        frame = frame[frame["date"] <= end]
    if lookback_days is not None:
        frame = frame.tail(int(lookback_days))
    if not include_incomplete:
        frame = frame[[ _frame_closed(value, public_tf) for value in frame["date"] ]]
    if limit is not None:
        frame = frame.tail(int(limit))
    if len(frame) > _MAX_CANDLES:
        raise DataError("REQUEST_LIMIT_EXCEEDED", f"Request returns {len(frame)} candles; maximum is {_MAX_CANDLES}")
    rows = [_candle(row, public_tf) for _, row in frame.iterrows()]
    latest_closed = next((item for item in reversed(rows) if item["closed"]), None)
    return {
        "symbol": normalized,
        "timeframe": public_tf,
        "requested_window": {"lookback_days": lookback_days, "limit": limit, "start": start, "end": end},
        "latest_closed_bar": latest_closed["timestamp"] if latest_closed else None,
        "latest_closed_price": latest_closed["close"] if latest_closed else None,
        "latest_closed_bar_timestamp": latest_closed["timestamp"] if latest_closed else None,
        "candles": rows,
    }


def _level_output(level: dict[str, Any], current_price: float | None, plan: dict[str, Any]) -> dict[str, Any]:
    output = dict(level)
    for key in ("price", "zone_low", "zone_high", "touches", "false_breakouts", "strength"):
        if key in output:
            output[key] = _json_number(output[key]) if key not in {"type"} else output[key]
    if current_price is not None and output.get("price") is not None:
        distance = float(output["price"]) - current_price
        output["distance_from_current_price"] = {"dollars": round(distance, 8), "percent": round(distance / current_price * 100, 8) if current_price else None, "daily_atr": round(distance / float(plan["daily_atr"]), 8) if plan.get("daily_atr") else None, "technical_atr": round(distance / float(plan["technical_atr"]), 8) if plan.get("technical_atr") else None}
    return output


def get_symbol_levels(symbol: str, level_set: str = "consolidated", min_strength: float | None = None, level_type: str | None = None, side: str | None = None, limit: int | None = None) -> dict[str, Any]:
    normalized = _symbol(symbol)
    if level_set not in {"consolidated", "raw"}:
        raise DataError("INVALID_LEVEL_SET", f"Unsupported level set: {level_set}")
    if limit is not None and int(limit) > _MAX_LEVELS:
        raise DataError("REQUEST_LIMIT_EXCEEDED", f"Maximum levels is {_MAX_LEVELS}")
    plan = _plan(normalized)
    levels = plan.get("levels" if level_set == "consolidated" else "raw_levels") or []
    if not isinstance(levels, list):
        raise DataError("NO_LEVEL_DATA", f"No {level_set} levels for {normalized}")
    current_price, current_timestamp = _price_and_timestamp(normalized)
    provenance = _level_provenance(plan)
    if side in {"above_price", "below_price"} and current_price is None:
        raise DataError("DATA_SOURCE_UNAVAILABLE", "Current price is required for side filtering")
    filtered = []
    for level in levels:
        if not isinstance(level, dict):
            continue
        if min_strength is not None and (level.get("strength") is None or float(level["strength"]) < float(min_strength)):
            continue
        if level_type and str(level.get("type", "")).lower() != level_type.lower():
            continue
        level_price = level.get("price")
        if side in {"above_price", "below_price"} and level_price is not None:
            if side == "above_price" and float(level_price) <= current_price: continue
            if side == "below_price" and float(level_price) >= current_price: continue
        filtered.append(_level_output(level, current_price, plan))
    if not filtered and not levels:
        raise DataError("NO_LEVEL_DATA", f"No {level_set} levels for {normalized}")
    if limit is not None:
        filtered = filtered[:int(limit)]
    if len(filtered) > _MAX_LEVELS:
        raise DataError("REQUEST_LIMIT_EXCEEDED", f"Maximum levels is {_MAX_LEVELS}")
    current_freshness = _freshness(current_timestamp)
    return {
        "symbol": normalized,
        # Backward-compatible alias: last_price means current_price only.
        "last_price": current_price,
        "current_price": current_price,
        "current_price_timestamp": current_timestamp,
        **provenance,
        "current_price_age_seconds": current_freshness.get("data_age_seconds"),
        "level_set": level_set,
        "levels": filtered,
    }


def get_level_context(symbol: str, level_price: float) -> dict[str, Any]:
    normalized = _symbol(symbol)
    try: target = float(level_price)
    except (TypeError, ValueError) as exc: raise DataError("LEVEL_NOT_FOUND", "level_price must be numeric") from exc
    consolidated = get_symbol_levels(normalized, "consolidated")
    raw = get_symbol_levels(normalized, "raw")
    levels = (consolidated.get("levels") or []) + (raw.get("levels") or [])
    matches = [item for item in levels if item.get("price") is not None and abs(float(item["price"]) - target) <= 0.005]
    if not matches:
        raise DataError("LEVEL_NOT_FOUND", f"No level matching {target:g} for {normalized}; tolerance is 0.005")
    exact = min(matches, key=lambda item: abs(float(item["price"]) - target))
    current_price = consolidated.get("current_price")
    reference_price = consolidated.get("level_reference_price")
    distance = None
    reference_distance = None
    if current_price is not None:
        distance = round(float(exact["price"]) - float(current_price), 8)
    if reference_price is not None:
        reference_distance = round(float(exact["price"]) - float(reference_price), 8)
    return {
        "symbol": normalized,
        "current_price": current_price,
        "current_price_timestamp": consolidated.get("current_price_timestamp"),
        "level_reference_price": reference_price,
        "level_reference_price_timestamp": consolidated.get("level_reference_price_timestamp"),
        "levels_as_of": consolidated.get("levels_as_of"),
        "distance_from_current_price": distance,
        "distance_from_level_reference_price": reference_distance,
        "level": exact,
        "match_tolerance": 0.005,
    }


def supported_timeframes() -> list[str]:
    available = index_snapshot()
    result = []
    for symbol_data in available.values():
        for file_tf in symbol_data:
            for public, mapped in TIMEFRAME_FILES.items():
                if mapped == file_tf and public.upper() in {"1D", "1W", "15M", "5M", "4H"}:
                    result.append(public.upper())
    return sorted(set(result))
