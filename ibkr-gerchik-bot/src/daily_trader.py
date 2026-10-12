"""Read-only Daily Trader orchestration over Trend Radar and the production scanner.

This module is deliberately an adapter, not another scanner.  Radar rows are
read from the existing ``RadarService`` and scanner results are obtained only
through its injected authoritative scanner callback.  No order, broker, or
execution module is imported here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.config import SETTINGS
from src.radar.service import RadarService


DAILY_TRADER_MODES = {"scheduled", "top", "breakouts", "fresh_flips"}
ACTIVE_LIFECYCLE_STATUSES = {"PENDING_ENTRY", "ENTRY_TRIGGERED"}
TERMINAL_LIFECYCLE_STATUSES = {
    "INVALIDATED",
    "EXPIRED",
    "AMBIGUOUS_INTRABAR_ORDER",
}


@dataclass(frozen=True)
class DailyTraderScanRequest:
    """Validated, intentionally small request contract for scheduled scans."""

    mode: str = "scheduled"
    top_n: int = 10
    include_breakouts: bool = True
    include_fresh_flips: bool = True
    max_symbols: int = 30

    @classmethod
    def from_mapping(cls, body: Mapping[str, Any] | None) -> "DailyTraderScanRequest":
        values = dict(body or {})
        mode = str(values.get("mode", cls.mode) or cls.mode).strip().lower()
        if mode not in DAILY_TRADER_MODES:
            raise ValueError(f"mode must be one of: {', '.join(sorted(DAILY_TRADER_MODES))}")

        def integer(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(values.get(name, default))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be an integer") from exc
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
            return value

        def boolean(name: str, default: bool) -> bool:
            raw = values.get(name, default)
            if isinstance(raw, bool):
                return raw
            normalized = str(raw).strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
            raise ValueError(f"{name} must be a boolean")

        return cls(
            mode=mode,
            top_n=integer("top_n", cls.top_n, 0, 100),
            include_breakouts=boolean("include_breakouts", cls.include_breakouts),
            include_fresh_flips=boolean("include_fresh_flips", cls.include_fresh_flips),
            max_symbols=integer("max_symbols", cls.max_symbols, 1, 100),
        )


class DailyTraderService:
    """Build stable read-only reports from the existing Radar/scanner seams."""

    def __init__(
        self,
        *,
        radar_service: RadarService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.radar = radar_service
        self._now = now or (lambda: datetime.now(ZoneInfo("America/New_York")))

    def _market_status(self, moment: datetime | None = None) -> str:
        current = moment or self._now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=ZoneInfo("America/New_York"))
        else:
            current = current.astimezone(ZoneInfo("America/New_York"))
        if current.weekday() >= 5:
            return "CLOSED"
        open_at = current.replace(
            hour=int(getattr(SETTINGS.trading_hours, "market_open_hour", 9)),
            minute=int(getattr(SETTINGS.trading_hours, "market_open_minute", 30)),
            second=0,
            microsecond=0,
        )
        close_at = current.replace(
            hour=int(getattr(SETTINGS.trading_hours, "market_close_hour", 16)),
            minute=int(getattr(SETTINGS.trading_hours, "market_close_minute", 0)),
            second=0,
            microsecond=0,
        )
        return "OPEN" if open_at <= current <= close_at else "CLOSED"

    @staticmethod
    def _radar_sort_key(row: Mapping[str, Any]) -> tuple[float, str, str]:
        try:
            score = float(row.get("radar_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        return (-score, str(row.get("symbol") or ""), str(row.get("as_of") or ""))

    def _radar_rows(self) -> list[dict[str, Any]]:
        rows = self.radar.list_results(limit=5000)
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    def select_symbols(self, request: DailyTraderScanRequest) -> tuple[list[str], dict[str, Any], dict[str, dict[str, Any]]]:
        """Select from the latest persisted Radar snapshot without recalculating it."""
        rows = sorted(self._radar_rows(), key=self._radar_sort_key)
        top = rows[: request.top_n]
        breakouts = [row for row in rows if row.get("breakout") is True]
        fresh_flips = [row for row in rows if row.get("trend_flip") is True]

        if request.mode == "top":
            groups: Iterable[list[dict[str, Any]]] = (top,)
        elif request.mode == "breakouts":
            groups = (breakouts,)
        elif request.mode == "fresh_flips":
            groups = (fresh_flips,)
        else:
            groups = (top,)
            if request.include_breakouts:
                groups = (*groups, breakouts)
            if request.include_fresh_flips:
                groups = (*groups, fresh_flips)

        selected: list[str] = []
        by_symbol: dict[str, dict[str, Any]] = {}
        for group in groups:
            for row in group:
                symbol = str(row.get("symbol") or "").strip().upper()
                if not symbol or symbol in by_symbol:
                    continue
                by_symbol[symbol] = row
                selected.append(symbol)
                if len(selected) >= request.max_symbols:
                    break
            if len(selected) >= request.max_symbols:
                break

        selection = {
            "top_n": request.top_n,
            "top": len(top),
            "breakouts": len(breakouts) if request.mode in {"scheduled", "breakouts"} else 0,
            "fresh_flips": len(fresh_flips) if request.mode in {"scheduled", "fresh_flips"} else 0,
            "unique_symbols": len(selected),
            "max_symbols": request.max_symbols,
        }
        return selected, selection, by_symbol

    @staticmethod
    def _radar_context(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        fields = (
            "symbol",
            "as_of",
            "trend",
            "radar_score",
            "trend_score",
            "breakout",
            "breakout_score",
            "breakout_direction",
            "momentum_score",
            "trend_flip",
            "trend_flip_from",
            "trend_flip_to",
            "price",
            "close_price",
            "reasons",
        )
        return {field: row.get(field) for field in fields}

    @staticmethod
    def _lifecycle(signal: Mapping[str, Any]) -> str:
        lifecycle = str(signal.get("lifecycle_status") or "").strip().upper()
        if lifecycle:
            return lifecycle
        # Existing scanner payloads carry lifecycle_status. This compatibility
        # fallback only handles older READY payloads and does not infer a setup.
        status = str(signal.get("status") or "").strip().upper()
        if status == "READY":
            return "ENTRY_TRIGGERED" if bool(signal.get("entry_triggered")) else "PENDING_ENTRY"
        return status or "UNKNOWN"

    @classmethod
    def _scanner_context(cls, signal: Mapping[str, Any], symbol: str) -> dict[str, Any]:
        lifecycle = cls._lifecycle(signal)
        position_size = signal.get("position_size_shares")
        if position_size is None:
            position_size = signal.get("position_size_units")
        return {
            "symbol": symbol,
            "strategy": signal.get("strategy"),
            "side": signal.get("side") or signal.get("direction"),
            "status": signal.get("status"),
            "lifecycle_status": lifecycle,
            "lifecycle_reason": signal.get("lifecycle_reason"),
            "entry_triggered": signal.get("entry_triggered"),
            "level": signal.get("level_price"),
            "level_type": signal.get("level_type"),
            "planned_entry": signal.get("planned_entry_price"),
            "entry": signal.get("entry_price"),
            "stop": signal.get("stop_price"),
            "target": signal.get("take_profit_price"),
            "rr": signal.get("rr"),
            "score": signal.get("score"),
            "pattern_quality": signal.get("pattern_quality"),
            "position_size": position_size,
            "risk_amount": signal.get("risk_amount"),
            "risk_pct": signal.get("risk_pct", signal.get("risk_per_trade_pct")),
            "reward_amount": signal.get("reward_amount"),
            "direction_conflict": signal.get("direction_conflict"),
            "data_quality_complete": signal.get("data_quality_complete"),
            "missing_quality_data": signal.get("missing_quality_data") or [],
            "chronology_source": signal.get("chronology_source"),
            "chronology_event": signal.get("chronology_event"),
            "entry_touch_time": signal.get("entry_touch_time"),
            "invalidation_touch_time": signal.get("invalidation_touch_time"),
        }

    @classmethod
    def _setup(cls, symbol: str, radar: Mapping[str, Any] | None, signal: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "radar": cls._radar_context(radar),
            "scanner": cls._scanner_context(signal, symbol),
        }

    def _assemble_results(
        self,
        results: Iterable[Mapping[str, Any]],
        radar_by_symbol: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        active: list[dict[str, Any]] = []
        terminal: list[dict[str, Any]] = []
        no_setup: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        counts = {
            "active_candidates": 0,
            "entry_triggered": 0,
            "pending_entry": 0,
            "invalidated": 0,
            "expired": 0,
            "ambiguous": 0,
            "no_setup": 0,
            "errors": 0,
        }

        for raw_item in results:
            item = dict(raw_item)
            symbol = str(item.get("symbol") or "").upper()
            signals = [signal for signal in (item.get("signals") or []) if isinstance(signal, Mapping)]
            if str(item.get("status") or "").upper() == "ERROR":
                error = {"symbol": symbol, "error": item.get("error") or "scanner_error"}
                errors.append(error)
                counts["errors"] += 1
                continue
            if not signals:
                no_setup.append({"symbol": symbol, "radar": self._radar_context(radar_by_symbol.get(symbol))})
                counts["no_setup"] += 1
                continue
            for signal in signals:
                setup = self._setup(symbol, radar_by_symbol.get(symbol), signal)
                lifecycle = str(setup["scanner"].get("lifecycle_status") or "UNKNOWN").upper()
                if lifecycle in ACTIVE_LIFECYCLE_STATUSES:
                    active.append(setup)
                    counts["active_candidates"] += 1
                    if lifecycle == "ENTRY_TRIGGERED":
                        counts["entry_triggered"] += 1
                    else:
                        counts["pending_entry"] += 1
                else:
                    terminal.append(setup)
                    if lifecycle == "INVALIDATED":
                        counts["invalidated"] += 1
                    elif lifecycle == "EXPIRED":
                        counts["expired"] += 1
                    elif lifecycle == "AMBIGUOUS_INTRABAR_ORDER":
                        counts["ambiguous"] += 1

        return {
            "summary": counts,
            "active": active,
            "terminal": terminal,
            "no_setup": no_setup,
            "errors": errors,
        }

    def scan(self, request: DailyTraderScanRequest | None = None) -> dict[str, Any]:
        scan_request = request or DailyTraderScanRequest()
        symbols, selection, radar_by_symbol = self.select_symbols(scan_request)
        batch = self.radar.scan_symbols(symbols, max_workers=min(5, max(1, len(symbols)))) if symbols else {
            "requested": 0,
            "completed": 0,
            "results": [],
        }
        assembled = self._assemble_results(batch.get("results") or [], radar_by_symbol)
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo("America/New_York"))
        radar_dates = [str(row.get("as_of")) for row in radar_by_symbol.values() if row.get("as_of")]
        return {
            "scan_id": str(uuid4()),
            "scan_timestamp": now.astimezone(ZoneInfo("America/New_York")).isoformat(timespec="seconds"),
            "market_status": self._market_status(now),
            "mode": scan_request.mode,
            "read_only": True,
            "orders_submitted": 0,
            "radar_as_of": max(radar_dates) if radar_dates else None,
            "selection": selection,
            "scanner": {
                "requested": int(batch.get("requested") or 0),
                "completed": int(batch.get("completed") or 0),
                "failed": len(assembled["errors"]),
            },
            **assembled,
        }

    def symbol(self, symbol: str) -> dict[str, Any]:
        normalized = str(symbol or "").strip().upper()
        if not normalized or normalized == "ALL":
            raise ValueError("A single stock symbol is required")
        payload = self.radar.scan_symbol(normalized)
        radar = self.radar.get(normalized)
        assembled = self._assemble_results(
            [{"symbol": normalized, "status": payload.get("scanner_status"), "signals": payload.get("signals")}],
            {normalized: radar or {}},
        )
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo("America/New_York"))
        return {
            "scan_id": str(uuid4()),
            "scan_timestamp": now.astimezone(ZoneInfo("America/New_York")).isoformat(timespec="seconds"),
            "market_status": self._market_status(now),
            "symbol": normalized,
            "radar": self._radar_context(radar),
            "scanner_status": payload.get("scanner_status", "NO_SETUP"),
            "read_only": True,
            "orders_submitted": 0,
            **assembled,
        }

    def health(self) -> dict[str, Any]:
        rows = self._radar_rows()
        radar_enabled = bool(getattr(self.radar.settings, "enabled", True))
        radar_available = radar_enabled and bool(rows)
        scanner_available = self.radar.scanner_runner is not None
        return {
            "status": "ok" if radar_available and scanner_available else "degraded",
            "read_only": True,
            "orders_submitted": 0,
            "radar_available": radar_available,
            "scanner_available": scanner_available,
            "latest_radar_as_of": max((str(row.get("as_of")) for row in rows if row.get("as_of")), default=None),
        }
