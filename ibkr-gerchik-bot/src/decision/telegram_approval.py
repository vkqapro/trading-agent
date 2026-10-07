"""Durable, fail-closed Telegram approval workflow.

Telegram carries only an approval identifier.  All trade values are loaded from
DecisionAudit's authoritative candidate row and are revalidated before an
execution intent is created.  This module deliberately has no broker imports;
the existing execution worker remains the only broker caller.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from src.config import SETTINGS
from src.decision.strategy_sources import StrategySnapshot, refresh_current_source_snapshot, snapshot_to_candidate

from .audit import DecisionAudit
from .models import DecisionCandidate

PREPARED = "PREPARED"
PENDING_APPROVAL = "PENDING_APPROVAL"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
EXPIRED = "EXPIRED"
REVALIDATING = "REVALIDATING"
REVALIDATION_FAILED = "REVALIDATION_FAILED"
RISK_REJECTED = "RISK_REJECTED"
EXECUTION_READY = "EXECUTION_READY"
SUBMITTING = "SUBMITTING"
EXECUTED = "EXECUTED"
EXECUTION_FAILED = "EXECUTION_FAILED"
CANCELLED = "CANCELLED"

APPROVAL_JOB_PENDING = "PENDING"
APPROVAL_JOB_PROCESSING = "PROCESSING"
APPROVAL_JOB_COMPLETED = "COMPLETED"
APPROVAL_JOB_FAILED = "FAILED"

_TERMINAL = {REJECTED, EXPIRED, REVALIDATION_FAILED, RISK_REJECTED, EXECUTED, EXECUTION_FAILED, CANCELLED}
# Approval identity is reusable only while the existing workflow is still active.
# Terminal history must remain immutable and must never block a fresh approval.
_REUSABLE_ACTIVE = {PREPARED, PENDING_APPROVAL}
_TRANSITIONS = {
    PREPARED: {PENDING_APPROVAL},
    PENDING_APPROVAL: {APPROVED, REJECTED, EXPIRED},
    APPROVED: {REVALIDATING, EXPIRED},
    REVALIDATING: {REVALIDATION_FAILED, RISK_REJECTED, EXECUTION_READY},
    EXECUTION_READY: {SUBMITTING, EXECUTION_FAILED},
    SUBMITTING: {EXECUTED, EXECUTION_FAILED},
}
_ALLOWED_STRATEGIES = {"LP1", "LP2", "PRB1", "PRB2"}
_PLAN_FIELDS = ("symbol", "strategy", "direction", "level_price", "entry", "stop", "target", "reward_risk", "risk_per_share")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def plan_hash(candidate: DecisionCandidate) -> str:
    payload = {key: candidate.to_dict().get(key) for key in _PLAN_FIELDS}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def register_canonical_setup(
    setup_id: str,
    *,
    scanner_run_id: str | None = None,
    database_path: str | Path | None = None,
    canonical_candidate: DecisionCandidate | None = None,
) -> dict[str, Any] | None:
    """Register an existing canonical scanner setup in DecisionAudit.

    The setup is resolved only from the backend's canonical strategy source. No
    Telegram payload, scanner prices, or LLM-provided fields are accepted.
    """
    requested = str(setup_id or "").strip()
    if not requested:
        return None
    canonical: DecisionCandidate | None = canonical_candidate
    source_name = "scanner_artifact" if canonical_candidate is not None else None
    source_snapshot: StrategySnapshot | None = None
    try:
        payload = refresh_current_source_snapshot(force=True, minimum_interval_seconds=0.0) if canonical is None else None
    except Exception:
        return None
    sources = payload.get("sources") if isinstance(payload, Mapping) else None
    if canonical is None and not isinstance(sources, Mapping):
        return None
    for source, raw_items in (sources.items() if isinstance(sources, Mapping) else ()):
        if not isinstance(raw_items, (list, tuple)):
            continue
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                continue
            try:
                snapshot = StrategySnapshot(**dict(raw))
                candidate, _ = snapshot_to_candidate(snapshot)
            except Exception:
                continue
            if candidate.candidate_id != requested:
                continue
            if snapshot.candidate_class != "EXECUTABLE_ENTRY_SIGNAL":
                return None
            canonical = candidate
            source_name = str(source)
            source_snapshot = snapshot
            break
        if canonical is not None:
            break
    if canonical is None:
        return None
    metadata = dict(canonical.metadata)
    if scanner_run_id:
        metadata["scanner_run_id"] = str(scanner_run_id)
    if source_snapshot is not None:
        metadata["registration_source"] = source_name
        metadata["registration_signal_state"] = source_snapshot.signal_state
    canonical = replace(canonical, metadata=metadata)
    digest = plan_hash(canonical)
    audit = DecisionAudit(database_path or SETTINGS.decision_agent.database_path)
    with audit.connection() as con:
        existing_row = con.execute(
            "SELECT * FROM candidates WHERE candidate_id = ?", (requested,)
        ).fetchone()
        if existing_row is not None:
            existing = _candidate(existing_row)
            existing_hash = plan_hash(existing)
            if existing_hash == digest:
                audit.record_candidate_revision(
                    existing, digest, scanner_run_id=scanner_run_id, connection=con
                )
                return {
                    "setup_id": requested,
                    "plan_hash": digest,
                    "registered": False,
                    "reused": True,
                    "source": source_name,
                }
        # Keep the current canonical row addressable by the revision FK, then
        # append the immutable revision before leaving the transaction.
        audit.record_candidate(canonical, connection=con)
        audit.record_candidate_revision(
            canonical, digest, scanner_run_id=scanner_run_id, connection=con
        )
    return {
        "setup_id": requested,
        "plan_hash": digest,
        "registered": True,
        "reused": False,
        "source": source_name,
    }


_DAILY_STOCK_STRATEGIES = {"LP1", "LP2", "PRB1", "PRB2"}


def _easter_sunday(year: int) -> date:
    """Return Easter Sunday for the Gregorian calendar."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def _nth_weekday(year: int, month: int, weekday: int, ordinal: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + (ordinal - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    return cursor - timedelta(days=(cursor.weekday() - weekday) % 7)


def _nyse_holidays(year: int) -> set[date]:
    """Return the regular NYSE full-day holidays for one calendar year."""
    easter = _easter_sunday(year)
    return {
        _observed_fixed_holiday(year, 1, 1),       # New Year's Day
        _nth_weekday(year, 1, 0, 3),               # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),               # Washington's Birthday
        easter - timedelta(days=2),                # Good Friday
        _last_weekday(year, 5, 0),                # Memorial Day
        _observed_fixed_holiday(year, 6, 19),      # Juneteenth
        _observed_fixed_holiday(year, 7, 4),       # Independence Day
        _nth_weekday(year, 9, 0, 1),               # Labor Day
        _nth_weekday(year, 11, 3, 4),              # Thanksgiving Day
        _observed_fixed_holiday(year, 12, 25),     # Christmas Day
    }


