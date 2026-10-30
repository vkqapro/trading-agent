"""Read-only adapter to the Trading Bot persisted candle service."""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.data.bar_store import index_snapshot, load_bars  # noqa: E402
from src.data.chart_history import build_partial_daily_frame  # noqa: E402
from src.mcp.trading_data_service import DataError, get_symbol_history  # noqa: E402
from src.symbol_universe import load_stock_symbols, stock_symbols_file  # noqa: E402

DATA_SOURCE = "Trading Bot persisted bar store (memory/bars via src.mcp.trading_data_service)"


@dataclass(frozen=True)
class LoadedBars:
    symbol: str
    timeframe: str
    frame: pd.DataFrame
    first_bar_timestamp: str
    last_bar_timestamp: str
    last_bar_closed: bool
    bars_used: int
    input_hash: str
    data_source: str = DATA_SOURCE


def configured_symbols() -> list[str]:
    return load_stock_symbols()


def universe_source() -> str:
    return str(stock_symbols_file())


def available_daily_symbols(symbols: Iterable[str]) -> list[str]:
    index = index_snapshot()
    return [symbol for symbol in symbols if isinstance(index.get(symbol, {}).get("daily"), dict)]


def _hash_candles(candles: list[dict[str, Any]]) -> str:
    canonical = json.dumps(candles, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_partial_daily_bars(symbol: str, *, as_of: str | None = None, limit: int = 500) -> LoadedBars:
    """Load closed daily history plus the current 5m-derived partial session."""
    daily_frame = load_bars(symbol, "daily")
    intraday_frame = load_bars(symbol, "intraday_5m")
    if as_of and not daily_frame.empty:
        cutoff = pd.Timestamp(as_of)
        cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
        daily_frame = daily_frame.loc[pd.to_datetime(daily_frame["date"], errors="coerce", utc=True) <= cutoff]
    partial = build_partial_daily_frame(daily_frame, intraday_frame, as_of=as_of)
    if partial.empty:
        raise DataError("NO_CLOSED_CANDLE_DATA", f"No daily candle data for {symbol}")
    partial["date"] = pd.to_datetime(partial["date"], errors="coerce")
    partial = partial.dropna(subset=["date", "open", "high", "low", "close"])
    frame = partial.set_index("date")[["open", "high", "low", "close", "volume"]]
    frame.columns = ["Open", "High", "Low", "Close", "Volume"]
    frame.index.name = "Date"
    serialized = [
        {key: (value.isoformat() if isinstance(value, pd.Timestamp) else value) for key, value in dict(row).items()}
        for row in partial.to_dict("records")
    ]
    last = partial.iloc[-1]
    last_date = str(last["date"])[:10]
    cutoff = pd.Timestamp(as_of) if as_of else None
    if cutoff is not None:
        cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
    def included_current(item: dict[str, Any]) -> bool:
        if str(item.get("timestamp", ""))[:10] != last_date:
            return False
        if cutoff is None:
            return True
        observed = pd.Timestamp(item.get("timestamp"))
        observed = observed.tz_localize("UTC") if observed.tzinfo is None else observed.tz_convert("UTC")
        return observed <= cutoff
    has_intraday_current = any(included_current({"timestamp": value}) for value in intraday_frame["date"].tolist()) if not intraday_frame.empty else False
    return LoadedBars(
        symbol=str(symbol).upper(), timeframe="1D", frame=frame,
        first_bar_timestamp=str(partial.iloc[0]["date"]), last_bar_timestamp=str(last["date"]),
        last_bar_closed=not has_intraday_current, bars_used=len(frame), input_hash=_hash_candles(serialized),
    )


def load_closed_daily_bars(symbol: str, *, as_of: str | None = None, limit: int = 500) -> LoadedBars:
    history = get_symbol_history(symbol, "1D", limit=limit, end=as_of, include_incomplete=False)
    candles = history.get("candles") or []
    if not candles:
        raise DataError("NO_CLOSED_CANDLE_DATA", f"No closed daily candle data for {symbol}")
    if any(not bool(item.get("closed")) for item in candles):
        raise DataError("INCOMPLETE_BAR_INCLUDED", f"Incomplete daily bar reached scanner for {symbol}")
    frame = pd.DataFrame(candles)
    # Persisted US/Eastern ISO strings cross DST offsets; normalize through UTC
    # so pandas never sees a mixed-offset DatetimeIndex.
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True).dt.tz_convert("America/New_York")
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close"])
    frame = frame.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
    frame.columns = ["Open", "High", "Low", "Close", "Volume"]
    frame.index.name = "Date"
    if frame.empty:
        raise DataError("NO_CLOSED_CANDLE_DATA", f"No valid closed daily candles for {symbol}")
    first = str(candles[0]["timestamp"])
    last = str(candles[-1]["timestamp"])
    return LoadedBars(
        symbol=str(history["symbol"]),
        timeframe="1D",
        frame=frame,
        first_bar_timestamp=first,
        last_bar_timestamp=last,
        last_bar_closed=True,
        bars_used=len(frame),
        input_hash=_hash_candles(candles),
    )
