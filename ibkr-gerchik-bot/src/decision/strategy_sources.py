"""Source-neutral strategy snapshots for Decision Lab.

This module is deliberately an adapter layer.  It does not place orders and it
does not fetch candles from a broker.  Sources consume the completed scan
context or bars already persisted by the existing market-data workflow.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

from dashboard import data_access as da
from dashboard_react.market_screener import ScreenerParams, run_market_screener
from src.config import SETTINGS
from src.data.chart_history import daily_bars_from_intraday
from src.decision.candidate_adapter import mapping_to_candidate, trade_signal_to_candidate
from src.decision.models import DecisionCandidate
from src.strategy.signal_models import TradeSignal


STRATEGY_SOURCE_IDS = (
    "stock_screener",
    "bmsb",
    "gaussian",
    "gerchik_router",
)


@dataclass(frozen=True)
class StrategySourceInfo:
    source_id: str
    display_name: str
    description: str


@dataclass(frozen=True)
class StrategySnapshot:
    """Canonical, source-neutral evidence for one symbol and completed scan."""

    scan_id: str
    source: str
    symbol: str
    strategy: str
    signal_state: str
    candidate_class: str
    price: float | None = None
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    reward_risk: float | None = None
    atr: float | None = None
    score: float | None = None
    source_timestamp: str | None = None
    bar_timestamp: str | None = None
    direction: str = "none"
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "scan_id": self.scan_id,
            "source": self.source,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "signal_state": self.signal_state,
            "candidate_class": self.candidate_class,
            "price": self.price,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "reward_risk": self.reward_risk,
            "atr": self.atr,
            "score": self.score,
            "source_timestamp": self.source_timestamp,
            "bar_timestamp": self.bar_timestamp,
            "direction": self.direction,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class StrategyScanContext:
    scan_id: str
    completed_at: str
    symbols: tuple[str, ...]
    watchlist: Mapping[str, object]
    scan_result: Mapping[str, object] = field(default_factory=dict)
    bars_by_symbol: Mapping[str, object] = field(default_factory=dict)
    source_metadata: Mapping[str, Mapping[str, object]] = field(default_factory=dict)


SnapshotAdapter = Callable[[StrategyScanContext], list[StrategySnapshot]]


@dataclass(frozen=True)
class StrategySource:
    info: StrategySourceInfo
    snapshot_adapter: SnapshotAdapter


class StrategySourceRegistry:
    """Small extensible registry; adding a source requires one adapter entry."""

    def __init__(self, sources: Sequence[StrategySource] | None = None) -> None:
        self._sources: dict[str, StrategySource] = {}
        for source in sources or default_sources():
            self.register(source)

    def register(self, source: StrategySource) -> None:
        source_id = str(source.info.source_id).strip().lower()
        if not source_id or source_id == "all":
            raise ValueError("strategy source id must be a non-empty source id other than all")
        self._sources[source_id] = replace(
            source,
            info=replace(source.info, source_id=source_id),
        )

    def get(self, source_id: str) -> StrategySource:
        normalized = str(source_id or "").strip().lower()
        try:
            return self._sources[normalized]
        except KeyError as exc:
            raise ValueError(f"Unknown strategy source: {source_id}") from exc

    def ids(self) -> tuple[str, ...]:
        return tuple(self._sources)

    def selection_ids(self) -> tuple[str, ...]:
        """Return selectable IDs, including the synthetic combined view."""
        return ("all", *self.ids())

    def infos(self) -> list[dict[str, str]]:
        return [{
                "id": "all",
                "label": "All",
                "description": "One source-neutral decision per symbol using all completed source evidence.",
            }] + [
            {
                "id": source.info.source_id,
                "label": source.info.display_name,
                "description": source.info.description,
            }
            for source in self._sources.values()
        ]

    def validate_selection(self, source_id: str) -> str:
        normalized = str(source_id or "").strip().lower()
        if normalized not in self.selection_ids():
            raise ValueError(f"Unknown strategy source: {source_id}")
        return normalized

    def snapshots(self, source_id: str, context: StrategyScanContext) -> list[StrategySnapshot]:
        return self.get(source_id).snapshot_adapter(context)

    def all_snapshots(self, context: StrategyScanContext) -> dict[str, list[StrategySnapshot]]:
        return {source_id: self.snapshots(source_id, context) for source_id in self.ids()}

    def all_snapshots_with_status(
        self, context: StrategyScanContext
    ) -> tuple[dict[str, list[StrategySnapshot]], dict[str, str]]:
        """Evaluate every source within one scan boundary with explicit status."""
        snapshots: dict[str, list[StrategySnapshot]] = {}
        statuses: dict[str, str] = {}
        for source_id in self.ids():
            try:
                items = self.snapshots(source_id, context)
            except Exception:
                # Never reuse a prior source result when the current adapter
                # did not complete. The persisted status lets ALL mode and the
                # UI distinguish unavailable evidence from no signal.
                snapshots[source_id] = []
                statuses[source_id] = "SOURCE_UNAVAILABLE"
                continue
            snapshots[source_id] = items
            metadata = context.source_metadata.get(source_id, {})
            if metadata.get("available") is False:
                statuses[source_id] = "SOURCE_UNAVAILABLE"
            else:
                statuses[source_id] = "READY" if items else "NO_CANDIDATES"
        return snapshots, statuses


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) and number == number else None


def _iso(value: object, fallback: str | None = None) -> str | None:
    if value is None:
        return fallback
    return str(value)


def _stock_symbols(context: StrategyScanContext) -> list[str]:
    symbols = {str(symbol).strip().upper() for symbol in context.symbols if str(symbol).strip()}
    symbols.update(str(symbol).strip().upper() for symbol in context.watchlist if str(symbol).strip())
    return sorted(symbols)


SOURCE_DATA_FRESHNESS_SECONDS = 30 * 60
_CURRENT_SOURCE_LOCK = threading.Lock()
_LAST_CURRENT_SOURCE_REFRESH = 0.0


def _parse_source_time(value: object, *, local_zone: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_zone)
    return parsed.astimezone(timezone.utc)


def _current_source_context() -> StrategyScanContext:
    """Read the legacy backend's persisted data without starting a job."""
    from dashboard.data_access import bars_index, load_daily_decisions, load_watchlist
    from src.symbol_universe import load_stock_symbols
    from zoneinfo import ZoneInfo

    watchlist = load_watchlist()
    configured = load_stock_symbols()
    symbols = tuple(sorted({
        str(item).strip().upper()
        for item in (*configured, *(watchlist.keys() if isinstance(watchlist, Mapping) else ()))
        if str(item).strip()
    }))
    local_zone = ZoneInfo(str(SETTINGS.trading_hours.timezone))
    index = bars_index()
    latest_update: datetime | None = None
    latest_bar: datetime | None = None
    has_bars = False
    for symbol in symbols:
        frames = index.get(symbol, {}) if isinstance(index, Mapping) else {}
        if not isinstance(frames, Mapping):
            continue
        for frame in frames.values():
            if not isinstance(frame, Mapping):
                continue
            updated = _parse_source_time(frame.get("updated_at"), local_zone=local_zone)
            bar_time = _parse_source_time(frame.get("last_bar"), local_zone=local_zone)
            if updated is not None:
                has_bars = True
                latest_update = max(latest_update, updated) if latest_update else updated
            if bar_time is not None:
                latest_bar = max(latest_bar, bar_time) if latest_bar else bar_time

    now = datetime.now(timezone.utc)
    bar_source_timestamp = latest_update.isoformat() if latest_update else None
    latest_bar_timestamp = latest_bar.isoformat() if latest_bar else None
    bar_age = max((now - latest_update).total_seconds(), 0.0) if latest_update else None
    bar_metadata = {
        "source_timestamp": bar_source_timestamp,
        "latest_bar_timestamp": latest_bar_timestamp,
        "data_age_seconds": round(bar_age, 3) if bar_age is not None else None,
        "freshness": (
            "FRESH" if bar_age is not None and bar_age <= SOURCE_DATA_FRESHNESS_SECONDS
            else "STALE" if bar_age is not None else "UNKNOWN"
        ),
        "available": has_bars,
    }
    daily_decisions = load_daily_decisions()
    decisions_date = str(daily_decisions.get("date") or "") if isinstance(daily_decisions, Mapping) else ""
    gerchik_available = bool(decisions_date)
    gerchik_timestamp = None
    if isinstance(daily_decisions, Mapping):
        attempts = []
        decisions = daily_decisions.get("decisions", {})
        if isinstance(decisions, Mapping):
            for entry in decisions.values():
                if isinstance(entry, Mapping) and isinstance(entry.get("attempts"), list):
                    attempts.extend(item for item in entry["attempts"] if isinstance(item, Mapping))
        parsed_attempts = [_parse_source_time(item.get("timestamp"), local_zone=local_zone) for item in attempts]
        parsed_attempts = [item for item in parsed_attempts if item is not None]
        if parsed_attempts:
            gerchik_timestamp = max(parsed_attempts).isoformat()
    source_metadata = {
        "stock_screener": dict(bar_metadata),
        "bmsb": dict(bar_metadata),
        "gaussian": dict(bar_metadata),
        "gerchik_router": {
            "source_timestamp": gerchik_timestamp or (f"{decisions_date}T00:00:00+00:00" if decisions_date else None),
            "latest_bar_timestamp": latest_bar_timestamp,
            "data_age_seconds": (
                round((now - _parse_source_time(gerchik_timestamp, local_zone=local_zone)).total_seconds(), 3)
                if gerchik_timestamp else None
            ),
            "freshness": "FRESH" if gerchik_available else "UNKNOWN",
            "available": gerchik_available,
        },
    }
    provenance = json.dumps(source_metadata, sort_keys=True, separators=(",", ":"))
    provisional_id = f"analysis-source-{hashlib.sha256(provenance.encode('utf-8')).hexdigest()[:32]}"
    return StrategyScanContext(
        scan_id=provisional_id,
        completed_at=now.isoformat(),
        symbols=symbols,
        watchlist=watchlist if isinstance(watchlist, Mapping) else {},
        scan_result={},
        bars_by_symbol={},
        source_metadata=source_metadata,
    )


