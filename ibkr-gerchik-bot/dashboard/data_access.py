"""Resilient, read-only data access for the Streamlit dashboard."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from src.config import SETTINGS
from src.data.bar_store import bar_path
from src.crypto.analysis import configured_crypto_symbols, load_crypto_state
from src.crypto.bar_store import crypto_bar_path, crypto_index_snapshot

DAILY_DECISIONS_PATH = SETTINGS.paths.runtime_dir.parent / "daily_decisions.json"
STATE_PATH = SETTINGS.paths.state_file
REPORTS_DIR = SETTINGS.paths.reports_dir
RESEARCH_LOG_PATH = SETTINGS.paths.research_log
TRADE_LOG_PATH = SETTINGS.paths.trade_log
ORDER_REQUESTS_PATH = SETTINGS.paths.runtime_dir.parent / "order_requests.json"
DASHBOARD_DATA_ACCESS_VERSION = 5

_WORKFLOW_HEADER = re.compile(r"(?m)^## Workflow ([^(]+?)(?: \(|$)")


@dataclass(frozen=True)
class SourceHealth:
    name: str
    path: Path
    exists: bool
    updated_at: Optional[datetime]
    age_seconds: Optional[float]
    size_bytes: int
    status: str
    detail: str = ""


@dataclass(frozen=True)
class ReportInfo:
    path: Path
    kind: str
    report_date: str
    updated_at: datetime
    size_bytes: int


def _signature(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
        return str(path), stat.st_mtime_ns, stat.st_size
    except OSError:
        return str(path), 0, 0


def _stable_read(path: Path, attempts: int = 3) -> bytes:
    """Read a file while another process may be replacing or appending to it."""
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            before = path.stat()
            payload = path.read_bytes()
            after = path.stat()
            if (
                before.st_mtime_ns == after.st_mtime_ns
                and before.st_size == after.st_size
                and len(payload) == after.st_size
            ):
                return payload
            last_error = OSError("file changed during read")
        except OSError as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(0.03 * (attempt + 1))
    if last_error:
        raise last_error
    raise OSError(f"Unable to read {path}")


@lru_cache(maxsize=32)
def _read_json_cached(path_text: str, _mtime_ns: int, _size: int) -> Dict[str, Any]:
    path = Path(path_text)
    try:
        data = json.loads(_stable_read(path).decode("utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return _read_json_cached(*_signature(path))


@lru_cache(maxsize=4)
def _workflow_snapshots_cached(
    path_text: str, _mtime_ns: int, _size: int
) -> Dict[str, Dict[str, Any]]:
    """Parse the workflow log once and retain the latest payload per stage."""
    try:
        text = _stable_read(Path(path_text)).decode("utf-8", errors="replace")
    except OSError:
        return {}

    latest: Dict[str, Dict[str, Any]] = {}
    matches = list(_WORKFLOW_HEADER.finditer(text))
    for index, match in enumerate(matches):
        stage = match.group(1).strip()
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[match.end() : section_end]
        json_start = section.find("```json")
        if json_start < 0:
            continue
        json_start += len("```json")
        json_end = section.find("```", json_start)
        if json_end < 0:
            continue
        try:
            envelope = json.loads(section[json_start:json_end].strip())
        except json.JSONDecodeError:
            continue
        payload = envelope.get("payload")
        if isinstance(payload, dict):
            latest[stage] = payload
    return latest


def load_workflow_snapshots() -> Dict[str, Dict[str, Any]]:
    if not RESEARCH_LOG_PATH.exists():
        return {}
    return _workflow_snapshots_cached(*_signature(RESEARCH_LOG_PATH))


def load_state() -> Dict[str, Any]:
    return _read_json(STATE_PATH)


def load_watchlist() -> Dict[str, Any]:
    snapshot = load_workflow_snapshots().get("Premarket", {})
    state_watchlist = load_state().get("watchlist", {})
    snapshot_watchlist = snapshot.get("watchlist") if isinstance(snapshot, dict) else None
    merged: Dict[str, Any] = {}
    if isinstance(snapshot_watchlist, dict):
        merged.update(snapshot_watchlist)
    if isinstance(state_watchlist, dict):
        # Runtime state is fresher for symbols added through the Watchlist Add
        # flow; do not hide those symbols behind the last full premarket snapshot.
        merged.update(state_watchlist)
    return merged


def load_premarket_snapshot() -> Dict[str, Any]:
    return load_workflow_snapshots().get("Premarket", {})


def blocked_news_summary(watchlist: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Per-symbol news-risk summary for every symbol blocked by the news filter.

    Returns one entry per blocked symbol with its matched headlines, the IBKR /
    provider codes that triggered the block, the source types, and any earnings
    event — everything needed to explain *why* the symbol was filtered out.
    """
    watchlist = watchlist if watchlist is not None else load_watchlist()
    summary: List[Dict[str, Any]] = []
    for symbol, plan in sorted(watchlist.items()):
        if not isinstance(plan, dict) or not plan.get("news_blocked"):
            continue
        headlines = [
            re.sub(r"^\{[^}]*\}\s*", "", str(h).strip())
            for h in (plan.get("matched_headlines") or [])
            if str(h).strip()
        ]
        summary.append(
            {
                "symbol": symbol,
                "headlines": headlines,
                "providers": [str(p) for p in (plan.get("news_provider_hits") or [])],
                "sources": [str(s) for s in (plan.get("news_source_types") or [])],
                "earnings": plan.get("earnings_event") if isinstance(plan.get("earnings_event"), dict) else {},
            }
        )
    return summary


