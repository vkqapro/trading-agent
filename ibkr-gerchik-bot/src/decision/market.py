"""Deterministic quote freshness and price helpers for autonomous execution."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Mapping


def quote_last(quote: Mapping[str, object]) -> float:
    for key in ("last", "ask", "bid", "close"):
        try:
            value = float(quote.get(key, 0.0) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        if math.isfinite(value) and value > 0:
            return value
    return 0.0


def quote_spread_pct(quote: Mapping[str, object]) -> float:
    try:
        bid = float(quote.get("bid", 0.0) or 0.0)
        ask = float(quote.get("ask", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 1.0
    if not all(math.isfinite(value) for value in (bid, ask)) or bid <= 0 or ask <= 0:
        return 1.0
    mid = (bid + ask) / 2.0
    return abs(ask - bid) / mid if mid > 0 else 1.0


def quote_age_seconds(quote: Mapping[str, object], *, now: datetime | None = None) -> float | None:
    """Return age or None when freshness cannot be proven."""
    for key in ("quote_age_seconds", "age_seconds"):
        if key in quote and quote[key] is not None:
            try:
                value = float(quote[key])
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) and value >= 0 else None
    raw = quote.get("quote_timestamp") or quote.get("timestamp") or quote.get("last_update")
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        reference = now or datetime.now(timezone.utc)
        return max(0.0, (reference.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None
