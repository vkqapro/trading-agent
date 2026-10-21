"""Application service for Trend Radar.

The service coordinates universe loading, persisted daily candles, pure
calculations, and snapshot persistence.  It intentionally has no broker or
order-manager dependency.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

from src.config import SETTINGS
from src.data.bar_store import index_snapshot, load_bars
from src.symbol_universe import load_stock_symbols
from src.storage.trend_radar_store import TrendRadarStore


_FINALIZED_LIFECYCLE_STATUSES = {"INVALIDATED", "EXPIRED", "AMBIGUOUS_INTRABAR_ORDER"}

from .engine import analyze_daily_bars
from .models import RadarResult, RadarScannerResult, RadarSettings


BarsLoader = Callable[[str, str], pd.DataFrame]
UniverseLoader = Callable[[], Iterable[str]]
ScannerRunner = Callable[[str], dict[str, Any]]


def source_observation_id(
    *,
    symbol: str,
    radar_as_of: str,
    scan_timestamp: str,
    payload: Mapping[str, Any],
) -> str:
    """Return a stable identity for one persisted scanner observation.

    This is created only at the authoritative Radar storage boundary when the
    scanner does not provide its own run ID.  The canonical observation body
    makes it traceable to the symbol, Radar snapshot, scan time, and scanner
    response without treating any one timestamp as an identity.
    """
    body = {
        "symbol": str(symbol).strip().upper(),
        "radar_as_of": str(radar_as_of),
        "scan_timestamp": str(scan_timestamp),
        "payload": dict(payload),
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    return "scanner-observation-" + digest[:32]


def direction_conflict(radar_trend: str | None, scanner_side: str | None) -> bool:
    """Return whether a scanner side opposes a directional Radar regime.

    Radar GREEN/RED are the only directional regimes. GREY is intentionally
    neutral. The function is presentation metadata only; it never changes
    scanner status, candidate creation, approval, or execution.
    """
    trend = str(radar_trend or "").strip().upper()
    side = str(scanner_side or "").strip().upper()
    return (trend == "GREEN" and side == "SHORT") or (trend == "RED" and side == "LONG")


class RadarService:
    def __init__(
        self,
        *,
        store: TrendRadarStore | None = None,
        settings: RadarSettings | None = None,
        bars_loader: BarsLoader | None = None,
        universe_loader: UniverseLoader | None = None,
        scanner_runner: ScannerRunner | None = None,
    ) -> None:
        self.settings = settings or getattr(SETTINGS, "radar", RadarSettings())
        self.store = store or TrendRadarStore(getattr(self.settings, "database_path", SETTINGS.decision_agent.database_path))
        self.bars_loader = bars_loader or load_bars
        self.universe_loader = universe_loader or self._default_universe
        self.scanner_runner = scanner_runner

    def _default_universe(self) -> list[str]:
        configured = list(load_stock_symbols())
        indexed = list(index_snapshot().keys())
        symbols = sorted({str(item).strip().upper() for item in [*configured, *indexed] if str(item).strip()})
        return symbols[: self.settings.max_universe_size]

    def universe(self) -> list[str]:
        return sorted({str(item).strip().upper() for item in self.universe_loader() if str(item).strip()})[: self.settings.max_universe_size]

    def refresh(self) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        results: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        if not self.settings.enabled:
            return {"enabled": False, "results": [], "skipped": [], "updated_at": started.isoformat()}

        universe = self.universe()
        for symbol in universe:
            try:
                bars = self.bars_loader(symbol, "daily")
                result = analyze_daily_bars(symbol, bars, previous_trend=None, settings=self.settings)
                if result is None:
                    skipped.append({"symbol": symbol, "reason": "insufficient_history"})
                    continue
                if result.avg_dollar_volume < self.settings.min_avg_dollar_volume:
                    skipped.append({"symbol": symbol, "reason": "illiquid"})
                    continue
                if (result.price or 0) < self.settings.min_price:
                    skipped.append({"symbol": symbol, "reason": "invalid_price"})
                    continue
                previous = self.store.previous_trend(symbol, result.as_of)
                if previous is not None:
                    result = analyze_daily_bars(symbol, bars, previous_trend=previous, settings=self.settings)
                if result is None:
                    skipped.append({"symbol": symbol, "reason": "calculation_failed"})
                    continue
                self.store.upsert(result)
                results.append(result.to_dict())
            except Exception as exc:  # one bad symbol must not abort a broad scan
                skipped.append({"symbol": symbol, "reason": f"error:{type(exc).__name__}"})

        return {
            "enabled": True,
            "results": sorted(results, key=lambda row: (-float(row.get("radar_score") or 0), str(row.get("symbol")))),
            "skipped": skipped,
            "universe_size": len(universe),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def list_results(self, *, limit: int = 1000, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        selected = filters or {}
        # The table does not need to hydrate legacy prices for every symbol.
        # Selected-detail reads call ``get()``, which backfills one snapshot
        # from the authoritative closed daily bar when necessary.
        results = self.store.latest_results(limit=max(limit, 1000))
        symbol = str(selected.get("symbol") or "").strip().upper()
        trend = str(selected.get("trend") or "").strip().upper()
        direction = str(selected.get("breakout_direction") or "").strip().upper()
        min_radar = float(selected.get("min_radar_score") or 0)
        min_trend = float(selected.get("min_trend_score") or 0)
        breakout = selected.get("breakout")
        flip = selected.get("trend_flip")
        hydrated_by_as_of: dict[str, dict[str, RadarScannerResult]] = {}
        rows: list[dict[str, Any]] = []
        for result in results:
            if symbol and result.symbol != symbol:
                continue
            if trend and result.trend != trend:
                continue
            if direction and result.breakout_direction != direction:
                continue
            if result.radar_score < min_radar or result.trend_score < min_trend:
                continue
            if breakout is not None and bool(result.breakout) != bool(breakout):
                continue
            if flip is not None and bool(result.trend_flip) != bool(flip):
                continue
            row = result.to_dict()
            if result.as_of not in hydrated_by_as_of:
                symbols = [item.symbol for item in results if item.as_of == result.as_of]
                hydrated_by_as_of[result.as_of] = self.store.latest_scanner_results(result.as_of, symbols)
            scanner_result = hydrated_by_as_of[result.as_of].get(result.symbol)
            if scanner_result is not None:
                row.update(
                    {
                        "scanner_status": scanner_result.scanner_status,
                        "scanner_label": scanner_result.scanner_label,
                        "scanner_strategy": scanner_result.strategy,
                        "scanner_side": scanner_result.side,
                        "scanner_signals_found": scanner_result.signals_found,
                        "scanner_scan_timestamp": scanner_result.scan_timestamp,
                        "scanner_source_scan_id": scanner_result.source_scan_id,
                        "scanner_error": scanner_result.error,
                        "scanner_result": scanner_result.to_dict(),
                    }
                )
            rows.append(row)
        return rows[: max(1, min(int(limit), 5000))]

    def get(self, symbol: str) -> dict[str, Any] | None:
        result = self.store.latest(str(symbol).upper())
        result = self._with_closed_price(result) if result else None
        return result.to_dict() if result else None

    def _with_closed_price(self, result: RadarResult) -> RadarResult:
        """Backfill legacy snapshots that predate serialized ``price``.

        The value is read from the same daily-bar authority used by the Radar
        engine, at the persisted snapshot date. It is never a live quote and
        does not recalculate or alter any Radar score.
        """
        if result.price is not None:
            try:
                if pd.notna(float(result.price)) and float(result.price) > 0:
                    return result
            except (TypeError, ValueError):
                pass
        try:
            bars = self.bars_loader(result.symbol, "daily")
            if not isinstance(bars, pd.DataFrame) or bars.empty or "close" not in bars or "date" not in bars:
                return result
            dates = pd.to_datetime(bars["date"], errors="coerce").dt.date.astype("string")
            closes = pd.to_numeric(bars["close"], errors="coerce")
            matches = closes[(dates == result.as_of) & closes.notna() & (closes > 0)]
            if matches.empty:
                return result
            enriched = replace(result, price=round(float(matches.iloc[-1]), 4))
            self.store.upsert(enriched)
            return enriched
        except Exception:
            return result

    def scan_symbol(self, symbol: str) -> dict[str, Any]:
        if self.scanner_runner is None:
            raise RuntimeError("authoritative scanner is not configured")
        normalized = str(symbol or "").strip().upper()
        if not normalized or normalized == "ALL":
            raise ValueError("A single stock symbol is required")
        radar_result = self.store.latest(normalized)
        scan_timestamp = datetime.now(timezone.utc).isoformat()
        try:
            payload = self.scanner_runner(normalized)
        except Exception as exc:
            self._persist_scanner_payload(
                normalized,
                radar_result,
                {
                    "scanner_status": "ERROR",
                    "signals": [],
                    "signals_found": 0,
                    "scan_timestamp": scan_timestamp,
                    "error": str(exc) or type(exc).__name__,
                },
            )
            raise
        if not isinstance(payload, dict):
            self._persist_scanner_payload(
                normalized,
                radar_result,
                {
                    "scanner_status": "ERROR",
                    "signals": [],
                    "signals_found": 0,
                    "scan_timestamp": scan_timestamp,
                    "error": "authoritative scanner returned an invalid response",
                },
            )
            raise RuntimeError("authoritative scanner returned an invalid response")
        signals = payload.get("signals") or []
        payload = dict(payload)
        radar_trend = radar_result.trend if radar_result else None
        annotated_signals: list[dict[str, Any]] = []
        for signal in signals:
            if not isinstance(signal, dict):
                annotated_signals.append(signal)
                continue
            annotated = dict(signal)
            side = annotated.get("side") or annotated.get("direction")
            annotated["direction_conflict"] = direction_conflict(radar_trend, side)
            annotated_signals.append(annotated)
        payload["signals"] = annotated_signals
        lifecycle_statuses = {
            str(signal.get("lifecycle_status") or "").upper()
            for signal in annotated_signals
            if isinstance(signal, dict)
        }
        if not annotated_signals:
            payload["scanner_status"] = "NO_SETUP"
        elif lifecycle_statuses and lifecycle_statuses.issubset(_FINALIZED_LIFECYCLE_STATUSES):
            payload["scanner_status"] = (
                next(iter(lifecycle_statuses))
                if len(lifecycle_statuses) == 1
                else "FINALIZED"
            )
        else:
            payload["scanner_status"] = "CANDIDATE"
        payload["radar_symbol"] = normalized
        payload["radar_trend"] = radar_trend
        payload.setdefault("scan_timestamp", scan_timestamp)
        source_scan_id = self._persist_scanner_payload(normalized, radar_result, payload)
        if source_scan_id:
            payload["source_scan_id"] = source_scan_id
            payload["signals"] = [
                {**signal, "source_scan_id": signal.get("source_scan_id") or source_scan_id}
                if isinstance(signal, dict)
                else signal
                for signal in annotated_signals
            ]
        return payload

    @staticmethod
    def _scanner_identity(payload: dict[str, Any]) -> tuple[str | None, str | None, str]:
        signals = payload.get("signals") or []
        first = signals[0] if signals and isinstance(signals[0], dict) else {}
        strategy = str(first.get("strategy") or "").strip().upper() or None
        side = str(first.get("side") or first.get("direction") or "").strip().upper() or None
        status = str(payload.get("scanner_status") or "").strip().upper()
        label = " ".join(item for item in (strategy, side) if item)
        if not label:
            label = status or "NO_SETUP"
        lifecycle = str(first.get("lifecycle_status") or "").strip().upper()
        if lifecycle in _FINALIZED_LIFECYCLE_STATUSES:
            label = " · ".join(item for item in (label, lifecycle) if item)
        return strategy, side, label

    def _persist_scanner_payload(
        self,
        symbol: str,
        radar_result: RadarResult | None,
        payload: dict[str, Any],
    ) -> str | None:
        if radar_result is None:
            return None
        status = str(payload.get("scanner_status") or "ERROR").strip().upper()
        scan_timestamp = str(payload.get("scan_timestamp") or datetime.now(timezone.utc).isoformat())
        strategy, side, label = self._scanner_identity(payload)
        source_scan_id = str(payload.get("source_scan_id") or payload.get("scan_id") or "").strip()
        if not source_scan_id:
            source_scan_id = source_observation_id(
                symbol=symbol,
                radar_as_of=radar_result.as_of,
                scan_timestamp=scan_timestamp,
                payload=payload,
            )
        persisted_payload = dict(payload)
        persisted_payload["source_scan_id"] = source_scan_id
        persisted_payload["signals"] = [
            {**signal, "source_scan_id": signal.get("source_scan_id") or source_scan_id}
            if isinstance(signal, dict)
            else signal
            for signal in (payload.get("signals") or [])
        ]
        self.store.upsert_scanner_result(
            RadarScannerResult(
                symbol=symbol,
                radar_as_of=radar_result.as_of,
                source_scan_id=source_scan_id,
                scan_timestamp=scan_timestamp,
                scanner_status=status,
                scanner_label=str(payload.get("scanner_label") or label),
                strategy=strategy,
                side=side,
                signals_found=int(payload.get("signals_found") or len(payload.get("signals") or [])),
                payload=persisted_payload,
                error=str(payload.get("error")) if payload.get("error") else None,
            )
        )
        return source_scan_id

    def scan_symbols(self, symbols: Iterable[str], *, max_workers: int = 3) -> dict[str, Any]:
        """Run a bounded batch through the same authoritative scanner callback.

        The callback is the only scanner integration point.  A worker failure is
        converted to that symbol's ``ERROR`` result so other symbols continue;
        this method never imports or calls broker/order code.
        """
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in symbols:
            symbol = str(raw or "").strip().upper()
            if not symbol or symbol == "ALL" or symbol in seen:
                continue
            seen.add(symbol)
            normalized.append(symbol)
        if not normalized:
            raise ValueError("At least one stock symbol is required")
        normalized = normalized[:100]
        workers = max(1, min(int(max_workers), 5, len(normalized)))

        def run_one(symbol: str) -> dict[str, Any]:
            try:
                payload = self.scan_symbol(symbol)
                signals = payload.get("signals") or []
                first = signals[0] if signals else {}
                strategy = str(first.get("strategy") or "").strip().upper()
                side = str(first.get("side") or first.get("direction") or "").strip().upper()
                lifecycle = str(first.get("lifecycle_status") or "").strip().upper()
                label = " ".join(item for item in (strategy, side) if item) or ("CANDIDATE" if signals else "NO_SETUP")
                if lifecycle in _FINALIZED_LIFECYCLE_STATUSES:
                    label = " · ".join(item for item in (label, lifecycle) if item)
                return {
                    "symbol": symbol,
                    "status": payload.get("scanner_status", "NO_SETUP"),
                    "label": label,
                    "signals_found": int(payload.get("signals_found") or len(signals)),
                    "signals": signals,
                    "scanner": payload,
                }
            except Exception as exc:  # isolate one symbol from the batch
                return {
                    "symbol": symbol,
                    "status": "ERROR",
                    "label": "ERROR",
                    "signals_found": 0,
                    "signals": [],
                    "error": str(exc) or type(exc).__name__,
                }

        results_by_symbol: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="trend-radar-scan") as executor:
            futures = {executor.submit(run_one, symbol): symbol for symbol in normalized}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    results_by_symbol[symbol] = future.result()
                except Exception as exc:  # defensive: run_one already isolates expected failures
                    results_by_symbol[symbol] = {
                        "symbol": symbol,
                        "status": "ERROR",
                        "label": "ERROR",
                        "signals_found": 0,
                        "signals": [],
                        "error": str(exc) or type(exc).__name__,
                    }
        results = [results_by_symbol[symbol] for symbol in normalized]
        return {
            "requested": len(normalized),
            "completed": len(results),
            "max_workers": workers,
            "results": results,
            "statuses": {item["symbol"]: item["status"] for item in results},
            "orders_submitted": 0,
        }