def load_intraday_snapshot() -> Dict[str, Any]:
    return load_workflow_snapshots().get("Intraday", {})


def load_tracked_positions() -> List[Dict[str, Any]]:
    positions = load_state().get("tracked_positions", [])
    if isinstance(positions, list):
        return [item for item in positions if isinstance(item, dict)]
    return []


def load_order_requests(limit: int = 50) -> List[Dict[str, Any]]:
    """Most-recent-first view of dashboard-submitted order/close requests."""
    data = _read_json(ORDER_REQUESTS_PATH)
    requests = data.get("requests", [])
    if not isinstance(requests, list):
        return []
    rows = [item for item in requests if isinstance(item, dict)]
    return list(reversed(rows))[:limit]


@lru_cache(maxsize=256)
def _read_bars_cached(path_text: str, _mtime_ns: int, _size: int) -> pd.DataFrame:
    try:
        frame = pd.read_csv(Path(path_text))
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError):
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    required = {"date", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "open", "high", "low", "close"])
    return frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def get_bars(symbol: str, timeframe: str) -> pd.DataFrame:
    path = bar_path(symbol, timeframe)
    if not path.exists():
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    return _read_bars_cached(*_signature(path)).copy()


def bars_index() -> Dict[str, Dict[str, Any]]:
    index_path = bar_path("_", "_").parent / "index.json"
    return _read_json(index_path)


def get_crypto_bars(symbol: str, timeframe: str) -> pd.DataFrame:
    path = crypto_bar_path(symbol, timeframe)
    if not path.exists():
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    return _read_bars_cached(*_signature(path)).copy()


def crypto_bars_index() -> Dict[str, Dict[str, Any]]:
    return crypto_index_snapshot()


def load_crypto_dashboard_state() -> Dict[str, Any]:
    return load_crypto_state()


def load_crypto_symbols() -> List[Dict[str, str]]:
    return configured_crypto_symbols()


def load_daily_decisions() -> Dict[str, Any]:
    return _read_json(DAILY_DECISIONS_PATH)