def _is_nyse_session(day: date) -> bool:
    return day.weekday() < 5 and day not in _nyse_holidays(day.year)


def _previous_nyse_session(day: date) -> date:
    cursor = day - timedelta(days=1)
    while not _is_nyse_session(cursor):
        cursor -= timedelta(days=1)
    return cursor


def expected_latest_completed_daily_session(current_time: datetime | None = None) -> date:
    """Return the latest NYSE session expected to have a completed daily bar.

    Before the configured regular-session close, the current session is not
    complete, so the previous NYSE session is required. After close, the
    current NYSE session is required. Weekends and regular NYSE holidays roll
    back to the previous session. This is a bar/session check, independent of
    the short runtime/quote freshness limits used by intraday execution.
    """
    zone = ZoneInfo(str(SETTINGS.trading_hours.timezone))
    local_now = current_time or datetime.now(zone)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=zone)
    else:
        local_now = local_now.astimezone(zone)
    today = local_now.date()
    close_time = time(
        SETTINGS.trading_hours.market_close_hour,
        SETTINGS.trading_hours.market_close_minute,
    )
    if not _is_nyse_session(today) or local_now.time() < close_time:
        return _previous_nyse_session(today)
    return today


def _bar_session_date(value: object, zone: ZoneInfo) -> date | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone).date()


def fresh_scanner_revalidate(
    candidate: DecisionCandidate,
    *,
    max_data_age_seconds: float | None = None,
    current_time: datetime | None = None,
) -> dict[str, Any]:
    """Reload scanner state inside the trading backend, never via Hermes.

    The existing source adapters recalculate from the repository's persisted
    bars/watchlist. A matching executable snapshot is required, and source
    freshness/status are fail-closed before risk evaluation.
    """
    limit = float(max_data_age_seconds if max_data_age_seconds is not None else SETTINGS.decision_agent.max_data_age_seconds)
    try:
        payload = refresh_current_source_snapshot(force=True, minimum_interval_seconds=0.0)
    except Exception as exc:
        return {"valid": False, "reasons": ("scanner_unavailable", str(exc))}
    sources = payload.get("sources") if isinstance(payload, Mapping) else None
    metadata = payload.get("source_metadata") if isinstance(payload, Mapping) else None
    if not isinstance(sources, Mapping):
        return {"valid": False, "reasons": ("scanner_snapshot_missing",)}
    stale_sources: list[str] = []
    daily_stock_strategy = (
        candidate.asset_class.lower() == "stock"
        and candidate.strategy.upper() in _DAILY_STOCK_STRATEGIES
    )
    zone = ZoneInfo(str(SETTINGS.trading_hours.timezone))
    expected_session = expected_latest_completed_daily_session(current_time) if daily_stock_strategy else None
    for source, raw_items in sources.items():
        source_meta = metadata.get(source, {}) if isinstance(metadata, Mapping) else {}
        if not isinstance(source_meta, Mapping) or source_meta.get("available") is not True:
            continue
        age = source_meta.get("data_age_seconds")
        if daily_stock_strategy:
            latest_session = _bar_session_date(source_meta.get("latest_bar_timestamp"), zone)
            if latest_session is None or latest_session < expected_session:
                stale_sources.append(
                    f"{source}:latest={latest_session or 'unknown'}<expected={expected_session}"
                )
                continue
        elif age is None or float(age) > limit:
            stale_sources.append(f"{source}:{age if age is not None else 'unknown'}>{limit:g}")
            continue
        if not isinstance(raw_items, list):
            continue
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                continue
            try:
                snapshot = StrategySnapshot(**dict(raw))
                current, _ = snapshot_to_candidate(snapshot)
            except Exception:
                continue
            if current.candidate_id != candidate.candidate_id:
                continue
            status = str(snapshot.metadata.get("status", snapshot.signal_state)).upper()
            if snapshot.candidate_class != "EXECUTABLE_ENTRY_SIGNAL" or status in {"REJECTED", "EXPIRED", "CANCELLED", "STALE", "UNAVAILABLE", "NO_SIGNAL"}:
                return {"valid": False, "reasons": ("setup_not_actionable",), "candidate": current}
            return {
                "valid": True,
                "candidate": current,
                "source": str(source),
                "data_age_seconds": float(age) if age is not None else None,
                "latest_completed_session": str(expected_session) if expected_session else None,
            }
    if stale_sources:
        return {
            "valid": False,
            "reasons": ("source_data_stale", *stale_sources),
            "stale_sources": tuple(stale_sources),
            "latest_completed_session": str(expected_session) if expected_session else None,
        }
    return {"valid": False, "reasons": ("setup_disappeared_or_stale",)}


def _candidate(row: sqlite3.Row) -> DecisionCandidate:
    value = json.loads(str(row["candidate_json"]))
    return DecisionCandidate(
        candidate_id=str(value["candidate_id"]),
        created_at=datetime.fromisoformat(str(value["created_at"]).replace("Z", "+00:00")),
        asset_class=str(value.get("asset_class", "stock")),
        symbol=str(value["symbol"]), strategy=str(value["strategy"]), direction=str(value["direction"]),
        entry=float(value["entry"]), stop=float(value["stop"]), target=float(value["target"]),
        level_price=value.get("level_price"), level_type=str(value.get("level_type", "")),
        level_strength=value.get("level_strength"), atr=value.get("atr"), reward_risk=value.get("reward_risk"),
        risk_per_share=value.get("risk_per_share"), confidence=value.get("confidence"),
        partial_targets=tuple(value.get("partial_targets", ())), source_signal_id=value.get("source_signal_id"),
        market_context=dict(value.get("market_context", {})), metadata=dict(value.get("metadata", {})),
    )


@dataclass(frozen=True)
class ApprovalResult:
    approval_id: str
    status: str
    message: str
    execution_intent_id: str | None = None
    reasons: tuple[str, ...] = ()