def current_source_snapshot_payload(
    registry: StrategySourceRegistry | None = None,
) -> dict[str, object]:
    """Read and normalize all existing strategy data for Decision Lab."""
    selected_registry = registry or DEFAULT_REGISTRY
    context = _current_source_context()
    snapshots, statuses = selected_registry.all_snapshots_with_status(context)
    fingerprints: dict[str, str] = {}
    normalized: dict[str, list[dict[str, object]]] = {}
    for source, items in snapshots.items():
        canonical = [dict(item.to_dict(), scan_id="") for item in items]
        fingerprints[source] = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24]
    identity = json.dumps(
        {source: {**dict(context.source_metadata.get(source, {})), "fingerprint": fingerprints.get(source)} for source in selected_registry.ids()},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    analysis_snapshot_id = f"analysis-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"
    for source, items in snapshots.items():
        normalized[source] = [dict(replace(item, scan_id=analysis_snapshot_id).to_dict()) for item in items]
    source_metadata: dict[str, dict[str, object]] = {}
    for source in selected_registry.ids():
        metadata = dict(context.source_metadata.get(source, {}))
        metadata.update({
            "fingerprint": fingerprints.get(source),
            "candidate_count": len(normalized.get(source, [])),
            "status": statuses.get(source, "SOURCE_UNAVAILABLE"),
        })
        source_metadata[source] = metadata
    return {
        "analysis_snapshot_id": analysis_snapshot_id,
        "scan_id": analysis_snapshot_id,
        "created_at": context.completed_at,
        "completed_at": context.completed_at,
        "source_counts": {source: len(items) for source, items in normalized.items()},
        "source_status": statuses,
        "source_metadata": source_metadata,
        "sources": normalized,
    }


def refresh_current_source_snapshot(
    *,
    registry: StrategySourceRegistry | None = None,
    force: bool = False,
    minimum_interval_seconds: float = 30.0,
) -> dict[str, object]:
    """Persist current source data; this never calls a trading-session job."""
    global _LAST_CURRENT_SOURCE_REFRESH
    from src.decision.strategy_control import load_snapshot, save_snapshot

    with _CURRENT_SOURCE_LOCK:
        now = time.monotonic()
        if not force and now - _LAST_CURRENT_SOURCE_REFRESH < minimum_interval_seconds:
            existing = load_snapshot()
            if isinstance(existing, dict):
                return existing
        payload = current_source_snapshot_payload(registry)
        save_snapshot(payload)
        _LAST_CURRENT_SOURCE_REFRESH = now
        return payload


def _daily_snapshot_frame(symbol: str) -> pd.DataFrame:
    daily = da.get_bars(symbol, "daily")
    intraday = da.get_bars(symbol, "intraday_5m")
    if intraday is None or intraday.empty:
        return daily
    derived = daily_bars_from_intraday(intraday)
    if derived.empty:
        return daily
    if daily is None or daily.empty:
        return derived
    known_dates = set(pd.to_datetime(daily["date"], errors="coerce").dropna().dt.date)
    missing = derived.loc[~pd.to_datetime(derived["date"], errors="coerce").dt.date.isin(known_dates)]
    return pd.concat([daily, missing], ignore_index=True).sort_values("date") if not missing.empty else daily


def _stock_screener_snapshots(context: StrategyScanContext) -> list[StrategySnapshot]:
    """Reuse the existing Stock Screener implementation over persisted bars."""
    symbols = _stock_symbols(context)
    params = ScreenerParams(
        asset_class="stock",
        timeframe="1D",
        strategies=("LP1", "LP2", "PRB1", "PRB2"),
        side_filter="ALL",
        rr=2.0,
        equity=100_000.0,
        risk_pct=0.005,
    )
    result = run_market_screener(
        symbols=symbols,
        bars_loader=lambda symbol, _timeframe: _daily_snapshot_frame(symbol),
        watchlist=dict(context.watchlist),
        params=params,
    )
    snapshots: list[StrategySnapshot] = []
    for row in result.get("signals", []):
        status = str(row.get("status") or "READY").upper()
        candidate_class = "EXECUTABLE_ENTRY_SIGNAL" if status == "READY" else "WATCH_CANDIDATE"
        entry = _number(row.get("entry_price"))
        stop = _number(row.get("stop_price"))
        target = _number(row.get("take_profit_price"))
        if not all(value is not None and value > 0 for value in (entry, stop, target)):
            candidate_class = "WATCH_CANDIDATE"
        bar_timestamp = _iso(row.get("signal_bar_date"), context.completed_at)
        snapshots.append(
            StrategySnapshot(
                scan_id=context.scan_id,
                source="stock_screener",
                symbol=str(row.get("ticker") or "").upper(),
                strategy=str(row.get("strategy") or "STOCK_SCREENER"),
                signal_state=status,
                candidate_class=candidate_class,
                price=entry,
                entry=entry,
                stop=stop,
                target=target,
                reward_risk=_number(row.get("rr")),
                atr=_number(row.get("atr_clean_14")),
                score=_number(row.get("score")),
                source_timestamp=bar_timestamp,
                bar_timestamp=bar_timestamp,
                direction="long" if str(row.get("side") or "").upper() == "LONG" else "short",
                metadata={
                    "status": status,
                    "entry_triggered": bool(row.get("entry_triggered", False)),
                    "level_price": _number(
                        row.get("level_price", row.get("signal_level"))
                    ) or entry,
                    "risk_per_share": _number(
                        row.get("risk_per_share", row.get("price_risk_per_share"))
                    ),
                    "deterministic_plan": candidate_class == "EXECUTABLE_ENTRY_SIGNAL",
                },
            )
        )
    return snapshots


def _monitor_snapshots(context: StrategyScanContext, source: str) -> list[StrategySnapshot]:
    """Call the existing BMSB/Gaussian monitor calculators; never copy formulas."""
    # These functions currently live in the dashboard server.  They calculate
    # only from persisted dashboard bars and do not create a broker client.
    from dashboard_react.server import _bmsb_scan_symbol, _gaussian_scan_symbol

    today = datetime.fromisoformat(context.completed_at.replace("Z", "+00:00")).date()
    scanner = _bmsb_scan_symbol if source == "bmsb" else _gaussian_scan_symbol
    snapshots: list[StrategySnapshot] = []
    for symbol in _stock_symbols(context):
        row = scanner(symbol, today, include_neutral=True, asset="stock")
        if not isinstance(row, Mapping):
            continue
        signal = str(row.get("signal") or "NO_DATA")
        if signal in {"NO_SIGNAL", "NO_DATA"}:
            continue
        side = str(row.get("side") or "NONE").lower()
        if side == "long":
            direction = "long"
        elif side == "short":
            direction = "short"
        else:
            direction = "none"
        candidate_class = _monitor_candidate_class(source, signal)
        snapshots.append(
            StrategySnapshot(
                scan_id=context.scan_id,
                source=source,
                symbol=str(row.get("symbol") or symbol).upper(),
                strategy="BMSB" if source == "bmsb" else "GAUSSIAN",
                signal_state=signal,
                candidate_class=candidate_class,
                price=_number(row.get("last_price")),
                entry=None,
                stop=None,
                target=None,
                reward_risk=None,
                atr=None,
                score=None,
                source_timestamp=_iso(row.get("cross_date") or row.get("daily_bar"), context.completed_at),
                bar_timestamp=_iso(row.get("weekly_bar") or row.get("daily_bar"), context.completed_at),
                direction=direction,
                metadata={
                    "deterministic_plan": False,
                    "source_execution_capability": "ANALYSIS_ONLY",
                    "execution_eligible": False,
                    "entry_level": _number(row.get("entry_level")),
                    "trigger_level": _number(row.get("trigger_level")),
                    "gap_pct": _number(row.get("gap_pct")),
                },
            )
        )
    return snapshots


def _monitor_candidate_class(source: str, signal: str) -> str:
    """Map monitor semantics without granting the monitor execution authority."""
    normalized_source = str(source or "").strip().lower()
    normalized_signal = str(signal or "").strip().upper()
    if "EXIT" in normalized_signal:
        return "POSITION_MANAGEMENT_SIGNAL"
    if (normalized_source == "gaussian" and normalized_signal == "GAUSSIAN_LONG_ENTRY") or (
        normalized_source == "bmsb" and normalized_signal == "CROSSED_LONG"
    ):
        return "ENTRY_SIGNAL"
    return "WATCH_CANDIDATE"


def _gerchik_snapshots(context: StrategyScanContext) -> list[StrategySnapshot]:
    details = context.scan_result.get("signal_details", [])
    if not isinstance(details, list):
        details = []
    if not details:
        # The legacy router persists its accepted/rejected attempts in the
        # daily decision log.  Consume only accepted deterministic plans; do
        # The application layer reads persisted decisions; it does not execute
        # the existing entry scanner.
        from dashboard.data_access import load_daily_decisions

        persisted = load_daily_decisions()
        decisions = persisted.get("decisions", {}) if isinstance(persisted, Mapping) else {}
        if isinstance(decisions, Mapping):
            for symbol, entry in decisions.items():
                if not isinstance(entry, Mapping) or not isinstance(entry.get("attempts"), list):
                    continue
                for attempt in entry["attempts"]:
                    if not isinstance(attempt, Mapping):
                        continue
                    signal = str(attempt.get("signal") or "NONE").upper()
                    if signal in {"", "NONE", "NO_SIGNAL"}:
                        continue
                    details.append({
                        "symbol": symbol,
                        "strategy": attempt.get("strategy") or "GERCHIK_ROUTER",
                        "signal": signal,
                        "entry": attempt.get("entry"),
                        "stop": attempt.get("stop"),
                        "target": attempt.get("target"),
                        "confidence": attempt.get("confidence"),
                        "signal_timestamp": attempt.get("timestamp"),
                        "source_bar_timestamp": attempt.get("timestamp"),
                    })
    snapshots: list[StrategySnapshot] = []
    for row in details:
        if not isinstance(row, Mapping):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        status = str(row.get("status") or "available").upper()
        entry, stop, target = (_number(row.get(key)) for key in ("entry", "stop", "target"))
        executable = all(value is not None and value > 0 for value in (entry, stop, target))
        candidate_class = "EXECUTABLE_ENTRY_SIGNAL" if executable else "WATCH_CANDIDATE"
        source_timestamp = _iso(row.get("source_bar_timestamp") or row.get("signal_timestamp"), context.completed_at)
        snapshots.append(
            StrategySnapshot(
                scan_id=context.scan_id,
                source="gerchik_router",
                symbol=symbol,
                strategy=str(row.get("strategy") or "GERCHIK_ROUTER"),
                signal_state=str(row.get("signal") or "SIGNAL"),
                candidate_class=candidate_class,
                price=entry,
                entry=entry,
                stop=stop,
                target=target,
                reward_risk=_number(row.get("reward_risk")),
                atr=_number(row.get("atr")),
                score=_number(row.get("confidence")),
                source_timestamp=source_timestamp,
                bar_timestamp=source_timestamp,
                direction=str(row.get("direction") or "none").lower(),
                metadata={
                    "status": status,
                    "deterministic_plan": executable,
                    "signal_level": _number(row.get("signal_level")),
                    "signal_level_type": row.get("signal_level_type"),
                },
            )
        )
    return snapshots


def _source_signal_id(snapshot: StrategySnapshot) -> str:
    raw = "|".join(
        str(value)
        for value in (
            snapshot.source,
            snapshot.scan_id,
            snapshot.symbol,
            snapshot.strategy,
            snapshot.signal_state,
            snapshot.bar_timestamp or snapshot.source_timestamp,
        )
    )
    return f"{snapshot.source}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def snapshot_to_candidate(snapshot: StrategySnapshot) -> tuple[DecisionCandidate, tuple[str, ...]]:
    """Adapt one snapshot without inventing a trade plan."""
    source_signal_id = _source_signal_id(snapshot)
    source_execution_eligible = snapshot.metadata.get("execution_eligible")
    execution_eligible = (
        bool(source_execution_eligible)
        if source_execution_eligible is not None
        else snapshot.candidate_class == "EXECUTABLE_ENTRY_SIGNAL"
    )
    metadata = {
        **dict(snapshot.metadata),
        "strategy_source": snapshot.source,
        "strategy_name": snapshot.strategy,
        "source_signal": snapshot.signal_state,
        "scan_id": snapshot.scan_id,
        "source_timestamp": snapshot.source_timestamp,
        "bar_timestamp": snapshot.bar_timestamp,
        "candidate_class": snapshot.candidate_class,
        "execution_eligible": execution_eligible,
    }
    if snapshot.candidate_class == "EXECUTABLE_ENTRY_SIGNAL":
        payload = {
            "symbol": snapshot.symbol,
            "strategy": snapshot.strategy,
            "direction": snapshot.direction,
            "level_price": _number(
                snapshot.metadata.get("level_price", snapshot.metadata.get("signal_level"))
            ),
            "level_type": snapshot.metadata.get("signal_level_type", "source_level"),
            "entry": snapshot.entry,
            "stop": snapshot.stop,
            "target": snapshot.target,
            "atr": snapshot.atr,
            "reward_risk": snapshot.reward_risk,
            "risk_per_share": _number(snapshot.metadata.get("risk_per_share"))
            or (abs(float(snapshot.entry) - float(snapshot.stop)) if snapshot.entry and snapshot.stop else None),
            "confidence": snapshot.score,
            "signal_timestamp": snapshot.source_timestamp,
        }
        candidate = mapping_to_candidate(
            payload,
            asset_class="stock",
            source_signal_id=source_signal_id,
            market_context={"strategy_source": snapshot.source, "scan_id": snapshot.scan_id},
        )
        return replace(candidate, metadata={**candidate.metadata, **metadata}), ("ENTER", "WAIT", "REJECT")

    # Analysis-only sources use a neutral non-trading candidate.  ENTER is
    # excluded at the request contract, so these values can never reach risk
    # or execution as an invented trade plan.
    created_at = datetime.now(timezone.utc)
    try:
        if snapshot.source_timestamp:
            parsed = datetime.fromisoformat(snapshot.source_timestamp.replace("Z", "+00:00"))
            created_at = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    digest = hashlib.sha256(source_signal_id.encode("utf-8")).hexdigest()[:32]
    candidate = DecisionCandidate(
        candidate_id=f"analysis-{digest}",
        created_at=created_at,
        asset_class="stock",
        symbol=snapshot.symbol,
        strategy=snapshot.strategy,
        direction=snapshot.direction,
        entry=0.0,
        stop=0.0,
        target=0.0,
        source_signal_id=source_signal_id,
        market_context={"strategy_source": snapshot.source, "scan_id": snapshot.scan_id},
        metadata=metadata,
    )
    return candidate, ("WAIT", "REJECT")


def snapshot_to_trade_signal(snapshot: StrategySnapshot) -> TradeSignal | None:
    """Adapt an executable snapshot to the existing signal execution boundary."""

    if snapshot.candidate_class != "EXECUTABLE_ENTRY_SIGNAL":
        return None
    if not all(
        value is not None and value > 0.0
        for value in (snapshot.entry, snapshot.stop, snapshot.target)
    ):
        return None

    metadata = dict(snapshot.metadata)
    entry = float(snapshot.entry)
    stop = float(snapshot.stop)
    target = float(snapshot.target)
    level_price = _number(metadata.get("level_price")) or entry
    risk_per_share = _number(metadata.get("risk_per_share")) or abs(entry - stop)
    return TradeSignal(
        symbol=snapshot.symbol,
        strategy=snapshot.strategy,
        signal=snapshot.signal_state,
        direction=snapshot.direction,
        entry=entry,
        stop=stop,
        target=target,
        level_price=level_price,
        level_type=str(metadata.get("signal_level_type") or "source_level"),
        reward_risk=float(snapshot.reward_risk or 0.0),
        risk_per_share=risk_per_share,
        atr=snapshot.atr,
        confidence=float(snapshot.score or 0.0),
        metadata={
            **metadata,
            "strategy_source": snapshot.source,
            "scan_id": snapshot.scan_id,
            "source_bar_timestamp": snapshot.bar_timestamp,
            "source_signal_id": _source_signal_id(snapshot),
        },
    )


def default_sources() -> tuple[StrategySource, ...]:
    return (
        StrategySource(
            StrategySourceInfo("stock_screener", "Stock Screener", "LP1 / LP2 / PRB1 / PRB2 deterministic screener output."),
            _stock_screener_snapshots,
        ),
        StrategySource(
            StrategySourceInfo("bmsb", "BMSB", "Existing BMSB monitor signals; currently analysis-only without a deterministic trade plan."),
            lambda context: _monitor_snapshots(context, "bmsb"),
        ),
        StrategySource(
            StrategySourceInfo("gaussian", "Gaussian", "Existing Gaussian monitor signals; currently analysis-only without a deterministic trade plan."),
            lambda context: _monitor_snapshots(context, "gaussian"),
        ),
        StrategySource(
            StrategySourceInfo("gerchik_router", "Gerchik Router", "Accepted signals from the existing run_entry_scan strategy router."),
            _gerchik_snapshots,
        ),
    )


DEFAULT_REGISTRY = StrategySourceRegistry()