def decisions_for_symbol(
    symbol: str, payload: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    payload = payload if payload is not None else load_daily_decisions()
    decisions = payload.get("decisions", {})
    if not isinstance(decisions, dict):
        return []
    entry = decisions.get(symbol, {})
    attempts = entry.get("attempts", []) if isinstance(entry, dict) else []
    return [attempt for attempt in attempts if isinstance(attempt, dict)]


def all_decision_attempts(payload: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    payload = payload if payload is not None else load_daily_decisions()
    decisions = payload.get("decisions", {})
    if not isinstance(decisions, dict):
        return []
    rows: List[Dict[str, Any]] = []
    for symbol, entry in decisions.items():
        if not isinstance(entry, dict):
            continue
        for attempt in entry.get("attempts", []):
            if isinstance(attempt, dict):
                rows.append({"symbol": symbol, **attempt})
    return rows


def decisions_to_frame(attempts: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for attempt in attempts:
        context = attempt.get("context", {})
        context = context if isinstance(context, dict) else {}
        reason = attempt.get("reason")
        if isinstance(reason, list):
            reason = "; ".join(str(item) for item in reason)
        zone = context.get("zone")
        if isinstance(zone, (list, tuple)) and len(zone) == 2:
            zone = f"{zone[0]} - {zone[1]}"
        rows.append(
            {
                "time": str(attempt.get("timestamp", "")).replace("T", " "),
                "strategy": attempt.get("strategy"),
                "level": attempt.get("level"),
                "level_type": attempt.get("level_type"),
                "signal": attempt.get("signal"),
                "entry": attempt.get("entry"),
                "stop": attempt.get("stop"),
                "target": attempt.get("target"),
                "confidence": attempt.get("confidence"),
                "reason": reason,
                "trend": context.get("trend"),
                "atr": context.get("atr_status"),
                "news": context.get("news_risk"),
                "zone": zone,
                "pattern": context.get("pattern"),
            }
        )
    return pd.DataFrame(rows)


@lru_cache(maxsize=8)
def _list_reports_cached(
    directory_text: str, _latest_mtime_ns: int, _count: int
) -> tuple[ReportInfo, ...]:
    directory = Path(directory_text)
    reports: List[ReportInfo] = []
    for path in directory.glob("*.xlsx"):
        if path.name.startswith("~$"):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        stem = path.stem
        match = re.match(r"(?P<kind>.+?)_(?P<date>\d{8})(?:_\d{6})?$", stem)
        kind = match.group("kind").replace("_", " ").title() if match else "Other"
        date_text = match.group("date") if match else ""
        report_date = (
            f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}" if date_text else "Unknown"
        )
        reports.append(
            ReportInfo(
                path=path,
                kind=kind,
                report_date=report_date,
                updated_at=datetime.fromtimestamp(stat.st_mtime).astimezone(),
                size_bytes=stat.st_size,
            )
        )
    return tuple(sorted(reports, key=lambda item: item.updated_at, reverse=True))


def list_report_info() -> List[ReportInfo]:
    if not REPORTS_DIR.exists():
        return []
    candidates = [p for p in REPORTS_DIR.glob("*.xlsx") if not p.name.startswith("~$")]
    mtimes = []
    for path in candidates:
        try:
            mtimes.append(path.stat().st_mtime_ns)
        except OSError:
            pass
    return list(
        _list_reports_cached(str(REPORTS_DIR), max(mtimes, default=0), len(candidates))
    )


def list_reports() -> List[Path]:
    return [item.path for item in list_report_info()]


@lru_cache(maxsize=16)
def _read_report_cached(path_text: str, _mtime_ns: int, _size: int) -> pd.DataFrame:
    try:
        return pd.read_excel(Path(path_text))
    except Exception:
        return pd.DataFrame()


def read_report(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return _read_report_cached(*_signature(path)).copy()


@lru_cache(maxsize=4)
def _trade_log_sections_cached(
    path_text: str, _mtime_ns: int, _size: int
) -> tuple[tuple[str, str], ...]:
    try:
        text = _stable_read(Path(path_text)).decode("utf-8", errors="replace")
    except OSError:
        return ()
    parts = re.split(r"(?m)^## ", text)
    sections: List[tuple[str, str]] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        first_line, _, body = part.partition("\n")
        sections.append((first_line.strip(), body.strip()))
    return tuple(sections)


def latest_trade_log_sections(limit: int = 20) -> List[Dict[str, str]]:
    if not TRADE_LOG_PATH.exists():
        return []
    sections = _trade_log_sections_cached(*_signature(TRADE_LOG_PATH))
    return [
        {"heading": heading, "body": body}
        for heading, body in sections[-limit:][::-1]
    ]


def source_health(stale_after_minutes: int = 20) -> List[SourceHealth]:
    """Return source availability with source-specific freshness semantics.

    Only the active-session feeds carry a freshness SLA:

    - **Runtime state** and **Daily decisions** are written continuously while
      the bot runs, so staleness here means the bot has likely stopped — this is
      what should raise attention.
    - **Research log** receives a workflow snapshot only at the *end* of an
      intraday loop, so mid-session it legitimately lags; it is reported as
      informational (age shown, but no false "stale" alarm).
    - **Trade log** is event-driven and only changes when a trade occurs.
    """
    now = datetime.now().astimezone()
    sources = (
        ("Runtime state", STATE_PATH, stale_after_minutes),
        ("Daily decisions", DAILY_DECISIONS_PATH, stale_after_minutes),
        ("Research log", RESEARCH_LOG_PATH, None),
        ("Trade log", TRADE_LOG_PATH, None),
    )
    result: List[SourceHealth] = []
    for name, path, threshold_minutes in sources:
        try:
            stat = path.stat()
        except OSError:
            result.append(
                SourceHealth(name, path, False, None, None, 0, "missing", "File not found")
            )
            continue
        updated_at = datetime.fromtimestamp(stat.st_mtime).astimezone()
        age = max(0.0, (now - updated_at).total_seconds())
        if threshold_minutes is None:
            status = "available"
            detail = "Event-driven source; age is informational"
        else:
            status = "stale" if age > threshold_minutes * 60 else "fresh"
            detail = f"Freshness threshold: {threshold_minutes} minutes"
        result.append(
            SourceHealth(
                name,
                path,
                True,
                updated_at,
                age,
                stat.st_size,
                status,
                detail,
            )
        )
    return result


def freshness() -> Dict[str, Optional[str]]:
    return {
        item.path.name: (
            item.updated_at.strftime("%Y-%m-%d %H:%M:%S %Z")
            if item.updated_at
            else None
        )
        for item in source_health()
    }


def clear_caches() -> None:
    _read_json_cached.cache_clear()
    _workflow_snapshots_cached.cache_clear()
    _read_bars_cached.cache_clear()
    _list_reports_cached.cache_clear()
    _read_report_cached.cache_clear()
    _trade_log_sections_cached.cache_clear()
