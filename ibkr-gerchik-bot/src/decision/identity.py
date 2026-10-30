"""Stable identities shared by autonomous persistence and broker provenance."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping


def execution_key(*, mode: str, agent_id: str, candidate_id: str, account_id: str | None = None) -> str:
    """Return the mode/agent/candidate namespace key used by SQLite claims."""
    parts = [str(mode).strip().lower(), str(agent_id).strip(), str(candidate_id).strip()]
    if account_id:
        parts.append(str(account_id).strip())
    return "|".join(parts)


def order_fingerprint(fields: Mapping[str, object]) -> str:
    encoded = json.dumps(dict(fields), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def broker_order_ref(*, agent_id: str, candidate_id: str, decision_id: str) -> str:
    """Create a compact deterministic orderRef suitable for IBKR order fields."""
    digest = hashlib.sha256(
        f"{agent_id}|{candidate_id}|{decision_id}".encode("utf-8")
    ).hexdigest()[:20]
    return f"LLM-{digest}"
