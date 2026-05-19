"""Excel exports for intraday scan outcomes."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from openpyxl import Workbook

from src.config import LOGGER


def _coerce_float(value: object) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _quote_reference_price(quote: Dict[str, object]) -> Optional[float]:
    bid = _coerce_float(quote.get("bid"))
    ask = _coerce_float(quote.get("ask"))
    last = _coerce_float(quote.get("last"))
    close = _coerce_float(quote.get("close"))

    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    if last is not None and last > 0:
        return last
    if close is not None and close > 0:
        return close
    return None


def nearest_level_details(plan: Dict[str, object], quote: Optional[Dict[str, object]] = None) -> Tuple[Optional[float], str]:
    """Return the most relevant watchlist level for reporting."""
    levels = plan.get("levels", [])
    if not isinstance(levels, list) or not levels:
        return None, ""

    quote_payload = quote if isinstance(quote, dict) else {}
    reference_price = _quote_reference_price(quote_payload)
    chosen_level: Optional[Dict[str, object]] = None

    if reference_price is not None:
        best_distance: Optional[float] = None
        for raw_level in levels:
            if not isinstance(raw_level, dict):
                continue
            level_price = _coerce_float(raw_level.get("center"))
            if level_price is None:
                level_price = _coerce_float(raw_level.get("price"))
            if level_price is None:
                continue
            distance = abs(level_price - reference_price)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                chosen_level = raw_level

    if chosen_level is None:
        ranked_levels = [level for level in levels if isinstance(level, dict)]
        if not ranked_levels:
            return None, ""
        chosen_level = max(
            ranked_levels,
            key=lambda level: _coerce_float(level.get("strength_score")) or _coerce_float(level.get("strength")) or 0.0,
        )

    nearest_level = _coerce_float(chosen_level.get("center"))
    if nearest_level is None:
        nearest_level = _coerce_float(chosen_level.get("price"))
    level_type = str(chosen_level.get("type", "") or "")
    return nearest_level, level_type


def write_intraday_scan_report(
    *,
    report_rows: Iterable[Dict[str, object]],
    scan_time: datetime,
    report_dir: Path,
) -> Path:
    """Write one intraday scan result workbook and return its path."""
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"intraday_scan_{scan_time.strftime('%Y%m%d_%H%M%S')}.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Intraday Scan"
    headers = ["stock_symbol", "nearest_level", "nearest_level_type", "reason_not_entered"]
    sheet.append(headers)

    for row in report_rows:
        sheet.append(
            [
                str(row.get("stock_symbol", "") or ""),
                row.get("nearest_level"),
                str(row.get("nearest_level_type", "") or ""),
                str(row.get("reason_not_entered", "") or ""),
            ]
        )

    for column_cells in sheet.columns:
        values = ["" if cell.value is None else str(cell.value) for cell in column_cells]
        max_length = max((len(value) for value in values), default=0)
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 14), 48)

    workbook.save(report_path)
    LOGGER.info("Intraday scan report written: %s", report_path)
    return report_path
