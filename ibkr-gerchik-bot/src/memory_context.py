"""Helpers for reading strategy and recent workflow memory."""

from __future__ import annotations

from pathlib import Path
from typing import Dict


def read_text_tail(path: Path, max_lines: int = 80) -> str:
    """Return the tail of a text file, or an empty string if it does not exist."""
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[-max_lines:])


def read_text_full(path: Path) -> str:
    """Return the full contents of a text file, or an empty string if missing."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def load_workflow_context(strategy_doc: Path, research_log: Path, trade_log: Path) -> Dict[str, str]:
    """Load the strategy doc plus recent research/trade context for workflow jobs."""
    return {
        "strategy_doc": read_text_full(strategy_doc),
        "research_log_tail": read_text_tail(research_log),
        "trade_log_tail": read_text_tail(trade_log),
    }
