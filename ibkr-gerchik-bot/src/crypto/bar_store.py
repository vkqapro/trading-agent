"""Crypto OHLCV CSV storage under memory/crypto/bars."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional
from uuid import uuid4

import pandas as pd

from src.config import LOGGER
from src.crypto.config import CRYPTO_SETTINGS, ensure_crypto_directories

_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
BARS_DIR = CRYPTO_SETTINGS.memory_dir / "bars"
INDEX_PATH = BARS_DIR / "index.json"


def _safe_symbol(symbol: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in symbol.upper())


def crypto_bar_path(symbol: str, timeframe: str) -> Path:
    return BARS_DIR / f"{_safe_symbol(symbol)}__{timeframe}.csv"


def _read_index() -> Dict[str, Dict[str, object]]:
    if not INDEX_PATH.exists():
        return {}
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_index(index: Dict[str, Dict[str, object]]) -> None:
    ensure_crypto_directories()
    temp = INDEX_PATH.with_name(f"{INDEX_PATH.name}.{os.getpid()}.{uuid4().hex}.tmp")
    temp.write_text(json.dumps(index, indent=2), encoding="utf-8")
    os.replace(temp, INDEX_PATH)


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty or "date" not in frame.columns:
        return pd.DataFrame(columns=_COLUMNS)
    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce", utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        data[column] = pd.to_numeric(data.get(column), errors="coerce")
    data = data.dropna(subset=["date", "open", "high", "low", "close"])
    data["volume"] = data["volume"].fillna(0.0)
    return data[_COLUMNS].sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def save_crypto_bars(symbol: str, timeframe: str, bars: pd.DataFrame, *, replace: bool = False) -> Optional[Path]:
    if bars is None or bars.empty:
        return None
    try:
        ensure_crypto_directories()
        incoming = _normalize_frame(bars)
        if incoming.empty:
            return None
        path = crypto_bar_path(symbol, timeframe)
        existing = (
            pd.DataFrame(columns=_COLUMNS)
            if replace
            else load_crypto_bars(symbol, timeframe) if path.exists() else pd.DataFrame(columns=_COLUMNS)
        )
        merged = incoming if existing.empty else _normalize_frame(pd.concat([existing, incoming], ignore_index=True))
        temp = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        merged.to_csv(temp, index=False)
        os.replace(temp, path)
        index = _read_index()
        index.setdefault(symbol.upper(), {})[timeframe] = {
            "path": path.name,
            "rows": int(len(merged)),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "last_bar": str(merged["date"].iloc[-1]),
        }
        _write_index(index)
        return path
    except Exception as exc:
        LOGGER.warning("Failed to persist crypto bars %s %s: %s", symbol, timeframe, exc)
        return None


def load_crypto_bars(symbol: str, timeframe: str) -> pd.DataFrame:
    path = crypto_bar_path(symbol, timeframe)
    if not path.exists():
        return pd.DataFrame(columns=_COLUMNS)
    try:
        return _normalize_frame(pd.read_csv(path))
    except Exception as exc:
        LOGGER.warning("Failed to load crypto bars %s %s: %s", symbol, timeframe, exc)
        return pd.DataFrame(columns=_COLUMNS)


def crypto_index_snapshot() -> Dict[str, Dict[str, object]]:
    return _read_index()
