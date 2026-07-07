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


def add_crypto_symbol(symbol: str, *, path: Path | None = None) -> dict:
    """Persist a crypto symbol input unless its normalized instrument exists.

    Returns a small status payload for dashboard API callers. Duplicate checks
    are performed on normalized OKX instrument ids, so ``DOGEUSDT`` and
    ``DOGE-USDT`` are treated as the same symbol.
    """

    inst_id = normalize_okx_instrument(symbol)
    if not inst_id:
        raise ValueError("Crypto symbol is required.")

    configured = {
        normalize_okx_instrument(value)
        for value in load_crypto_symbol_inputs(path)
        if normalize_okx_instrument(value)
    }
    if inst_id in configured:
        return {"added": False, "duplicate": True, "symbol": inst_id}

    resolved = path or CRYPTO_SETTINGS.symbols_file
    resolved.parent.mkdir(parents=True, exist_ok=True)
    existing_text = resolved.read_text(encoding="utf-8-sig") if resolved.exists() else ""
    append_text = inst_id
    if existing_text and not existing_text.endswith(("\n", "\r")):
        append_text = f"\n{append_text}"
    with resolved.open("a", encoding="utf-8") as handle:
        if not existing_text:
            handle.write("# TradingView/OKX symbols are normalized by src.crypto.symbols.\n")
            handle.write("# Keep one comma-separated line or one symbol per line.\n")
        handle.write(f"{append_text}\n")
    return {"added": True, "duplicate": False, "symbol": inst_id}
