"""Optional artifact output routed exclusively to the research workspace."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .safety import write_json_artifact
from .timezones import EASTERN


def write_review_artifact(result: dict, *, name: str | None = None) -> Path:
    """Write an optional runtime review only below ``C:\\AI\\TradingResearch``."""
    stamp = datetime.now(EASTERN).strftime("%Y%m%dT%H%M%S%z")
    filename = name or f"trade_review_{stamp}.json"
    if not filename.lower().endswith(".json"):
        filename += ".json"
    return write_json_artifact("workspace/exports/" + filename, result)

