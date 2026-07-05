"""Crypto Gerchik-style analysis using the shared stock strategy logic."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd

from src.config import LOGGER
from src.crypto.bar_store import crypto_index_snapshot, load_crypto_bars, save_crypto_bars
from src.crypto.config import CRYPTO_SETTINGS, ensure_crypto_directories
from src.crypto.okx_client import OKXClient, OKX_BAR_BY_TIMEFRAME
from src.crypto.symbols import default_instrument_type, load_crypto_symbol_inputs, normalize_okx_instrument
from src.strategy.levels import detect_levels, optimize_trade_levels, calculate_atr

STATE_PATH = CRYPTO_SETTINGS.memory_dir / "state.json"
TIMEFRAMES = ("daily", "intraday_4h", "intraday_1h", "intraday_5m")


@contextmanager
def _quiet_shared_level_logs():
    """Keep crypto batch scans from flooding the shared stock bot log file."""
    previous_level = LOGGER.level
    LOGGER.setLevel(logging.WARNING)
    try:
        yield
    finally:
        LOGGER.setLevel(previous_level)


def configured_crypto_symbols() -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    seen: set[str] = set()
    for raw in load_crypto_symbol_inputs():
        inst_id = normalize_okx_instrument(raw)
        if not inst_id or inst_id in seen:
            continue
        seen.add(inst_id)
        rows.append({"raw": raw, "inst_id": inst_id, "inst_type": default_instrument_type(inst_id)})
    return rows


def validate_symbols(client: OKXClient | None = None) -> List[Dict[str, object]]:
    client = client or OKXClient()
    configured = configured_crypto_symbols()
    by_type: Dict[str, set[str]] = {}
    for row in configured:
        by_type.setdefault(row["inst_type"], set())
    available: Dict[str, set[str]] = {}
    for inst_type in by_type:
        available[inst_type] = {item.inst_id for item in client.instruments(inst_type)}
    result = []
    for row in configured:
        exists = row["inst_id"] in available.get(row["inst_type"], set())
        result.append({**row, "okx_available": exists})
    return result


def collect_crypto_bars(symbols: Iterable[str] | None = None, *, timeframes: Iterable[str] = TIMEFRAMES, validate: bool = False) -> Dict[str, object]:
    ensure_crypto_directories()
    client = OKXClient()
    if symbols is None:
        configured = configured_crypto_symbols()
        instruments = [row["inst_id"] for row in configured]
    else:
        instruments = [normalize_okx_instrument(symbol) for symbol in symbols if normalize_okx_instrument(symbol)]

    validation_map = {}
    if validate:
        validation_map = {row["inst_id"]: row for row in validate_symbols(client)}

    persisted = 0
    failed: List[Dict[str, str]] = []
    for inst_id in instruments:
        if validate and not validation_map.get(inst_id, {}).get("okx_available", False):
            failed.append({"symbol": inst_id, "reason": "not_available_on_okx"})
            continue
        for timeframe in timeframes:
            try:
                bars = client.candles(inst_id, timeframe)
                if save_crypto_bars(inst_id, timeframe, bars, replace=(timeframe == "daily")) is not None:
                    persisted += 1
                else:
                    failed.append({"symbol": inst_id, "timeframe": timeframe, "reason": "empty_bars"})
            except Exception as exc:
                LOGGER.warning("Crypto candle fetch failed %s %s: %s", inst_id, timeframe, exc)
                failed.append({"symbol": inst_id, "timeframe": timeframe, "reason": str(exc)})
    return {"symbols": len(instruments), "persisted": persisted, "failed": failed}


def analyze_symbol(inst_id: str) -> Dict[str, object]:
    daily = load_crypto_bars(inst_id, "daily")
    intraday = load_crypto_bars(inst_id, "intraday_5m")
    if daily.empty:
        return {"symbol": inst_id, "ready": False, "reason": "missing_daily_bars"}

    with _quiet_shared_level_logs():
        raw_levels = detect_levels(inst_id, daily, intraday)
        daily_atr = calculate_atr(daily)
        trade_levels = optimize_trade_levels(raw_levels, daily_atr)
    price = float(daily.iloc[-1]["close"])
    return {
        "symbol": inst_id,
        "ready": True,
        "price": round(price, 8),
        "daily_atr": round(float(daily_atr), 8),
        "bars": {tf: int(len(load_crypto_bars(inst_id, tf))) for tf in TIMEFRAMES},
        "raw_levels": [level.to_dict() for level in raw_levels],
        "trade_levels": [level.to_dict() for level in trade_levels],
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def run_crypto_analysis(symbols: Iterable[str] | None = None) -> Dict[str, object]:
    ensure_crypto_directories()
    existing = load_crypto_state()
    if symbols is None:
        instruments = [row["inst_id"] for row in configured_crypto_symbols()]
        watchlist = {}
        updated_instruments = set(instruments)
    else:
        instruments = [normalize_okx_instrument(symbol) for symbol in symbols if normalize_okx_instrument(symbol)]
        updated_instruments = set(instruments)
        watchlist = existing.get("watchlist", {}) if isinstance(existing.get("watchlist"), dict) else {}
    errors = []
    for inst_id in instruments:
        try:
            watchlist[inst_id] = analyze_symbol(inst_id)
        except Exception as exc:
            LOGGER.warning("Crypto analysis failed %s: %s", inst_id, exc)
            errors.append({"symbol": inst_id, "reason": str(exc)})
    if symbols is not None:
        existing_symbols = existing.get("symbols", []) if isinstance(existing.get("symbols"), list) else []
        instruments = sorted({str(symbol) for symbol in existing_symbols if symbol} | set(instruments))
        existing_errors = existing.get("errors", []) if isinstance(existing.get("errors"), list) else []
        errors = [
            row for row in existing_errors
            if not isinstance(row, dict) or row.get("symbol") not in updated_instruments
        ] + errors
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbols": instruments,
        "watchlist": watchlist,
        "errors": errors,
        "bar_index": crypto_index_snapshot(),
    }
    STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def load_crypto_state() -> Dict[str, object]:
    if not STATE_PATH.exists():
        return {"watchlist": {}, "symbols": [], "errors": []}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"watchlist": {}, "symbols": [], "errors": []}
    except (OSError, json.JSONDecodeError):
        return {"watchlist": {}, "symbols": [], "errors": []}
