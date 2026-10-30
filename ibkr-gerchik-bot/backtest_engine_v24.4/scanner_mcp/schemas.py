"""Validation and stable error contracts for Strategy Scanner MCP."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

RUN_ID_RE = re.compile(r"^scan_\d{8}T\d{6}Z_[0-9a-f]{6}$")
STRATEGY_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


@dataclass(frozen=True)
class ScannerMCPError(Exception):
    code: str
    message: str
    details: dict[str, Any] | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": dict(self.details or {}),
            }
        }


def validate_run_id(run_id: str) -> str:
    value = str(run_id or "").strip()
    if not RUN_ID_RE.fullmatch(value):
        raise ScannerMCPError("RUN_NOT_FOUND", "Scan run was not found", {"run_id": value})
    return value


def validate_strategy_token(strategy_id: str) -> str:
    value = str(strategy_id or "").strip().lower()
    if not STRATEGY_ID_RE.fullmatch(value):
        raise ScannerMCPError("INVALID_STRATEGY", "Strategy is not production-allowlisted", {"strategy_id": value})
    return value
