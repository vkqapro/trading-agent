"""Immutable normalized values used by the research-only analytics pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def json_value(value: Any) -> Any:
    """Recursively turn immutable research values into JSON-safe primitives."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [json_value(item) for item in value]
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class ResearchTrade:
    """A non-persistable normalized projection of one journal record."""

    trade_id: str
    symbol: str
    market: str | None
    source: str | None
    strategy: str
    strategy_version: str | None
    side: str
    status: str
    status_group: str
    created_at: datetime | None
    setup_time: datetime | None
    signal_time: datetime | None
    entry_time: datetime | None
    exit_time: datetime | None
    planned_entry: Decimal | None
    planned_stop: Decimal | None
    planned_target: Decimal | None
    current_stop: Decimal | None
    current_target: Decimal | None
    entry_price: Decimal | None
    exit_price: Decimal | None
    quantity: Decimal | None
    realized_pnl: Decimal | None
    fees: Decimal | None
    exit_reason: str | None
    approval_source: str | None
    execution_source: str | None
    trading_mode: str | None
    missing_fields: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return json_value(asdict(self))


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None


STATUS_GROUPS: dict[str, str] = {
    "OPEN": "active",
    "SUBMITTED": "active",
    "OPEN_ALERT": "active",
    "CLOSE_ALERT": "active",
    "UNFILLED": "non_executed",
    "REJECTED": "non_executed",
    "CANCELLED": "non_executed",
    "CANCELED": "non_executed",
    "EXPIRED": "non_executed",
    "FAILED": "non_executed",
    "ERROR": "non_executed",
    "CLOSED_UNKNOWN": "ambiguous",
}


def status_group(status: str) -> str:
    normalized = str(status or "UNKNOWN").upper()
    if normalized in STATUS_GROUPS:
        return STATUS_GROUPS[normalized]
    if normalized.startswith("CLOSED"):
        return "completed"
    return "excluded"

