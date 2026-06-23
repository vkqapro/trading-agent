"""Excel export helpers for premarket level reports."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.config import LOGGER, SETTINGS, ensure_directories


def _coerce_float(value: object) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _zone(level: Dict[str, object]) -> Tuple[Optional[float], Optional[float]]:
    price = _coerce_float(level.get("price"))
    zone_low = _coerce_float(level.get("zone_low"))
    zone_high = _coerce_float(level.get("zone_high"))
    low = zone_low if zone_low is not None else price
    high = zone_high if zone_high is not None else price
    if low is None or high is None:
        return None, None
    return min(low, high), max(low, high)


def _center(level: Dict[str, object]) -> float:
    center = _coerce_float(level.get("center"))
    price = _coerce_float(level.get("price"))
    return center if center is not None else float(price or 0.0)


def _touches(level: Dict[str, object]) -> int:
    try:
        return int(level.get("touches", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _strength(level: Dict[str, object]) -> float:
    return _coerce_float(level.get("strength_score")) or _coerce_float(level.get("strength")) or 0.0


def _level_key(level: Dict[str, object]) -> Tuple[float, float, float]:
    zone_low, zone_high = _zone(level)
    return (
        round(_level_price(level) or 0.0, 2),
        round(zone_low or 0.0, 2),
        round(zone_high or 0.0, 2),
    )


def _level_price(level: Optional[Dict[str, object]]) -> Optional[float]:
    if level is None:
        return None
    price = _coerce_float(level.get("price"))
    return price if price is not None else _center(level)


def _trade_level_eligible(level: Dict[str, object]) -> bool:
    return _touches(level) >= SETTINGS.strategy.min_trade_level_touches


def _gap_to_upper_level(
    lower_level: Dict[str, object],
    upper_level: Dict[str, object],
    daily_atr: float,
) -> Tuple[Optional[float], Optional[float]]:
    _, lower_zone_high = _zone(lower_level)
    upper_zone_low, _ = _zone(upper_level)
    if lower_zone_high is None or upper_zone_low is None:
        return None, None
    clean_gap = max(upper_zone_low - lower_zone_high, 0.0)
    clean_gap_atr_pct = clean_gap / daily_atr if daily_atr > 0 else None
    return clean_gap, clean_gap_atr_pct


def _priority_level(
    *,
    anchor: Dict[str, object],
    candidates: List[Tuple[Dict[str, object], float, float, int]],
) -> Tuple[Optional[Dict[str, object]], Optional[float], Optional[float], Optional[int]]:
    if not candidates:
        return None, None, None, None
    anchor_center = _center(anchor)
    level, clean_gap, clean_gap_atr_pct, skipped = min(
        candidates,
        key=lambda item: (
            abs(_center(item[0]) - anchor_center),
            -_touches(item[0]),
            -_strength(item[0]),
        ),
    )
    return level, clean_gap, clean_gap_atr_pct, skipped


def _raw_levels_between_count(
    ordered_raw_levels: List[Dict[str, object]],
    left: Dict[str, object],
    right: Dict[str, object],
) -> int:
    left_center = _center(left)
    right_center = _center(right)
    lower_center = min(left_center, right_center)
    upper_center = max(left_center, right_center)
    return sum(1 for level in ordered_raw_levels if lower_center < _center(level) < upper_center)


def _optimization_reason(level: Dict[str, object], trade_level_keys: set[Tuple[float, float, float]]) -> str:
    if _level_key(level) in trade_level_keys:
        return "selected"
    if not _trade_level_eligible(level):
        return "touches_below_minimum"
    return "too_close_to_selected_trade_level"


def _clean_gap_annotations(
    levels: List[Dict[str, object]],
    daily_atr: float,
    *,
    trade_levels: Optional[List[Dict[str, object]]] = None,
) -> Dict[int, Dict[str, object]]:
    ordered = sorted(levels, key=_center)
    ordered_trade_levels = sorted(trade_levels if trade_levels is not None else levels, key=_center)
    trade_level_keys = {_level_key(level) for level in ordered_trade_levels}
    annotations: Dict[int, Dict[str, object]] = {}
    min_gap_pct = SETTINGS.strategy.min_clean_level_gap_atr_pct

    for index, level in enumerate(ordered):
        previous_raw_level = ordered[index - 1] if index > 0 else None
        next_raw_level = ordered[index + 1] if index < len(ordered) - 1 else None
        raw_down_gap, raw_down_gap_atr_pct = (
            _gap_to_upper_level(previous_raw_level, level, daily_atr) if previous_raw_level is not None else (None, None)
        )
        raw_up_gap, raw_up_gap_atr_pct = (
            _gap_to_upper_level(level, next_raw_level, daily_atr) if next_raw_level is not None else (None, None)
        )

        below_candidates: List[Tuple[Dict[str, object], float, float, int]] = []
        above_candidates: List[Tuple[Dict[str, object], float, float, int]] = []

        for candidate in ordered_trade_levels:
            if _center(candidate) >= _center(level) or _level_key(candidate) == _level_key(level):
                continue
            if not _trade_level_eligible(candidate):
                continue
            candidate_gap, candidate_gap_atr_pct = _gap_to_upper_level(candidate, level, daily_atr)
            if candidate_gap is None or candidate_gap_atr_pct is None or candidate_gap_atr_pct < min_gap_pct:
                continue
            below_candidates.append((candidate, candidate_gap, candidate_gap_atr_pct, _raw_levels_between_count(ordered, candidate, level)))

        for candidate in ordered_trade_levels:
            if _center(candidate) <= _center(level) or _level_key(candidate) == _level_key(level):
                continue
            if not _trade_level_eligible(candidate):
                continue
            candidate_gap, candidate_gap_atr_pct = _gap_to_upper_level(level, candidate, daily_atr)
            if candidate_gap is None or candidate_gap_atr_pct is None or candidate_gap_atr_pct < min_gap_pct:
                continue
            above_candidates.append((candidate, candidate_gap, candidate_gap_atr_pct, _raw_levels_between_count(ordered, level, candidate)))

        below_level, below_gap, below_gap_atr_pct, skipped_below = _priority_level(anchor=level, candidates=below_candidates)
        above_level, above_gap, above_gap_atr_pct, skipped_above = _priority_level(anchor=level, candidates=above_candidates)
        level_eligible = _trade_level_eligible(level)
        used_for_trading = _level_key(level) in trade_level_keys
        tradeable_below = "YES" if used_for_trading and level_eligible and below_level is not None else "NO"
        tradeable_above = "YES" if used_for_trading and level_eligible and above_level is not None else "NO"
        annotations[id(level)] = {
            "used_for_trading": "YES" if used_for_trading else "NO",
            "optimization_reason": _optimization_reason(level, trade_level_keys),
            "trade_level_eligible": "YES" if level_eligible else "NO",
            "min_trade_level_touches": SETTINGS.strategy.min_trade_level_touches,
            "previous_raw_level": _level_price(previous_raw_level),
            "raw_down_gap": round(raw_down_gap, 4) if raw_down_gap is not None else None,
            "raw_down_gap_atr_pct": round(raw_down_gap_atr_pct, 4) if raw_down_gap_atr_pct is not None else None,
            "next_raw_level": _level_price(next_raw_level),
            "raw_up_gap": round(raw_up_gap, 4) if raw_up_gap is not None else None,
            "raw_up_gap_atr_pct": round(raw_up_gap_atr_pct, 4) if raw_up_gap_atr_pct is not None else None,
            "trade_level_below": _level_price(below_level),
            "trade_level_below_touches": _touches(below_level) if below_level is not None else None,
            "trade_clean_gap_below": round(below_gap, 4) if below_gap is not None else None,
            "trade_clean_gap_atr_pct_below": round(below_gap_atr_pct, 4) if below_gap_atr_pct is not None else None,
            "tradeable_room_below": tradeable_below,
            "skipped_raw_levels_below": skipped_below,
            "trade_level_above": _level_price(above_level),
            "trade_level_above_touches": _touches(above_level) if above_level is not None else None,
            "trade_clean_gap_above": round(above_gap, 4) if above_gap is not None else None,
            "trade_clean_gap_atr_pct_above": round(above_gap_atr_pct, 4) if above_gap_atr_pct is not None else None,
            "tradeable_room_above": tradeable_above,
            "skipped_raw_levels_above": skipped_above,
        }
    return annotations


def export_premarket_levels_report(watchlist: Dict[str, object]) -> Path:
    """Write a daily Excel report of strong levels for the premarket snapshot."""
    ensure_directories()
    rows: List[Dict[str, object]] = []
    min_strength = SETTINGS.premarket_levels_export_min_strength

    for symbol, plan in watchlist.items():
        levels = plan.get("raw_levels", plan.get("levels", [])) if isinstance(plan, dict) else []
        trade_levels = plan.get("levels", []) if isinstance(plan, dict) else []
        if not isinstance(levels, list):
            continue
        if not isinstance(trade_levels, list):
            trade_levels = []
        typed_levels = [level for level in levels if isinstance(level, dict)]
        typed_trade_levels = [level for level in trade_levels if isinstance(level, dict)]
        daily_atr = _coerce_float(plan.get("daily_atr")) or 0.0
        spacing = _clean_gap_annotations(typed_levels, daily_atr, trade_levels=typed_trade_levels)
        for level in levels:
            if not isinstance(level, dict):
                continue
            strength = float(level.get("strength_score", 0.0) or 0.0)
            if strength <= min_strength:
                continue
            annotation = spacing.get(id(level), {})
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
                    "UsedForTrading": annotation.get("used_for_trading"),
                    "OptimizationReason": annotation.get("optimization_reason"),
                    "TradeLevelEligible": annotation.get("trade_level_eligible"),
                    "MinTradeLevelTouches": annotation.get("min_trade_level_touches"),
                    "PreviousRawLevel": annotation.get("previous_raw_level"),
                    "RawCleanGapToPrevious": annotation.get("raw_down_gap"),
                    "RawCleanGapAtrPctPrevious": annotation.get("raw_down_gap_atr_pct"),
                    "NextRawLevel": annotation.get("next_raw_level"),
                    "RawCleanGapToNext": annotation.get("raw_up_gap"),
                    "RawCleanGapAtrPctNext": annotation.get("raw_up_gap_atr_pct"),
                    "TradeLevelBelow": annotation.get("trade_level_below"),
                    "TradeLevelBelowTouches": annotation.get("trade_level_below_touches"),
                    "TradeCleanGapBelow": annotation.get("trade_clean_gap_below"),
                    "TradeCleanGapAtrPctBelow": annotation.get("trade_clean_gap_atr_pct_below"),
                    "TradeableRoomBelow": annotation.get("tradeable_room_below"),
                    "SkippedRawLevelsBelow": annotation.get("skipped_raw_levels_below"),
                    "TradeLevelAbove": annotation.get("trade_level_above"),
                    "TradeLevelAboveTouches": annotation.get("trade_level_above_touches"),
                    "TradeCleanGapAbove": annotation.get("trade_clean_gap_above"),
                    "TradeCleanGapAtrPctAbove": annotation.get("trade_clean_gap_atr_pct_above"),
                    "TradeableRoomAbove": annotation.get("tradeable_room_above"),
                    "SkippedRawLevelsAbove": annotation.get("skipped_raw_levels_above"),
                }
            )

    if rows:
        frame = pd.DataFrame(rows).sort_values(["Ticker", "Level", "StrengthScore", "Touches"], ascending=[True, False, False, False])
    else:
        frame = pd.DataFrame(
            columns=[
                "Ticker",
                "SourceDate",
                "FirstTouchDate",
                "Level",
                "ZoneLow",
                "ZoneHigh",
                "Touches",
                "StrengthScore",
                "LevelType",
                "Families",
                "UsedForTrading",
                "OptimizationReason",
                "TradeLevelEligible",
                "MinTradeLevelTouches",
                "PreviousRawLevel",
                "RawCleanGapToPrevious",
                "RawCleanGapAtrPctPrevious",
                "NextRawLevel",
                "RawCleanGapToNext",
                "RawCleanGapAtrPctNext",
                "TradeLevelBelow",
                "TradeLevelBelowTouches",
                "TradeCleanGapBelow",
                "TradeCleanGapAtrPctBelow",
                "TradeableRoomBelow",
                "SkippedRawLevelsBelow",
                "TradeLevelAbove",
                "TradeLevelAboveTouches",
                "TradeCleanGapAbove",
                "TradeCleanGapAtrPctAbove",
                "TradeableRoomAbove",
                "SkippedRawLevelsAbove",
            ]
        )

    timestamp = datetime.now()
    output_path = SETTINGS.paths.reports_dir / f"premarket_levels_{timestamp.strftime('%Y%m%d')}.xlsx"
    try:
        frame.to_excel(output_path, index=False)
    except PermissionError:
        fallback_path = SETTINGS.paths.reports_dir / f"premarket_levels_{timestamp.strftime('%Y%m%d_%H%M%S')}.xlsx"
        LOGGER.warning("Premarket levels report is locked: %s; writing fallback report: %s", output_path, fallback_path)
        frame.to_excel(fallback_path, index=False)
        output_path = fallback_path
    return output_path
