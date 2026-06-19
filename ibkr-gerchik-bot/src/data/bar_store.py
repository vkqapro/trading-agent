"""Persist and load OHLCV bars so the dashboard works without a live TWS connection.

Bars are stored as CSV (no extra dependencies beyond pandas) under
``memory/bars/``. Each symbol/timeframe pair gets one file plus a shared
``index.json`` that records when each was last refreshed.

The save helpers are intentionally defensive: a failure to persist bars must
never interrupt the trading jobs that call them.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from src.config import LOGGER, MEMORY_DIR

BARS_DIR = MEMORY_DIR / "bars"
INDEX_PATH = BARS_DIR / "index.json"

_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def _ensure_dir() -> None:
    BARS_DIR.mkdir(parents=True, exist_ok=True)


def _safe_symbol(symbol: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in symbol.upper())


def bar_path(symbol: str, timeframe: str) -> Path:
    return BARS_DIR / f"{_safe_symbol(symbol)}__{timeframe}.csv"


def _read_index() -> Dict[str, Dict[str, object]]:
    if not INDEX_PATH.exists():
        return {}
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_index(index: Dict[str, Dict[str, object]]) -> None:
    try:
        INDEX_PATH.write_text(json.dumps(index, indent=2), encoding="utf-8")
    except OSError as exc:  # pragma: no cover - best effort
        LOGGER.warning("Failed to write bar index: %s", exc)


def save_bars(symbol: str, timeframe: str, bars: pd.DataFrame) -> Optional[Path]:
    """Persist a bars frame for ``symbol``/``timeframe``.

    ``timeframe`` is a free-form label such as ``"daily"`` or ``"intraday_5m"``.
    Returns the written path, or ``None`` on failure (errors are swallowed and
    logged so callers in the trading loop are never disrupted).
    """
    if bars is None or bars.empty:
        return None
    try:
        _ensure_dir()
        frame = bars.copy()
        available = [col for col in _COLUMNS if col in frame.columns]
        if "date" not in available:
            return None
        frame = frame[available]
        path = bar_path(symbol, timeframe)
        frame.to_csv(path, index=False)

        index = _read_index()
        index.setdefault(symbol.upper(), {})[timeframe] = {
            "path": path.name,
            "rows": int(len(frame)),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "last_bar": str(frame["date"].iloc[-1]),
        }
        _write_index(index)
        return path
    except Exception as exc:  # pragma: no cover - defensive, never break trading
        LOGGER.warning("Failed to persist %s %s bars: %s", symbol, timeframe, exc)
        return None


def load_bars(symbol: str, timeframe: str) -> pd.DataFrame:
    """Read persisted bars for ``symbol``/``timeframe`` (empty frame if missing)."""
    path = bar_path(symbol, timeframe)
    if not path.exists():
        return pd.DataFrame(columns=_COLUMNS)
    try:
        frame = pd.read_csv(path)
        if "date" in frame.columns:
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            frame = frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        return frame
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.warning("Failed to load %s %s bars: %s", symbol, timeframe, exc)
        return pd.DataFrame(columns=_COLUMNS)


def bar_metadata(symbol: str, timeframe: str) -> Dict[str, object]:
    """Return the index entry for a symbol/timeframe (empty dict if unknown)."""
    return _read_index().get(symbol.upper(), {}).get(timeframe, {})


def index_snapshot() -> Dict[str, Dict[str, object]]:
    """Return the whole bar index (symbol -> timeframe -> metadata)."""
    return _read_index()
