"""Excel export helpers for premarket level reports."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pandas as pd

from src.config import SETTINGS, ensure_directories


def export_premarket_levels_report(watchlist: Dict[str, object]) -> Path:
    """Write a daily Excel report of strong levels for the premarket snapshot."""
    ensure_directories()
    rows: List[Dict[str, object]] = []
    min_strength = SETTINGS.premarket_levels_export_min_strength

    for symbol, plan in watchlist.items():
        levels = plan.get("levels", []) if isinstance(plan, dict) else []
        if not isinstance(levels, list):
            continue
        for level in levels:
            if not isinstance(level, dict):
                continue
            strength = float(level.get("strength_score", 0.0) or 0.0)
            if strength <= min_strength:
                continue
            rows.append(
                {
                    "Ticker": symbol,
                    "SourceDate": str(level.get("source_date", "") or ""),
                    "FirstTouchDate": str(level.get("first_touch_date", "") or ""),
                    "Level": float(level.get("price", 0.0) or 0.0),
                    "ZoneLow": float(level.get("zone_low", level.get("price", 0.0)) or 0.0),
                    "ZoneHigh": float(level.get("zone_high", level.get("price", 0.0)) or 0.0),
                    "Touches": int(level.get("touches", 0) or 0),
                    "StrengthScore": round(strength, 2),
                    "LevelType": str(level.get("type", "")),
                    "Families": ", ".join(level.get("families", []) or []),
                }
            )

    if rows:
        frame = pd.DataFrame(rows).sort_values(["Ticker", "Level", "StrengthScore", "Touches"], ascending=[True, False, False, False])
    else:
        frame = pd.DataFrame(
            columns=["Ticker", "SourceDate", "FirstTouchDate", "Level", "ZoneLow", "ZoneHigh", "Touches", "StrengthScore", "LevelType", "Families"]
        )

    output_path = SETTINGS.paths.reports_dir / f"premarket_levels_{datetime.now().strftime('%Y%m%d')}.xlsx"
    frame.to_excel(output_path, index=False)
    return output_path
