"""Read-only Daily Trader orchestration over Trend Radar and the production scanner.

This module is deliberately an adapter, not another scanner.  Radar rows are
read from the existing ``RadarService`` and scanner results are obtained only
through its injected authoritative scanner callback.  No order, broker, or
execution module is imported here.
"""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
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
VALID_SETUP_SIDES = {"LONG", "SHORT"}


@dataclass(frozen=True)
class DailyTraderScanRequest:
    """Validated, intentionally small request contract for scheduled scans."""

    mode: str = "scheduled"
    top_n: int = 10
    include_breakouts: bool = True
    include_fresh_flips: bool = True
    max_symbols: int = 30
    idempotency_key: str | None = None
    trigger_source: str = "manual"
    # Internal orchestration handoff. Public request parsing intentionally
    # does not expose this escape hatch; the dashboard uses it only after
    # validating the authoritative RADAR_SETUPS universe.
    symbols_override: tuple[str, ...] = ()
    scanner_timeout_seconds: float | None = None

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

        idempotency_key = str(values.get("idempotency_key") or "").strip() or None
        trigger_source = str(values.get("trigger_source") or "manual").strip().lower() or "manual"
        if trigger_source not in {"manual", "scheduled"}:
            raise ValueError("trigger_source must be manual or scheduled")

        return cls(
            mode=mode,
            top_n=integer("top_n", cls.top_n, 0, 100),
            include_breakouts=boolean("include_breakouts", cls.include_breakouts),
            include_fresh_flips=boolean("include_fresh_flips", cls.include_fresh_flips),
            max_symbols=integer("max_symbols", cls.max_symbols, 1, 100),
            idempotency_key=idempotency_key,
            trigger_source=trigger_source,
        )

    def normalized_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "top_n": self.top_n,
            "include_breakouts": self.include_breakouts,
            "include_fresh_flips": self.include_fresh_flips,
            "max_symbols": self.max_symbols,
            "trigger_source": self.trigger_source,
        }

    def request_key(self) -> str:
        payload = dict(self.normalized_payload())
        payload.pop("trigger_source", None)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


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

    @staticmethod
    def _latest_as_of(rows: Iterable[Mapping[str, Any]]) -> str:
        return max((str(row.get("as_of") or "") for row in rows), default="")

    def _current_radar_rows(self) -> list[dict[str, Any]]:
        rows = self._radar_rows()
        latest_as_of = self._latest_as_of(rows)
        if not latest_as_of:
            return []
        return [row for row in rows if str(row.get("as_of") or "") == latest_as_of]

    def select_symbols(self, request: DailyTraderScanRequest) -> tuple[list[str], dict[str, Any], dict[str, dict[str, Any]]]:
        """Select from the latest persisted Radar snapshot without recalculating it."""
        rows = sorted(self._current_radar_rows(), key=self._radar_sort_key)
        if request.symbols_override:
            current = {str(row.get("symbol") or "").strip().upper(): row for row in rows}
            selected = []
            by_symbol = {}
            for raw_symbol in request.symbols_override:
                symbol = str(raw_symbol or "").strip().upper()
                if symbol and symbol in current and symbol not in by_symbol:
                    by_symbol[symbol] = current[symbol]
                    selected.append(symbol)
            return selected, {
                "top_n": request.top_n,
                "top": 0,
                "breakouts": 0,
                "fresh_flips": 0,
                "unique_symbols": len(selected),
                "max_symbols": len(selected),
                "universe_source": "RADAR_SETUPS",
            }, by_symbol
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
            "trend_flip_date",
            "trend_flip_age_days",
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
            "scanner_status": signal.get("scanner_status"),
            "scanner_label": signal.get("scanner_label"),
            "scan_timestamp": signal.get("scan_timestamp"),
            "source_scan_id": signal.get("source_scan_id"),
            "lifecycle_status": lifecycle,
            "lifecycle_reason": signal.get("lifecycle_reason"),
            "entry_triggered": signal.get("entry_triggered"),
            "level": signal.get("level_price"),
            "level_type": signal.get("level_type"),
            "planned_entry": signal.get("planned_entry_price"),
            "planned_entry_price": signal.get("planned_entry_price"),
            "entry": signal.get("entry_price"),
            "entry_price": signal.get("entry_price"),
            "stop": signal.get("stop_price"),
            "stop_price": signal.get("stop_price"),
            "target": signal.get("take_profit_price"),
            "take_profit_price": signal.get("take_profit_price"),
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
            # Preserve the authoritative scanner anchors verbatim. The
            # dashboard uses them only to locate the scanner's formation.
            "scanner_signal_id": signal.get("scanner_signal_id") or signal.get("signal_id"),
            "signal_id": signal.get("signal_id"),
            "signal_bar_date": signal.get("signal_bar_date") or signal.get("signal_bar"),
            "entry_day": signal.get("entry_day"),
            "pattern_bar_dates": signal.get("pattern_bar_dates") or [],
            "signal_timestamp": signal.get("signal_timestamp"),
            "setup_timestamp": signal.get("setup_timestamp"),
            "source_bar_timestamp": signal.get("source_bar_timestamp"),
            "signal_timeframe": signal.get("signal_timeframe") or signal.get("timeframe"),
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
        *,
        side: str | None = None,
    ) -> dict[str, Any]:
        side_filter = str(side or "").strip().upper()
        if side_filter and side_filter not in VALID_SETUP_SIDES:
            raise ValueError("side must be LONG or SHORT")
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
                setup_side = str(setup["scanner"].get("side") or "").strip().upper()
                if side_filter and setup_side != side_filter:
                    continue
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

    def radar_rows(self, *, flips_only: bool = False, limit: int = 20) -> dict[str, Any]:
        rows = self._current_radar_rows()
        if flips_only:
            rows = [row for row in rows if row.get("trend_flip") is True]
        rows = sorted(rows, key=self._radar_sort_key)[: max(1, min(int(limit), 100))]
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo("America/New_York"))
        return {
            "scan_timestamp": now.astimezone(ZoneInfo("America/New_York")).isoformat(timespec="seconds"),
            "market_status": self._market_status(now),
            "mode": "fresh_flips" if flips_only else "radar",
            "read_only": True,
            "orders_submitted": 0,
            "radar_as_of": max((str(row.get("as_of")) for row in rows if row.get("as_of")), default=None),
            "count": len(rows),
            "results": [self._radar_context(row) for row in rows],
        }

    def persisted_setups(self, *, side: str | None = None) -> dict[str, Any]:
        rows = self._current_radar_rows()
        radar_by_symbol = {
            str(row.get("symbol") or "").strip().upper(): row
            for row in rows
            if str(row.get("symbol") or "").strip()
        }
        results: list[dict[str, Any]] = []
        for row in rows:
            symbol = str(row.get("symbol") or "").strip().upper()
            scanner_result = row.get("scanner_result")
            if not symbol or not isinstance(scanner_result, Mapping):
                continue
            payload = scanner_result.get("payload")
            payload = dict(payload) if isinstance(payload, Mapping) else {}
            signals: list[dict[str, Any]] = []
            for signal in payload.get("signals") or []:
                if not isinstance(signal, Mapping):
                    continue
                enriched = dict(signal)
                enriched.setdefault("scanner_status", scanner_result.get("scanner_status") or payload.get("scanner_status"))
                enriched.setdefault("scanner_label", scanner_result.get("scanner_label"))
                enriched.setdefault("scan_timestamp", scanner_result.get("scan_timestamp"))
                enriched.setdefault("source_scan_id", scanner_result.get("source_scan_id"))
                signals.append(enriched)
            results.append(
                {
                    "symbol": symbol,
                    "status": scanner_result.get("scanner_status") or payload.get("scanner_status"),
                    "signals": signals,
                    "error": scanner_result.get("error") or payload.get("error"),
                }
            )
        assembled = self._assemble_results(results, radar_by_symbol, side=side)
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo("America/New_York"))
        return {
            "scan_id": "persisted",
            "generated_at": now.astimezone(ZoneInfo("America/New_York")).isoformat(timespec="seconds"),
            "market_status": self._market_status(now),
            "mode": "persisted_setups",
            "side": str(side or "ALL").strip().upper() or "ALL",
            "read_only": True,
            "orders_submitted": 0,
            "radar_as_of": self._latest_as_of(rows) or None,
            "selection": {
                "persisted_scanner_results": len(results),
                "unique_symbols": len({item["symbol"] for item in results}),
                "max_symbols": len(results),
            },
            "scanner": {
                "requested": 0,
                "completed": len(results),
                "failed": len(assembled["errors"]),
            },
            **assembled,
        }

    def scan(self, request: DailyTraderScanRequest | None = None, *, scan_id: str | None = None) -> dict[str, Any]:
        scan_request = request or DailyTraderScanRequest()
        symbols, selection, radar_by_symbol = self.select_symbols(scan_request)
        batch = self.radar.scan_symbols(
            symbols,
            max_workers=min(5, max(1, len(symbols))),
            timeout_seconds=scan_request.scanner_timeout_seconds,
            batch_id=scan_id if scan_request.scanner_timeout_seconds is not None else None,
        ) if symbols else {
            "requested": 0,
            "completed": 0,
            "results": [],
        }
        assembled = self._assemble_results(batch.get("results") or [], radar_by_symbol)
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo("America/New_York"))
        radar_dates = [str(row.get("as_of")) for row in radar_by_symbol.values() if row.get("as_of")]
        radar_as_of = max(radar_dates) if radar_dates else None
        persisted_result_count = 0
        if radar_as_of:
            persisted_result_count = len(self.radar.store.latest_scanner_results(radar_as_of, symbols))
        return {
            "scan_id": str(scan_id or uuid4()),
            "scan_timestamp": now.astimezone(ZoneInfo("America/New_York")).isoformat(timespec="seconds"),
            "market_status": self._market_status(now),
            "mode": scan_request.mode,
            "read_only": True,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
            "radar_as_of": radar_as_of,
            "processed_count": int(batch.get("completed") or 0),
            "persisted_result_count": persisted_result_count,
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
        current_rows = self._current_radar_rows()
        radar_enabled = bool(getattr(self.radar.settings, "enabled", True))
        radar_available = radar_enabled and bool(rows)
        scanner_available = self.radar.scanner_runner is not None
        return {
            "status": "ok" if radar_available and scanner_available else "degraded",
            "read_only": True,
            "orders_submitted": 0,
            "radar_available": radar_available,
            "scanner_available": scanner_available,
            "latest_radar_as_of": self._latest_as_of(rows) or None,
            "current_radar_row_count": len(current_rows),
        }

    def market_report(
        self,
        *,
        dashboard_api: Mapping[str, Any] | None = None,
        production_scanner: Mapping[str, Any] | None = None,
        radar_universe: Mapping[str, Any] | None = None,
        market_screener: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Assemble Daily Trader platform health without running a scan."""
        rows = self._radar_rows()
        current_rows = self._current_radar_rows()
        latest_as_of = self._latest_as_of(rows) or None
        persisted_count = sum(1 for row in current_rows if isinstance(row.get("scanner_result"), Mapping))
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo("America/New_York"))
        warnings: list[str] = []
        if not rows:
            warnings.append("RADAR_EMPTY")
        if rows and not current_rows:
            warnings.append("RADAR_CURRENT_SNAPSHOT_EMPTY")
        if latest_as_of is None:
            warnings.append("RADAR_AS_OF_MISSING")
        if persisted_count == 0:
            warnings.append("PERSISTED_SCANNER_RESULTS_EMPTY")

        scanner_available = bool((production_scanner or {}).get("scanner_available", self.radar.scanner_runner is not None))
        if not scanner_available:
            warnings.append("PRODUCTION_SCANNER_UNAVAILABLE")
        radar_universe_valid = bool((radar_universe or {}).get("valid"))
        if radar_universe and not radar_universe_valid:
            warnings.append(str(radar_universe.get("reason_code") or "RADAR_SETUPS_UNAVAILABLE"))

        dashboard_status = dict(dashboard_api or {"status": "ok", "api_available": True})
        screener_status = dict(market_screener or production_scanner or {})
        return {
            "status": "ok" if dashboard_status.get("api_available", True) and latest_as_of and scanner_available else "degraded",
            "read_only": True,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
            "generated_at": now.astimezone(ZoneInfo("America/New_York")).isoformat(timespec="seconds"),
            "market_session": self._market_status(now),
            "dashboard_api": dashboard_status,
            "radar": {
                "available": bool(rows),
                "as_of": latest_as_of,
                "current_row_count": len(current_rows),
                "total_latest_symbol_rows": len(rows),
            },
            "production_scanner": dict(production_scanner or {"scanner_available": self.radar.scanner_runner is not None}),
            "persisted_scanner_results": {
                "available": persisted_count > 0,
                "current_result_count": persisted_count,
                "radar_as_of": latest_as_of,
            },
            "radar_setups": {
                "available": bool(radar_universe and radar_universe.get("available")),
                "valid": radar_universe_valid,
                "eligible_count": int((radar_universe or {}).get("eligible_symbol_count") or 0),
                "reason_code": (radar_universe or {}).get("reason_code"),
                "snapshot_as_of": (radar_universe or {}).get("snapshot_as_of"),
            },
            "market_screener": screener_status,
            "warnings": warnings,
        }


class DailyTraderScanJobManager:
    """Persistent async wrapper around the existing synchronous scan path."""

    def __init__(
        self,
        *,
        service_factory: Callable[[], DailyTraderService],
        max_workers: int = 1,
    ) -> None:
        self._service_factory = service_factory
        self._executor = ThreadPoolExecutor(max_workers=max(1, int(max_workers)), thread_name_prefix="daily-trader-scan")
        self._lock = threading.Lock()
        self._reconciled = False

    def _store(self):
        return self._service_factory().radar.store

    @staticmethod
    def _status_payload(job: Mapping[str, Any]) -> dict[str, Any]:
        request = job.get("request") if isinstance(job.get("request"), Mapping) else {}
        result = job.get("result") if isinstance(job.get("result"), Mapping) else None
        error = job.get("error") if isinstance(job.get("error"), Mapping) else None
        payload = {
            "scan_id": job.get("scan_id"),
            "status": job.get("status"),
            "radar_as_of": job.get("radar_as_of") or (result or {}).get("radar_as_of"),
            "mode": request.get("mode"),
            "top_n": request.get("top_n"),
            "max_symbols": request.get("max_symbols"),
            "include_breakouts": request.get("include_breakouts"),
            "include_fresh_flips": request.get("include_fresh_flips"),
            "trigger_source": job.get("trigger_source") or request.get("trigger_source"),
            "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
            "processed_count": int(job.get("processed_count") or (result or {}).get("processed_count") or 0),
            "persisted_result_count": int(job.get("persisted_result_count") or (result or {}).get("persisted_result_count") or 0),
            "error": error,
            "read_only": True,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
        }
        if result is not None:
            payload["result"] = result
        return payload

    def _reconcile_once(self) -> None:
        if self._reconciled:
            return
        with self._lock:
            if not self._reconciled:
                self._store().reconcile_interrupted_daily_trader_scan_jobs()
                self._reconciled = True

    def submit(self, request: DailyTraderScanRequest) -> dict[str, Any]:
        self._reconcile_once()
        service = self._service_factory()
        symbols, _selection, radar_by_symbol = service.select_symbols(request)
        radar_as_of = service._latest_as_of(radar_by_symbol.values()) or None
        scan_id = str(uuid4())
        with self._lock:
            job, created = service.radar.store.create_or_reuse_daily_trader_scan_job(
                scan_id=scan_id,
                request_key=request.request_key(),
                request=request.normalized_payload(),
                idempotency_key=request.idempotency_key,
                trigger_source=request.trigger_source,
                radar_as_of=radar_as_of,
            )
            if created:
                self._executor.submit(self._run, str(job["scan_id"]), request)
        return self._status_payload(job)

    def status(self, scan_id: str) -> dict[str, Any] | None:
        self._reconcile_once()
        job = self._store().get_daily_trader_scan_job(str(scan_id))
        if job is None:
            return None
        return self._status_payload(job)

    def latest_completed(self) -> dict[str, Any] | None:
        job = self._store().get_latest_completed_daily_trader_scan_job()
        if job is None:
            return None
        return self._status_payload(job)

    def _run(self, scan_id: str, request: DailyTraderScanRequest) -> None:
        service = self._service_factory()
        store = service.radar.store
        store.mark_daily_trader_scan_job_running(scan_id)
        try:
            result = service.scan(request, scan_id=scan_id)
            store.complete_daily_trader_scan_job(
                scan_id,
                result=result,
                processed_count=int(result.get("processed_count") or 0),
                persisted_result_count=int(result.get("persisted_result_count") or 0),
                radar_as_of=result.get("radar_as_of"),
            )
        except Exception as exc:
            store.fail_daily_trader_scan_job(
                scan_id,
                error={
                    "reason_code": "DAILY_TRADER_SCAN_FAILED",
                    "type": type(exc).__name__,
                    "message": str(exc) or type(exc).__name__,
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
