"""Normalize TradingView/OKX crypto symbols into OKX instrument ids."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List

from src.crypto.config import CRYPTO_SETTINGS


_QUOTE_SUFFIXES = ("USDT", "USDC", "USD", "BTC", "ETH", "EUR")


def _split_raw_text(text: str) -> List[str]:
    values: List[str] = []
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#"):
            continue
        values.extend(item.strip() for item in cleaned.split(",") if item.strip())
    return values


def load_crypto_symbol_inputs(path: Path | None = None) -> List[str]:
    values: List[str] = []
    resolved = path or CRYPTO_SETTINGS.symbols_file
    if resolved.exists():
        values.extend(_split_raw_text(resolved.read_text(encoding="utf-8-sig")))
    values.extend(CRYPTO_SETTINGS.inline_symbols)
    return _dedupe(values)


def _dedupe(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        key = normalized.upper()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


def normalize_okx_instrument(symbol: str) -> str:
    """Convert user/TradingView symbol text to a likely OKX instrument id."""
    raw = str(symbol or "").strip().upper()
    if not raw:
        return ""
    if ":" in raw:
        prefix, raw = raw.split(":", 1)
        # TradingView INDEX symbols are not OKX tradables; map BTCUSD to a
        # conservative spot proxy so the chart can still display.
        if prefix == "INDEX" and raw == "BTCUSD":
            raw = "BTCUSDT"
    raw = raw.replace("/", "").replace("_", "").replace(" ", "")
    is_swap = raw.endswith(".P") or raw.endswith("PERP") or raw.endswith("SWAP")
    raw = re.sub(r"(\.P|PERP|SWAP)$", "", raw)

    # Already OKX-ish, e.g. BTC-USDT or BTC-USDT-SWAP.
    if "-" in raw:
        parts = [part for part in raw.split("-") if part]
        if len(parts) >= 2:
            inst = f"{parts[0]}-{parts[1]}"
            if is_swap or (len(parts) > 2 and parts[2] == "SWAP"):
                inst += "-SWAP"
            return inst

    for quote in _QUOTE_SUFFIXES:
        if raw.endswith(quote) and len(raw) > len(quote):
            base = raw[: -len(quote)]
            inst = f"{base}-{quote}"
            if is_swap:
                inst += "-SWAP"
            return inst
    return raw


def default_instrument_type(inst_id: str) -> str:
    return "SWAP" if inst_id.endswith("-SWAP") else "SPOT"