class ApprovalStore:
    """SQLite-backed approval state machine sharing the DecisionAudit database."""

    def __init__(self, database_path: str | Path, *, ttl_seconds: int = 900,
                 preparation_ttl_seconds: int | None = None,
                 allowed_user_ids: tuple[str, ...] = (), allowed_chat_ids: tuple[str, ...] = (),
                 paper_execution_enabled: bool | None = None) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = int(ttl_seconds)
        self.preparation_ttl_seconds = int(
            getattr(SETTINGS, "telegram_approval_preparation_ttl_seconds", 300)
            if preparation_ttl_seconds is None else preparation_ttl_seconds
        )
        if self.preparation_ttl_seconds <= 0:
            raise ValueError("preparation_ttl_seconds must be positive")
        self.allowed_user_ids = {str(item) for item in allowed_user_ids if str(item)}
        self.allowed_chat_ids = {str(item) for item in allowed_chat_ids if str(item)}
        self.paper_execution_enabled = bool(
            SETTINGS.paper_trading and SETTINGS.decision_agent.allow_ibkr_paper_trading
            if paper_execution_enabled is None else paper_execution_enabled
        )
        self._initialize()

    def connection(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        return con

    @contextmanager
    def _session(self):
        con = self.connection()
        try:
            yield con
        finally:
            con.close()

    def _initialize(self) -> None:
        with self._session() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS trade_approvals (
                approval_id TEXT PRIMARY KEY, setup_id TEXT NOT NULL, plan_hash TEXT NOT NULL,
                scanner_run_id TEXT, symbol TEXT NOT NULL, strategy TEXT NOT NULL, direction TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, delivered_at TEXT,
                approved_at TEXT, rejected_at TEXT, executed_at TEXT,
                telegram_chat_id TEXT, telegram_user_id TEXT, telegram_message_id TEXT,
                telegram_callback_id TEXT, approval_source TEXT NOT NULL,
                execution_intent_id TEXT, broker_order_id TEXT, broker_account_id TEXT,
                broker_perm_id TEXT, broker_parent_order_id TEXT, broker_stop_order_id TEXT,
                broker_limit_order_id TEXT, broker_status TEXT, broker_statuses_json TEXT,
                broker_submitted_at TEXT, broker_tif TEXT, broker_outside_rth INTEGER,
                submission_outcome TEXT, reviewed_quantity INTEGER, risk_amount REAL,
                reward_amount REAL, failure_code TEXT, failure_message TEXT,
                candidate_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trade_approval_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id TEXT NOT NULL,
                event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trade_execution_intents (
                execution_intent_id TEXT PRIMARY KEY, approval_id TEXT NOT NULL UNIQUE,
                setup_id TEXT NOT NULL, plan_hash TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
                intent_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trade_approval_jobs (
                job_id TEXT PRIMARY KEY, approval_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL, created_at TEXT NOT NULL,
                claimed_at TEXT, completed_at TEXT, failure_message TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_trade_approvals_status ON trade_approvals(status);
            CREATE INDEX IF NOT EXISTS idx_trade_approval_jobs_status ON trade_approval_jobs(status);
            """)
            columns = {str(row[1]) for row in con.execute("PRAGMA table_info(trade_approvals)").fetchall()}
            if "delivered_at" not in columns:
                con.execute("ALTER TABLE trade_approvals ADD COLUMN delivered_at TEXT")
                # Existing PENDING rows predate delivery tracking; preserve their
                # prior expiry semantics rather than silently making them eternal.
                con.execute("UPDATE trade_approvals SET delivered_at=created_at WHERE status=?", (PENDING_APPROVAL,))
            for name, definition in {
                "broker_account_id": "TEXT", "broker_perm_id": "TEXT",
                "broker_parent_order_id": "TEXT", "broker_stop_order_id": "TEXT",
                "broker_limit_order_id": "TEXT", "broker_status": "TEXT",
                "broker_statuses_json": "TEXT", "broker_submitted_at": "TEXT",
                "broker_tif": "TEXT", "broker_outside_rth": "INTEGER",
                "submission_outcome": "TEXT",
                "reviewed_quantity": "INTEGER", "risk_amount": "REAL",
                "reward_amount": "REAL",
            }.items():
                if name not in columns:
                    con.execute(f"ALTER TABLE trade_approvals ADD COLUMN {name} {definition}")
            self._migrate_reusable_approval_identity(con)

    @staticmethod
    def _migrate_reusable_approval_identity(con: sqlite3.Connection) -> None:
        """Replace the legacy all-history UNIQUE constraint with an active-only index."""
        schema = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='trade_approvals'"
        ).fetchone()
        if schema and "UNIQUE(setup_id,plan_hash)" in str(schema[0]).replace(" ", "").replace("\n", "").replace("\t", ""):
            con.execute("ALTER TABLE trade_approvals RENAME TO trade_approvals_legacy")
            con.executescript("""
                CREATE TABLE trade_approvals (
                    approval_id TEXT PRIMARY KEY, setup_id TEXT NOT NULL, plan_hash TEXT NOT NULL,
                    scanner_run_id TEXT, symbol TEXT NOT NULL, strategy TEXT NOT NULL, direction TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, delivered_at TEXT,
                    approved_at TEXT, rejected_at TEXT, executed_at TEXT,
                    telegram_chat_id TEXT, telegram_user_id TEXT, telegram_message_id TEXT,
                    telegram_callback_id TEXT, approval_source TEXT NOT NULL,
                    execution_intent_id TEXT, broker_order_id TEXT, broker_account_id TEXT,
                    broker_perm_id TEXT, broker_parent_order_id TEXT, broker_stop_order_id TEXT,
                    broker_limit_order_id TEXT, broker_status TEXT, broker_statuses_json TEXT,
                    broker_submitted_at TEXT, broker_tif TEXT, broker_outside_rth INTEGER,
                    submission_outcome TEXT, reviewed_quantity INTEGER, risk_amount REAL,
                    reward_amount REAL, failure_code TEXT, failure_message TEXT,
                    candidate_json TEXT NOT NULL
                );
                INSERT INTO trade_approvals (
                    approval_id,setup_id,plan_hash,scanner_run_id,symbol,strategy,direction,status,created_at,expires_at,
                    approved_at,rejected_at,executed_at,telegram_chat_id,telegram_user_id,telegram_message_id,
                    telegram_callback_id,approval_source,execution_intent_id,broker_order_id,candidate_json
                ) SELECT
                    approval_id,setup_id,plan_hash,scanner_run_id,symbol,strategy,direction,status,created_at,expires_at,
                    approved_at,rejected_at,executed_at,telegram_chat_id,telegram_user_id,telegram_message_id,
                    telegram_callback_id,approval_source,execution_intent_id,broker_order_id,candidate_json
                FROM trade_approvals_legacy;
                DROP TABLE trade_approvals_legacy;
            """)
            con.execute("UPDATE trade_approvals SET delivered_at=created_at WHERE status=?", (PENDING_APPROVAL,))
        # Expire stale pending rows before enforcing the active-only uniqueness rule.
        con.execute(
            "UPDATE trade_approvals SET status=?, failure_code=?, failure_message=? "
            "WHERE status=? AND delivered_at IS NOT NULL AND expires_at <= ?",
            (EXPIRED, "approval_expired", "approval TTL elapsed", PENDING_APPROVAL, _iso(_now())),
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_trade_approvals_status_v2 ON trade_approvals(status)")
        con.execute("DROP INDEX IF EXISTS uq_trade_approvals_active_identity")
        con.execute(
            "CREATE UNIQUE INDEX uq_trade_approvals_active_identity "
            "ON trade_approvals(setup_id, plan_hash) "
            "WHERE status IN ('PREPARED','PENDING_APPROVAL')"
        )

    def _event(self, con: sqlite3.Connection, approval_id: str, event: str, **payload: Any) -> None:
        con.execute("INSERT INTO trade_approval_events(approval_id,event_type,payload_json,created_at) VALUES (?,?,?,?)",
                    (approval_id, event, json.dumps(payload, sort_keys=True, default=str), _iso(_now())))

    def _auth(self, user_id: str, chat_id: str) -> None:
        if not self.allowed_user_ids or not self.allowed_chat_ids:
            raise PermissionError("telegram approval allowlist is not configured")
        if str(user_id) not in self.allowed_user_ids or str(chat_id) not in self.allowed_chat_ids:
            raise PermissionError("telegram identity is not authorized")

    def _load_candidate(self, con: sqlite3.Connection, setup_id: str) -> DecisionCandidate:
        row = con.execute("SELECT * FROM candidates WHERE candidate_id = ?", (setup_id,)).fetchone()
        if row is None:
            raise ValueError("setup_not_found")
        candidate = _candidate(row)
        if candidate.direction.lower() != "long" or candidate.strategy.upper() not in _ALLOWED_STRATEGIES:
            raise ValueError("setup_not_supported_for_telegram_approval")
        return candidate

    def _retire_stale_prepared_in_connection(self, con: sqlite3.Connection, *, now: datetime) -> int:
        """Retire old undelivered preparations without deleting their history."""
        cutoff = now - timedelta(seconds=self.preparation_ttl_seconds)
        rows = con.execute(
            "SELECT approval_id,created_at FROM trade_approvals "
            "WHERE status=? AND delivered_at IS NULL AND telegram_message_id IS NULL",
            (PREPARED,),
        ).fetchall()
        retired = 0
        for row in rows:
            try:
                created_at = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                created_at = created_at.astimezone(timezone.utc)
            except (TypeError, ValueError):
                # An unreadable timestamp cannot prove freshness; fail closed by
                # retiring the undelivered preparation rather than reusing it.
                created_at = cutoff - timedelta(microseconds=1)
            if created_at > cutoff:
                continue
            changed = con.execute(
                "UPDATE trade_approvals SET status=?,failure_code=?,failure_message=? "
                "WHERE approval_id=? AND status=? AND delivered_at IS NULL AND telegram_message_id IS NULL",
                (EXPIRED, "preparation_expired", "undelivered preparation TTL elapsed", row["approval_id"], PREPARED),
            ).rowcount
            if changed:
                self._event(con, str(row["approval_id"]), "PREPARATION_EXPIRED",
                            preparation_ttl_seconds=self.preparation_ttl_seconds,
                            created_at=str(row["created_at"]))
                retired += changed
        return retired

    def retire_stale_prepared(self, *, now: datetime | None = None) -> int:
        """Retire stale undelivered PREPARED rows and preserve their history."""
        current = (now or _now()).astimezone(timezone.utc)
        with self._session() as con:
            con.execute("BEGIN IMMEDIATE")
            retired = self._retire_stale_prepared_in_connection(con, now=current)
            con.commit()
            return retired

    def create(self, setup_id: str, *, scanner_run_id: str | None = None, chat_id: str | None = None,
               user_id: str | None = None, message_id: str | None = None, source: str = "scanner") -> ApprovalResult:
        with self._session() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                self._retire_stale_prepared_in_connection(con, now=_now())
                candidate = self._load_candidate(con, setup_id)
                digest = plan_hash(candidate)
                existing = con.execute(
                    "SELECT * FROM trade_approvals WHERE setup_id=? AND plan_hash=? "
                    "AND status IN ('PREPARED','PENDING_APPROVAL') "
                    "ORDER BY created_at DESC LIMIT 1",
                    (setup_id, digest),
                ).fetchone()
                if existing is not None:
                    if existing["status"] == PENDING_APPROVAL and datetime.fromisoformat(existing["expires_at"]) <= _now():
                        con.execute(
                            "UPDATE trade_approvals SET status=?,failure_code=?,failure_message=? WHERE approval_id=? AND status=?",
                            (EXPIRED, "approval_expired", "approval TTL elapsed", existing["approval_id"], PENDING_APPROVAL),
                        )
                        self._event(con, existing["approval_id"], "EXPIRED")
                    else:
                        con.commit()
                        return ApprovalResult(str(existing["approval_id"]), str(existing["status"]), "approval already exists")
                approval_id = "approval_" + uuid.uuid4().hex
                created = _now()
                # PREPARED has no human review window yet. expires_at is a required
                # legacy column placeholder and is ignored until delivered_at exists.
                con.execute("""INSERT INTO trade_approvals
                    (approval_id,setup_id,plan_hash,scanner_run_id,symbol,strategy,direction,status,created_at,expires_at,
                     telegram_chat_id,telegram_user_id,telegram_message_id,approval_source,candidate_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (approval_id, setup_id, digest, scanner_run_id, candidate.symbol, candidate.strategy, candidate.direction,
                     PREPARED, _iso(created), _iso(created), chat_id, user_id, message_id, source,
                     json.dumps(candidate.to_dict(), sort_keys=True, default=str)))
                self._event(con, approval_id, "APPROVAL_PREPARED", setup_id=setup_id, plan_hash=digest)
                con.commit()
                return ApprovalResult(approval_id, PREPARED, "approval prepared; review TTL begins after card delivery")
            except Exception:
                con.rollback()
                raise

    def confirm_delivery(self, approval_id: str, *, chat_id: str, message_id: str | None = None) -> ApprovalResult:
        """Start the one human review window after a confirmed Telegram send.

        The PREPARED→PENDING_APPROVAL transition is atomic and idempotent by
        approval_id. Repeated delivery notifications never reset expires_at.
        """
        with self._session() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM trade_approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if row is None:
                con.rollback(); raise ValueError("approval_not_found")
            if row["status"] == PREPARED:
                delivered = _now()
                expires = delivered + timedelta(seconds=self.ttl_seconds)
                updated = con.execute(
                    "UPDATE trade_approvals SET status=?,delivered_at=?,expires_at=?,telegram_chat_id=?,telegram_message_id=? "
                    "WHERE approval_id=? AND status=? AND delivered_at IS NULL",
                    (PENDING_APPROVAL, _iso(delivered), _iso(expires), str(chat_id), message_id, approval_id, PREPARED),
                ).rowcount
                if updated != 1:
                    con.rollback(); raise ValueError("delivery_confirmation_conflict")
                self._event(con, approval_id, "CARD_DELIVERED", telegram_chat_id=str(chat_id), telegram_message_id=message_id, expires_at=_iso(expires))
                con.commit()
                return ApprovalResult(approval_id, PENDING_APPROVAL, "approval card delivered; review TTL started")
            con.commit()
            return ApprovalResult(approval_id, str(row["status"]), "delivery already recorded; review TTL unchanged")

    def record_delivery_ack_unknown(self, approval_id: str, *, chat_id: str, reason: str) -> ApprovalResult:
        """Persist an ambiguous Telegram acknowledgement without activating review.

        A send-response timeout may follow an already-delivered Telegram card.
        The approval remains PREPARED and cannot be actioned until explicit
        reconciliation confirms delivery.
        """
        with self._session() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM trade_approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if row is None:
                con.rollback(); raise ValueError("approval_not_found")
            if row["status"] == PREPARED:
                existing_reason = str(row["failure_message"] or "")
                con.execute(
                    "UPDATE trade_approvals SET telegram_chat_id=?,failure_code=?,failure_message=? WHERE approval_id=? AND status=?",
                    (str(chat_id), "delivery_ack_unknown", str(reason), approval_id, PREPARED),
                )
                if existing_reason != str(reason):
                    self._event(con, approval_id, "DELIVERY_ACK_UNKNOWN", telegram_chat_id=str(chat_id), reason=str(reason))
            con.commit()
            return ApprovalResult(approval_id, str(row["status"]), "delivery acknowledgement unknown; reconciliation required")

    def get(self, approval_id: str) -> dict[str, Any] | None:
        with self._session() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM trade_approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if row is None:
                con.rollback()
                return None
            row = self._expire_if_needed(con, row)
            intent = con.execute(
                "SELECT intent_json FROM trade_execution_intents WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
            con.commit()
            result = dict(row)
            if intent is not None:
                result["execution_intent_json"] = intent["intent_json"]
            return result

    def list_approvals(self, *, status: str | None = None) -> list[dict[str, Any]]:
        """Read approval rows for the broker-owning worker."""
        with self._session() as con:
            con.execute("BEGIN IMMEDIATE")
            if status is None:
                rows = con.execute("SELECT * FROM trade_approvals ORDER BY created_at").fetchall()
            else:
                rows = con.execute("SELECT * FROM trade_approvals WHERE status=? ORDER BY created_at", (status,)).fetchall()
            con.commit()
            return [dict(row) for row in rows]

    def list_telegram_card_reconciliation(self) -> list[dict[str, Any]]:
        """Return persisted cards whose status may need a transport refresh.

        This is deliberately read-only. The Telegram plugin owns delivery and
        message editing; the backend only exposes durable state and the
        persisted message correlation identifiers.
        """
        statuses = (
            REVALIDATION_FAILED, RISK_REJECTED, REJECTED, EXPIRED,
            EXECUTED, EXECUTION_FAILED, CANCELLED,
            EXECUTION_READY, SUBMITTING, APPROVED,
        )
        placeholders = ",".join("?" for _ in statuses)
        with self._session() as con:
            rows = con.execute(
                f"SELECT * FROM trade_approvals WHERE status IN ({placeholders}) "
                "AND telegram_chat_id IS NOT NULL AND telegram_message_id IS NOT NULL "
                "ORDER BY COALESCE(approved_at, delivered_at, created_at)",
                statuses,
            ).fetchall()
            return [dict(row) for row in rows]

    def expire_stale(self) -> int:
        """Startup recovery: expire every stale pending approval atomically."""
        count = 0
        with self._session() as con:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute("SELECT approval_id FROM trade_approvals WHERE status=? AND delivered_at IS NOT NULL AND expires_at <= ?", (PENDING_APPROVAL, _iso(_now()))).fetchall()
            for row in rows:
                updated = con.execute("UPDATE trade_approvals SET status=?,failure_code=?,failure_message=? WHERE approval_id=? AND status=?", (EXPIRED, "approval_expired", "approval TTL elapsed", row["approval_id"], PENDING_APPROVAL)).rowcount
                count += updated
                if updated:
                    self._event(con, row["approval_id"], "EXPIRED")
            con.commit()
        return count

    def _expire_if_needed(self, con: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row:
        if row["status"] == PENDING_APPROVAL and row["delivered_at"] is not None and datetime.fromisoformat(row["expires_at"]) <= _now():
            con.execute("UPDATE trade_approvals SET status=?,failure_code=?,failure_message=? WHERE approval_id=? AND status=?",
                        (EXPIRED, "approval_expired", "approval TTL elapsed", row["approval_id"], PENDING_APPROVAL))
            self._event(con, row["approval_id"], "EXPIRED")
            row = con.execute("SELECT * FROM trade_approvals WHERE approval_id=?", (row["approval_id"],)).fetchone()
        return row

    def decide(self, approval_id: str, *, approve: bool, user_id: str, chat_id: str, callback_id: str | None = None) -> ApprovalResult:
        self._auth(user_id, chat_id)
        with self._session() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM trade_approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if row is None:
                con.rollback(); raise ValueError("approval_not_found")
            row = self._expire_if_needed(con, row)
            target = APPROVED if approve else REJECTED
            if row["status"] != PENDING_APPROVAL:
                con.commit()
                return ApprovalResult(approval_id, str(row["status"]), "decision ignored: state is terminal or already decided")
            column = "approved_at" if approve else "rejected_at"
            updated = con.execute(f"UPDATE trade_approvals SET status=?,{column}=?,telegram_user_id=?,telegram_chat_id=?,telegram_callback_id=? WHERE approval_id=? AND status=?",
                                  (target, _iso(_now()), str(user_id), str(chat_id), callback_id, approval_id, PENDING_APPROVAL)).rowcount
            if updated != 1:
                con.commit(); return ApprovalResult(approval_id, target, "decision already processed")
            self._event(con, approval_id, target, telegram_user_id=str(user_id), telegram_chat_id=str(chat_id))
            if approve:
                job_id = f"approval-job-{approval_id}"
                con.execute(
                    "INSERT OR IGNORE INTO trade_approval_jobs(job_id,approval_id,status,created_at) VALUES (?,?,?,?)",
                    (job_id, approval_id, APPROVAL_JOB_PENDING, _iso(_now())),
                )
                self._event(con, approval_id, "APPROVAL_JOB_QUEUED", job_id=job_id)
            con.commit()
            return ApprovalResult(approval_id, target, "🟡 APPROVED — REVALIDATING" if approve else "🔴 REJECTED")

    def approval_job(self, approval_id: str) -> dict[str, Any] | None:
        """Return the durable worker job correlated to one approval."""
        with self._session() as con:
            row = con.execute("SELECT * FROM trade_approval_jobs WHERE approval_id=?", (approval_id,)).fetchone()
            return dict(row) if row is not None else None

    def claim_approved_jobs(self, *, limit: int = 25) -> list[dict[str, Any]]:
        """Atomically claim approved jobs so one worker handles each once."""
        claimed: list[dict[str, Any]] = []
        with self._session() as con:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute(
                "SELECT * FROM trade_approval_jobs WHERE status=? ORDER BY created_at LIMIT ?",
                (APPROVAL_JOB_PENDING, int(limit)),
            ).fetchall()
            now = _iso(_now())
            for row in rows:
                changed = con.execute(
                    "UPDATE trade_approval_jobs SET status=?,claimed_at=? WHERE job_id=? AND status=?",
                    (APPROVAL_JOB_PROCESSING, now, row["job_id"], APPROVAL_JOB_PENDING),
                ).rowcount
                if changed:
                    item = dict(row)
                    item["status"] = APPROVAL_JOB_PROCESSING
                    item["claimed_at"] = now
                    claimed.append(item)
            con.commit()
        return claimed

    def complete_approved_job(self, job_id: str, *, failure_message: str | None = None) -> bool:
        """Mark a claimed approval job terminal after backend processing."""
        with self._session() as con:
            changed = con.execute(
                "UPDATE trade_approval_jobs SET status=?,completed_at=?,failure_message=? "
                "WHERE job_id=? AND status=?",
                (APPROVAL_JOB_FAILED if failure_message else APPROVAL_JOB_COMPLETED,
                 _iso(_now()), failure_message, job_id, APPROVAL_JOB_PROCESSING),
            ).rowcount
            return bool(changed)

    def backend_revalidate_approval(self, approval_id: str) -> ApprovalResult:
        """Run scanner revalidation in the backend before risk/execution work."""
        with self._session() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM trade_approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if row is None:
                con.rollback(); raise ValueError("approval_not_found")
            if row["status"] != APPROVED:
                con.commit(); return ApprovalResult(approval_id, str(row["status"]), "approval is not awaiting revalidation")
            candidate = self._load_candidate(con, str(row["setup_id"]))
            if plan_hash(candidate) != str(row["plan_hash"]):
                con.execute("UPDATE trade_approvals SET status=?,failure_code=?,failure_message=? WHERE approval_id=? AND status=?", (REVALIDATION_FAILED, "plan_hash_changed", "authoritative setup changed", approval_id, APPROVED))
                self._event(con, approval_id, "REVALIDATION_FAILED", reason="plan_hash_changed"); con.commit()
                return ApprovalResult(approval_id, REVALIDATION_FAILED, "⚠️ SETUP CHANGED", reasons=("plan_hash_changed",))
            check = fresh_scanner_revalidate(candidate)
            if check.get("valid") is not True:
                reasons = tuple(str(x) for x in check.get("reasons", ())) or ("revalidation_failed",)
                con.execute("UPDATE trade_approvals SET status=?,failure_code=?,failure_message=? WHERE approval_id=? AND status=?", (REVALIDATION_FAILED, reasons[0], ";".join(reasons), approval_id, APPROVED))
                self._event(con, approval_id, "REVALIDATION_FAILED", reasons=reasons); con.commit()
                return ApprovalResult(approval_id, REVALIDATION_FAILED, "⚠️ SETUP CHANGED", reasons=reasons)
            fresh = check.get("candidate")
            if isinstance(fresh, DecisionCandidate) and plan_hash(fresh) != str(row["plan_hash"]):
                con.execute("UPDATE trade_approvals SET status=?,failure_code=?,failure_message=? WHERE approval_id=? AND status=?", (REVALIDATION_FAILED, "plan_hash_changed", "fresh setup changed", approval_id, APPROVED))
                self._event(con, approval_id, "REVALIDATION_FAILED", reason="plan_hash_changed")
                con.commit()
                return ApprovalResult(approval_id, REVALIDATION_FAILED, "SETUP CHANGED", reasons=("plan_hash_changed",))
            self._event(con, approval_id, "REVALIDATION_PASSED", data_age_seconds=check.get("data_age_seconds"), source=check.get("source"))
            con.commit()
            return ApprovalResult(approval_id, APPROVED, "Backend revalidation passed")

    def mark_execution(self, approval_id: str, status: str, *, broker_order_id: str | None = None,
                       failure: str | None = None, broker_details: Mapping[str, Any] | None = None) -> bool:
        """CAS terminal/update hook used by the existing execution worker."""
        if status not in {SUBMITTING, EXECUTED, EXECUTION_FAILED}:
            raise ValueError("invalid execution status")
        with self._session() as con:
            expected = EXECUTION_READY if status == SUBMITTING else SUBMITTING
            details = dict(broker_details or {})
            fields = ("status=?,broker_order_id=?,failure_message=?,broker_account_id=?,broker_perm_id=?,"
                      "broker_parent_order_id=?,broker_stop_order_id=?,broker_limit_order_id=?,broker_status=?,"
                      "broker_statuses_json=?,broker_submitted_at=?,broker_tif=?,broker_outside_rth=?,submission_outcome=?")
            values: list[Any] = [
                status, broker_order_id, failure,
                details.get("account_id"), details.get("perm_id"), details.get("parent_order_id"),
                details.get("stop_order_id"), details.get("limit_order_id"), details.get("broker_status"),
                json.dumps(details.get("broker_statuses"), sort_keys=True) if details.get("broker_statuses") is not None else None,
                details.get("submitted_at"), details.get("tif"),
                int(bool(details["outside_rth"])) if "outside_rth" in details else None,
                details.get("submission_outcome"), approval_id, expected,
            ]
            if status == EXECUTED:
                fields += ",executed_at=?"
                values.insert(len(values) - 2, _iso(_now()))
            changed = con.execute(f"UPDATE trade_approvals SET {fields} WHERE approval_id=? AND status=?", values).rowcount
            if changed:
                self._event(con, approval_id, "BROKER_SUBMIT_SUCCESS" if status == EXECUTED else "BROKER_SUBMIT_STARTED" if status == SUBMITTING else "BROKER_SUBMIT_FAILED", broker_order_id=broker_order_id, failure=failure)
            return bool(changed)

    def reconcile_approved_execution(self, approval_id: str, *,
                                     risk_gate: Callable[[DecisionCandidate], Mapping[str, Any]],
                                     enqueue: Callable[[Mapping[str, Any]], Any]) -> ApprovalResult:
        """Resume an approved approval after callback-side revalidation completed.

        This is deliberately separate from ``decide``/``approve_setup``.  It
        may continue only when the durable approval history contains
        ``REVALIDATION_PASSED`` and no execution intent or broker identifiers
        exist.  Account-aware risk evaluation and queueing remain delegated to
        the existing broker-owning worker.
        """
        with self._session() as con:
            row = con.execute("SELECT * FROM trade_approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if row is None:
                con.rollback()
                raise ValueError("approval_not_found")
            intent = con.execute(
                "SELECT execution_intent_id,status FROM trade_execution_intents WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
            if intent is not None:
                con.commit()
                return ApprovalResult(approval_id, EXECUTION_READY, "execution intent already exists",
                                      execution_intent_id=str(intent["execution_intent_id"]))
            if row["status"] != APPROVED:
                con.commit()
                return ApprovalResult(approval_id, str(row["status"]), "approval is not reconcilable")
            if any(row[key] is not None for key in (
                "broker_order_id", "broker_account_id", "broker_perm_id", "broker_parent_order_id",
                "broker_stop_order_id", "broker_limit_order_id", "broker_status",
            )):
                con.rollback()
                raise ValueError("broker_evidence_exists")
            passed = con.execute(
                "SELECT 1 FROM trade_approval_events WHERE approval_id=? AND event_type=? LIMIT 1",
                (approval_id, "REVALIDATION_PASSED"),
            ).fetchone()
            if passed is None:
                con.rollback()
                raise ValueError("revalidation_not_recorded")
            con.commit()

        # Reuse the existing intent/risk/queue implementation.  The callback
        # already persisted the successful revalidation; this no-op revalidator
        # prevents a timeout recovery from rerunning the slow scanner lookup.
        return self.process_approved(
            approval_id,
            revalidate=lambda candidate: {"valid": True, "candidate": candidate},
            risk_gate=risk_gate,
            enqueue=enqueue,
        )

    def process_approved(self, approval_id: str, *, revalidate: Callable[[DecisionCandidate], Mapping[str, Any]] | None = None,
                         risk_gate: Callable[[DecisionCandidate], Mapping[str, Any]],
                         enqueue: Callable[[Mapping[str, Any]], Any]) -> ApprovalResult:
        """Revalidate, run the existing deterministic gate, create one intent, enqueue it."""
        with self._session() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM trade_approvals WHERE approval_id=?", (approval_id,)).fetchone()
            if row is None: con.rollback(); raise ValueError("approval_not_found")
            row = self._expire_if_needed(con, row)
            if row["status"] != APPROVED:
                con.commit(); return ApprovalResult(approval_id, str(row["status"]), "approval is not ready")
            if not self.paper_execution_enabled:
                con.execute("UPDATE trade_approvals SET status=?,failure_code=?,failure_message=? WHERE approval_id=? AND status=?", (RISK_REJECTED, "paper_execution_disabled", "Paper execution kill switch is disabled", approval_id, APPROVED))
                self._event(con, approval_id, "RISK_GATE_REJECTED", reasons=("paper_execution_disabled",))
                con.commit()
                return ApprovalResult(approval_id, RISK_REJECTED, "⛔ APPROVED BUT NOT EXECUTED", reasons=("paper_execution_disabled",))
            con.execute("UPDATE trade_approvals SET status=? WHERE approval_id=? AND status=?", (REVALIDATING, approval_id, APPROVED))
            self._event(con, approval_id, "REVALIDATION_STARTED")
            candidate = self._load_candidate(con, str(row["setup_id"]))
            if plan_hash(candidate) != str(row["plan_hash"]):
                con.execute("UPDATE trade_approvals SET status=?,failure_code=?,failure_message=? WHERE approval_id=?", (REVALIDATION_FAILED, "plan_hash_changed", "authoritative setup changed", approval_id))
                self._event(con, approval_id, "REVALIDATION_FAILED", reason="plan_hash_changed"); con.commit()
                return ApprovalResult(approval_id, REVALIDATION_FAILED, "⚠️ SETUP CHANGED", reasons=("plan_hash_changed",))
            try:
                check = dict((revalidate or fresh_scanner_revalidate)(candidate) or {})
            except Exception as exc:
                con.execute("UPDATE trade_approvals SET status=?,failure_code=?,failure_message=? WHERE approval_id=?", (REVALIDATION_FAILED, "revalidation_error", str(exc), approval_id))
                self._event(con, approval_id, "REVALIDATION_FAILED", reason="revalidation_error")
                con.commit()
                return ApprovalResult(approval_id, REVALIDATION_FAILED, "⚠️ SETUP CHANGED", reasons=("revalidation_error",))
            fresh = check.get("candidate")
            if check.get("valid") is not True or (isinstance(fresh, DecisionCandidate) and plan_hash(fresh) != str(row["plan_hash"])):
                reasons = tuple(str(x) for x in check.get("reasons", ())) or ("revalidation_failed",)
                con.execute("UPDATE trade_approvals SET status=?,failure_code=?,failure_message=? WHERE approval_id=?", (REVALIDATION_FAILED, reasons[0], ";".join(reasons), approval_id))
                self._event(con, approval_id, "REVALIDATION_FAILED", reasons=reasons); con.commit()
                return ApprovalResult(approval_id, REVALIDATION_FAILED, "⚠️ SETUP CHANGED", reasons=reasons)
            validated_candidate = fresh if isinstance(fresh, DecisionCandidate) else candidate
            self._event(con, approval_id, "REVALIDATION_PASSED")
            try:
                risk = dict(risk_gate(validated_candidate) or {})
            except Exception as exc:
                con.execute("UPDATE trade_approvals SET status=?,failure_code=?,failure_message=? WHERE approval_id=?", (RISK_REJECTED, "risk_gate_error", str(exc), approval_id))
                self._event(con, approval_id, "RISK_GATE_REJECTED", reasons=("risk_gate_error",))
                con.commit()
                return ApprovalResult(approval_id, RISK_REJECTED, "⛔ APPROVED BUT NOT EXECUTED", reasons=("risk_gate_error",))
            if risk.get("paper_verified") is not True or risk.get("account_allowed") is not True:
                reasons = list(risk.get("reasons", ()))
                if risk.get("paper_verified") is not True:
                    reasons.append("paper_account_unverified")
                if risk.get("account_allowed") is not True:
                    reasons.append("paper_account_not_allowlisted")
                risk = {**risk, "approved": False, "reasons": reasons}
            if risk.get("approved") is not True or int(risk.get("quantity", 0) or 0) <= 0:
                reasons = tuple(str(x) for x in risk.get("reasons", ())) or ("risk_gate_rejected",)
                con.execute("UPDATE trade_approvals SET status=?,failure_code=?,failure_message=? WHERE approval_id=?", (RISK_REJECTED, "risk_gate_rejected", ";".join(reasons), approval_id))
                self._event(con, approval_id, "RISK_GATE_REJECTED", reasons=reasons); con.commit()
                return ApprovalResult(approval_id, RISK_REJECTED, "⛔ APPROVED BUT NOT EXECUTED", reasons=reasons)
            intent_id = "intent_" + uuid.uuid4().hex
            risk_amount = abs(float(validated_candidate.entry) - float(validated_candidate.stop)) * int(risk["quantity"])
            reward_amount = abs(float(validated_candidate.target) - float(validated_candidate.entry)) * int(risk["quantity"])
            intent = {"execution_intent_id": intent_id, "approval_id": approval_id, "setup_id": row["setup_id"], "plan_hash": row["plan_hash"],
                      "symbol": validated_candidate.symbol, "strategy": validated_candidate.strategy, "direction": "LONG", "entry": validated_candidate.entry,
                      "stop": validated_candidate.stop, "target": validated_candidate.target, "quantity": int(risk["quantity"]),
                      "risk_amount": risk_amount, "reward_amount": reward_amount, "account_id": risk.get("account_id"),
                      "idempotency_key": "approval:" + approval_id}
            now = _iso(_now())
            con.execute("INSERT INTO trade_execution_intents VALUES (?,?,?,?,?,?,?,?,?)", (intent_id, approval_id, row["setup_id"], row["plan_hash"], intent["idempotency_key"], json.dumps(intent, sort_keys=True), EXECUTION_READY, now, now))
            con.execute("UPDATE trade_approvals SET status=?,execution_intent_id=? WHERE approval_id=?", (EXECUTION_READY, intent_id, approval_id))
            con.execute(
                "UPDATE trade_approvals SET reviewed_quantity=?,risk_amount=?,reward_amount=? WHERE approval_id=?",
                (int(risk["quantity"]), risk_amount, reward_amount, approval_id),
            )
            self._event(con, approval_id, "RISK_GATE_PASSED", quantity=int(risk["quantity"]))
            self._event(con, approval_id, "EXECUTION_INTENT_CREATED", execution_intent_id=intent_id)
            con.commit()
        try:
            enqueue(intent)
        except Exception as exc:
            with self._session() as con:
                con.execute("UPDATE trade_approvals SET status=?,failure_code=?,failure_message=? WHERE approval_id=? AND status=?", (EXECUTION_FAILED, "enqueue_failed", str(exc), approval_id, EXECUTION_READY))
            return ApprovalResult(approval_id, EXECUTION_FAILED, "❌ EXECUTION FAILED", reasons=("enqueue_failed",))
        return ApprovalResult(approval_id, EXECUTION_READY, "Execution intent queued", execution_intent_id=intent_id)


def enqueue_existing_worker(intent: Mapping[str, Any]) -> str:
    """Send a validated intent to the existing atomic execution queue."""
    from src.execution.order_requests import submit_approval_intent
    return submit_approval_intent(dict(intent))


def format_ready_message(candidate: DecisionCandidate, approval_id: str, expires_at: datetime) -> str:
    score = "n/a" if candidate.confidence is None else f"{candidate.confidence:.4f}"
    rr = "n/a" if candidate.reward_risk is None else f"{candidate.reward_risk:.2f}"
    return (f"🟢 READY SETUP\n\n{candidate.symbol} · {candidate.strategy} · LONG\n\n"
            f"Level: {candidate.level_price if candidate.level_price is not None else 'n/a'}\nEntry: {candidate.entry}\n"
            f"Stop: {candidate.stop}\nTarget: {candidate.target}\nR:R: {rr}\nScore: {score}\n\n"
            f"Approval ID: {approval_id}\nExpires: {expires_at.astimezone().strftime('%H:%M:%S %Z')}\n\n"
            "[ APPROVE ]  [ REJECT ]")


_APPROVAL_ID_RE = re.compile(r"^approval_[0-9a-f]{32}$")


def parse_callback_data(value: str) -> tuple[str, str]:
    """Parse untrusted native Telegram callback data without invoking an LLM."""
    try:
        action, approval_id = str(value).split(":", 1)
    except ValueError as exc:
        raise ValueError("malformed callback") from exc
    if action not in {"approve", "reject", "details"} or not _APPROVAL_ID_RE.fullmatch(approval_id):
        raise ValueError("invalid callback")
    return action, approval_id


def callback_data(action: str, approval_id: str) -> str:
    if action not in {"approve", "reject", "details"} or not _APPROVAL_ID_RE.fullmatch(str(approval_id)):
        raise ValueError("invalid callback")
    return f"{action}:{approval_id}"


def telegram_status_message(status: str, *, approval_id: str | None = None, reason: str | None = None) -> str:
    """Map backend states to safe Telegram presentation text."""
    messages = {
        PENDING_APPROVAL: "🟢 READY SETUP\nAwaiting approval",
        APPROVED: "🟡 APPROVED\nBackend revalidation in progress...",
        REVALIDATING: "🟡 APPROVED\nBackend revalidation in progress...",
        REVALIDATION_FAILED: "⚠️ SETUP CHANGED OR NO LONGER VALID\nA new READY setup is required.",
        RISK_REJECTED: "⛔ APPROVED BUT NOT EXECUTED\nRisk gate rejected the setup.",
        EXECUTION_READY: "🟡 EXECUTION READY\nWaiting for the existing execution worker.",
        SUBMITTING: "🟡 SUBMITTING\nIBKR Paper order is being reconciled.",
        EXECUTED: "✅ EXECUTED — IBKR PAPER",
        REJECTED: "🔴 REJECTED\nNo order submitted.",
        EXPIRED: "⌛ APPROVAL EXPIRED\nNo order submitted.",
        EXECUTION_FAILED: "❌ EXECUTION FAILED\nNo duplicate retry without reconciliation.",
        CANCELLED: "🔴 CANCELLED\nNo order submitted.",
    }
    text = messages.get(str(status), "⚠️ APPROVAL STATUS UNKNOWN\nNo order submitted.")
    if approval_id:
        text += f"\nApproval: {approval_id}"
    if reason:
        text += f"\nReason: {reason}"
    return text


def telegram_controls(approval_id: str, *, pending: bool = True) -> list[list[dict[str, str]]]:
    """Return native inline-button semantics; callbacks remain opaque references."""
    if not _APPROVAL_ID_RE.fullmatch(str(approval_id)):
        raise ValueError("invalid approval id")
    if not pending:
        return []
    return [[
        {"text": "✅ APPROVE", "callback_data": callback_data("approve", approval_id)},
        {"text": "❌ REJECT", "callback_data": callback_data("reject", approval_id)},
    ], [{"text": "📊 DETAILS", "callback_data": callback_data("details", approval_id)}]]
