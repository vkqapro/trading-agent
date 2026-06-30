"""Crypto bot configuration.

API secrets should live in the local .env file only. Public candle collection
does not require OKX credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from src.config import BASE_DIR, MEMORY_DIR


def _env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _env_bool(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return _env_str(name, fallback).lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    return int(_env_str(name, str(default)))


def _env_csv(name: str, default: str = "") -> List[str]:
    return [item.strip() for item in _env_str(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class CryptoConfig:
    symbols_file: Path = BASE_DIR / _env_str("CRYPTO_SYMBOLS_FILE", "config/crypto_symbols.txt")
    inline_symbols: List[str] = field(default_factory=list)
    okx_base_url: str = _env_str("OKX_BASE_URL", "https://www.okx.com")
    okx_api_key: str = _env_str("OKX_API_KEY", "")
    okx_api_secret: str = _env_str("OKX_API_SECRET", "")
    okx_api_passphrase: str = _env_str("OKX_API_PASSPHRASE", "")
    okx_simulated_trading: bool = _env_bool("OKX_SIMULATED_TRADING", True)
    okx_account_mode: str = _env_str("OKX_ACCOUNT_MODE", "spot").lower()
    request_timeout_seconds: int = _env_int("OKX_REQUEST_TIMEOUT_SECONDS", 20)
    candle_limit: int = _env_int("OKX_CANDLE_LIMIT", 300)
    collect_interval_seconds: int = _env_int("CRYPTO_COLLECT_INTERVAL_SECONDS", 300)
    memory_dir: Path = MEMORY_DIR / "crypto"

    def __post_init__(self) -> None:
        object.__setattr__(self, "inline_symbols", _env_csv("CRYPTO_SYMBOLS", ""))


CRYPTO_SETTINGS = CryptoConfig()


def ensure_crypto_directories() -> None:
    CRYPTO_SETTINGS.memory_dir.mkdir(parents=True, exist_ok=True)
    (CRYPTO_SETTINGS.memory_dir / "bars").mkdir(parents=True, exist_ok=True)
    (CRYPTO_SETTINGS.memory_dir / "runtime").mkdir(parents=True, exist_ok=True)
