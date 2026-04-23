"""Deterministic news risk filter."""

from __future__ import annotations

from typing import Iterable, List


HIGH_RISK_KEYWORDS = {
    "earnings",
    "fda",
    "offering",
    "bankruptcy",
    "guidance",
    "lawsuit",
    "merger",
    "acquisition",
}


def filter_news_risk(headlines: Iterable[str]) -> List[str]:
    """Return headlines that should exclude symbols from trading due to event risk."""
    flagged: List[str] = []
    for headline in headlines:
        normalized = headline.lower()
        if any(keyword in normalized for keyword in HIGH_RISK_KEYWORDS):
            flagged.append(headline)
    return flagged
