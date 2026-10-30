"""Helpers for maintaining the configured stock-symbol universe."""

from __future__ import annotations

import csv
import os
import re
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STOCK_SYMBOLS_FILE = ROOT / "config" / "STOCK_SYMBOLS.csv"
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{0,9}$")


def normalize_stock_symbol(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    if not SYMBOL_RE.fullmatch(normalized):
        raise ValueError("Use a stock ticker like NVDA, AAPL, TSLA. Letters/numbers only, max 10 characters.")
    return normalized


def stock_symbols_file() -> Path:
    configured = str(os.environ.get("STOCK_SYMBOLS_FILE", "") or "").strip()
    if not configured:
        return DEFAULT_STOCK_SYMBOLS_FILE
    path = Path(configured)
    return path if path.is_absolute() else (ROOT / path)


def load_stock_symbols(path: Path | None = None) -> List[str]:
    resolved = path or stock_symbols_file()
    if not resolved.exists():
        return []
    with resolved.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        rows = [row for row in reader if row and any(cell.strip() for cell in row)]
    if not rows:
        return []

    header = [cell.strip().lower() for cell in rows[0]]
    symbol_index = header.index("symbol") if "symbol" in header else None
    data_rows = rows[1:] if symbol_index is not None else rows

    symbols: List[str] = []
    seen: set[str] = set()
    for row in data_rows:
        if not row:
            continue
        raw = row[symbol_index] if symbol_index is not None and symbol_index < len(row) else row[0]
        raw = str(raw or "").strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            symbol = normalize_stock_symbol(raw)
        except ValueError:
            continue
        if symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def add_stock_symbol(symbol: str, path: Path | None = None) -> dict:
    """Add ``symbol`` to the stock CSV if absent. Returns a serialisable result."""
    normalized = normalize_stock_symbol(symbol)
    resolved = path or stock_symbols_file()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    existing = load_stock_symbols(resolved)
    if normalized in existing:
        return {"symbol": normalized, "added": False, "path": str(resolved)}

    updated = [*existing, normalized]
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol"])
        for item in updated:
            writer.writerow([item])
    os.replace(temp, resolved)
    return {"symbol": normalized, "added": True, "path": str(resolved)}


def remove_stock_symbol(symbol: str, path: Path | None = None) -> dict:
    """Remove ``symbol`` from the stock CSV if present. Returns a serialisable result."""
    normalized = normalize_stock_symbol(symbol)
    resolved = path or stock_symbols_file()
    existing = load_stock_symbols(resolved)
    updated = [item for item in existing if item != normalized]
    removed = len(updated) != len(existing)

    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol"])
        for item in updated:
            writer.writerow([item])
    os.replace(temp, resolved)
    return {"symbol": normalized, "removed": removed, "path": str(resolved)}
