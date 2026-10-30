"""Durable AI Trade Desk proposals and deterministic proposal math.

AI may publish and explain a proposal, but all trade facts are copied from an
authoritative persisted scanner candidate. Human actions remain separate from
AI ingestion and are handed to the existing ApprovalStore by the dashboard.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


PROPOSAL_STATUSES = {
    "DISCOVERED",
    "UNDER_REVIEW",
    "HUMAN_REVIEWED_BLOCKED",
    "READY",
    "INVALIDATED",
    "APPROVED",
    "REJECTED",
    "SUBMITTED",
    "FILLED",
    "CANCELLED",
    "EXPIRED",
}
ACTIVE_PROPOSAL_STATUSES = {"DISCOVERED", "UNDER_REVIEW", "HUMAN_REVIEWED_BLOCKED", "READY"}
TERMINAL_LIFECYCLES = {"INVALIDATED", "EXPIRED", "NO_SETUP", "AMBIGUOUS_INTRABAR_ORDER"}
SUPPORTED_ORDER_TYPES = {"LIMIT", "MARKET", "STOP_LIMIT"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _finite_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def calculate_trade_metrics(
    *,
    direction: str,
    entry: Any,
    stop: Any,
    target: Any,
    rr: Any | None = None,
    cash_allocation: Any | None = None,
    max_risk_pct: Any | None = None,
    quantity: Any | None = None,
    keep_target: bool = False,
    calculation_driver: str = "rr",
) -> dict[str, Any]:
    """Calculate a proposal preview using direction-aware whole-share math."""
    side = str(direction or "").strip().upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    entry_value = _finite_number(entry, "entry")
    stop_value = _finite_number(stop, "stop")
    target_value = _finite_number(target, "target")
    if entry_value <= 0 or stop_value <= 0 or target_value <= 0:
        raise ValueError("entry, stop, and target must be positive")

    risk_per_share = entry_value - stop_value if side == "LONG" else stop_value - entry_value
    reward_per_share = target_value - entry_value if side == "LONG" else entry_value - target_value
    if risk_per_share <= 0:
        raise ValueError(f"invalid {side} stop relationship")
    if reward_per_share <= 0:
        raise ValueError(f"invalid {side} target relationship")

    driver = str(calculation_driver or "rr").strip().lower()
    if driver not in {"rr", "target"}:
        raise ValueError("calculation_driver must be rr or target")
    rr_value = _finite_number(rr, "rr") if rr not in (None, "") else reward_per_share / risk_per_share
    if keep_target:
        if rr_value <= 0:
            raise ValueError("rr must be positive")
        if side == "LONG":
            stop_value = entry_value - reward_per_share / rr_value
        else:
            stop_value = entry_value + reward_per_share / rr_value
        risk_per_share = entry_value - stop_value if side == "LONG" else stop_value - entry_value
        if risk_per_share <= 0:
            raise ValueError("adjusted stop is invalid")
    elif driver == "target":
        rr_value = reward_per_share / risk_per_share
    else:
        if rr_value <= 0:
            raise ValueError("rr must be positive")
        target_value = entry_value + risk_per_share * rr_value if side == "LONG" else entry_value - risk_per_share * rr_value
        reward_per_share = target_value - entry_value if side == "LONG" else entry_value - target_value

    cash_value = _finite_number(cash_allocation, "cash_allocation") if cash_allocation not in (None, "") else 0.0
    risk_pct_value = _finite_number(max_risk_pct, "max_risk_pct") if max_risk_pct not in (None, "") else 0.0
    if cash_value < 0 or risk_pct_value < 0:
        raise ValueError("cash_allocation and max_risk_pct cannot be negative")
    requested_quantity = int(_finite_number(quantity, "quantity")) if quantity not in (None, "") else 0
    if requested_quantity < 0:
        raise ValueError("quantity cannot be negative")
    qty_by_cash = math.floor(cash_value / entry_value) if cash_value > 0 else requested_quantity
    risk_budget = cash_value * risk_pct_value / 100.0
    qty_by_risk = math.floor(risk_budget / risk_per_share) if risk_budget > 0 else requested_quantity
    calculated_quantity = min(qty_by_cash, qty_by_risk) if cash_value > 0 and risk_budget > 0 else requested_quantity
    actual_risk = calculated_quantity * risk_per_share
    potential_profit = calculated_quantity * reward_per_share
    return {
        "entry": round(entry_value, 6),
        "stop": round(stop_value, 6),
        "target": round(target_value, 6),
        "rr": round(reward_per_share / risk_per_share, 6),
        "risk_per_share": round(risk_per_share, 6),
        "reward_per_share": round(reward_per_share, 6),
        "cash_allocation": round(cash_value, 2),
        "max_risk_pct": round(risk_pct_value, 6),
        "risk_budget": round(risk_budget, 2),
        "quantity": int(calculated_quantity),
        "actual_risk": round(actual_risk, 2),
        "potential_profit": round(potential_profit, 2),
        "notional_exposure": round(calculated_quantity * entry_value, 2),
    }


def candidate_identity(candidate: Mapping[str, Any]) -> str:
    values = (
        candidate.get("source_scan_id"),
        candidate.get("symbol"),
        candidate.get("strategy"),
        candidate.get("side") or candidate.get("direction"),
        candidate.get("scan_timestamp"),
    )
    return "candidate-" + hashlib.sha256("|".join(str(item or "") for item in values).encode()).hexdigest()[:24]


def opportunity_identity(candidate: Mapping[str, Any]) -> str:
    """Return a setup identity that is independent of scan/job timestamps.

    The scanner must provide a stable setup anchor.  A source observation ID
    is intentionally excluded: it identifies an observation, not an ongoing
    opportunity.  Missing setup evidence fails closed rather than collapsing
    unrelated signals by ticker, strategy, and direction.
    """
    source = candidate.get("source") if isinstance(candidate.get("source"), Mapping) else {}
    symbol = str(candidate.get("symbol") or source.get("symbol") or "").strip().upper()
    strategy = str(candidate.get("strategy") or source.get("strategy") or "").strip().upper()
    direction = str(candidate.get("side") or candidate.get("direction") or source.get("side") or "").strip().upper()
    level = candidate.get("level", source.get("level"))
    level_type = str(candidate.get("level_type") or source.get("level_type") or "").strip().upper()
    planned_entry = candidate.get("planned_entry", candidate.get("entry", source.get("planned_entry")))
    entry_touch_time = candidate.get("entry_touch_time") or source.get("entry_touch_time")
    anchors = (
        ("setup_id", candidate.get("setup_id") or source.get("setup_id")),
        ("setup_anchor_id", candidate.get("setup_anchor_id") or source.get("setup_anchor_id")),
        ("level_id", candidate.get("level_id") or source.get("level_id")),
        ("anchor_date", candidate.get("anchor_date") or source.get("anchor_date")),
        ("entry_touch_time", entry_touch_time),
    )
    anchor_kind, anchor = next(((kind, value) for kind, value in anchors if value not in (None, "")), ("", None))
    if (
        not all((symbol, strategy, direction, level_type))
        or level in (None, "")
        or planned_entry in (None, "")
        or anchor in (None, "")
    ):
        raise ValueError("OPPORTUNITY_ID_UNVERIFIED: stable scanner setup identity is required")
    try:
        level_value = f"{float(level):.6f}"
        entry_value = f"{float(planned_entry):.6f}"
    except (TypeError, ValueError) as exc:
        raise ValueError("OPPORTUNITY_ID_UNVERIFIED: setup prices are invalid") from exc
    # A touch time is an anchor only when the scanner has no stronger persisted setup anchor.
    # Including it beside an anchor date/setup ID would split the same opportunity when an
    # optional touch timestamp appears later.
    values = (symbol, strategy, direction, anchor_kind, str(anchor), level_type, level_value, entry_value)
    return "opportunity-" + hashlib.sha256("|".join(values).encode()).hexdigest()[:24]


class AITradeDeskStore:
    """SQLite-backed proposal store sharing the existing Decision Lab DB."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connection() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS ai_trade_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    symbol TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    direction TEXT NOT NULL,
                     opportunity_id TEXT,
                    source_scan_id TEXT NOT NULL,
                    source_candidate_id TEXT NOT NULL,
                    source_timestamp TEXT,
                    anchor_date TEXT,
                    entry REAL NOT NULL,
                    stop REAL NOT NULL,
                    target REAL NOT NULL,
                    rr REAL NOT NULL,
                    order_type TEXT NOT NULL,
                    cash_allocation REAL NOT NULL,
                    max_risk_pct REAL NOT NULL,
                    quantity INTEGER NOT NULL,
                    risk_per_share REAL NOT NULL,
                    actual_risk REAL NOT NULL,
                    potential_profit REAL NOT NULL,
                    notional_exposure REAL NOT NULL,
                    scanner_status TEXT NOT NULL,
                    scanner_lifecycle TEXT NOT NULL,
                    scanner_score REAL,
                    ai_reasoning TEXT,
                    ai_confidence REAL,
                    ai_model TEXT,
                    ai_review_status TEXT NOT NULL DEFAULT 'NOT_REVIEWED',
                    ai_review_json TEXT NOT NULL DEFAULT '{}',
                    warnings_json TEXT NOT NULL,
                    source_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    manual_override_status TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    approval_id TEXT,
                    expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ai_trade_proposals_status ON ai_trade_proposals(status);
                CREATE INDEX IF NOT EXISTS idx_ai_trade_proposals_source ON ai_trade_proposals(source_scan_id, symbol, strategy, direction);
                 CREATE TABLE IF NOT EXISTS ai_trade_observations (
                     observation_id TEXT PRIMARY KEY,
                     proposal_id TEXT NOT NULL,
                     opportunity_id TEXT NOT NULL,
                     source_scan_id TEXT NOT NULL,
                     source_candidate_id TEXT NOT NULL,
                     observed_at TEXT NOT NULL,
                     candidate_json TEXT NOT NULL,
                     UNIQUE(proposal_id, source_scan_id, source_candidate_id)
                 );
                CREATE TABLE IF NOT EXISTS ai_trade_proposal_revisions (
                    revision_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(proposal_id, revision)
                );
                CREATE TABLE IF NOT EXISTS ai_trade_proposal_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ai_trade_review_history (
                    review_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    reviewed_observation_id TEXT NOT NULL,
                    reviewed_source_scan_id TEXT NOT NULL,
                    review_fingerprint TEXT NOT NULL,
                    review_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(proposal_id, reviewed_observation_id, review_fingerprint)
                );
                CREATE INDEX IF NOT EXISTS idx_ai_trade_review_history_proposal
                    ON ai_trade_review_history(proposal_id, created_at);
                """
            )
            columns = {row[1] for row in con.execute("PRAGMA table_info(ai_trade_proposals)").fetchall()}
            if "ai_review_status" not in columns:
                con.execute("ALTER TABLE ai_trade_proposals ADD COLUMN ai_review_status TEXT NOT NULL DEFAULT 'NOT_REVIEWED'")
            if "ai_review_json" not in columns:
                con.execute("ALTER TABLE ai_trade_proposals ADD COLUMN ai_review_json TEXT NOT NULL DEFAULT '{}'")
            if "opportunity_id" not in columns:
                con.execute("ALTER TABLE ai_trade_proposals ADD COLUMN opportunity_id TEXT")
            con.execute("CREATE INDEX IF NOT EXISTS idx_ai_trade_proposals_opportunity ON ai_trade_proposals(opportunity_id, status)")

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for key in ("warnings_json", "source_json", "ai_review_json"):
            raw = result.pop(key, "{}")
            try:
                result[key.removesuffix("_json")] = json.loads(raw)
            except (TypeError, ValueError):
                result[key.removesuffix("_json")] = {}
        return result

    def list(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as con:
            rows = con.execute(
                "SELECT p.*, (SELECT COUNT(*) FROM ai_trade_observations o WHERE o.proposal_id=p.proposal_id) AS observation_count FROM ai_trade_proposals p WHERE (? IS NULL OR p.status=?) ORDER BY p.updated_at DESC",
                (status, status),
            ).fetchall()
            return [self._decode(row) for row in rows]

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        with self._connection() as con:
            row = con.execute("SELECT p.*, (SELECT COUNT(*) FROM ai_trade_observations o WHERE o.proposal_id=p.proposal_id) AS observation_count FROM ai_trade_proposals p WHERE p.proposal_id=?", (proposal_id,)).fetchone()
            return self._decode(row) if row else None

    def ingest(self, candidate: Mapping[str, Any], *, idempotency_key: str, ai_reasoning: str = "", ai_confidence: Any = None, ai_model: str = "", ai_review_status: str = "NOT_REVIEWED") -> dict[str, Any]:
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 200:
            raise ValueError("idempotency_key is required")
        symbol = str(candidate.get("symbol") or "").strip().upper()
        strategy = str(candidate.get("strategy") or "").strip().upper()
        direction = str(candidate.get("side") or candidate.get("direction") or "").strip().upper()
        scanner_status = str(candidate.get("scanner_status") or "CANDIDATE").strip().upper()
        lifecycle = str(candidate.get("lifecycle_status") or "UNKNOWN").strip().upper()
        if not symbol or strategy not in {"LP1", "LP2", "PRB1", "PRB2"} or direction not in {"LONG", "SHORT"}:
            raise ValueError("unsupported candidate identity")
        source_scan_id = str(candidate.get("source_scan_id") or "").strip()
        if not source_scan_id:
            raise ValueError("SOURCE_PROVENANCE_UNVERIFIED: source_scan_id is required")
        opportunity_id = opportunity_identity(candidate)
        if scanner_status in {"NO_SETUP", "NO_SCAN", "NOT_SCANNED", "ERROR"} or lifecycle in TERMINAL_LIFECYCLES:
            raise ValueError("candidate is not eligible for proposal ingestion")
        entry = candidate.get("entry", candidate.get("planned_entry"))
        stop = candidate.get("stop")
        target = candidate.get("target")
        rr = candidate.get("rr")
        quantity = candidate.get("position_size")
        cash = candidate.get("cash_allocation")
        if cash in (None, "") and entry not in (None, "") and quantity not in (None, ""):
            cash = _finite_number(entry, "entry") * int(_finite_number(quantity, "quantity"))
        risk_pct = candidate.get("risk_pct") or 0.5
        metrics = calculate_trade_metrics(direction=direction, entry=entry, stop=stop, target=target, rr=rr, cash_allocation=cash, max_risk_pct=risk_pct, quantity=quantity)
        timestamp = _now()
        proposal_id = "aitd-" + uuid.uuid4().hex
        warnings = list(candidate.get("missing_quality_data") or [])
        if candidate.get("direction_conflict") is True:
            warnings.append("Radar direction conflict is informational")
        source_candidate_id = str(candidate.get("source_candidate_id") or candidate_identity(candidate))
        observation_id = "observation-" + hashlib.sha256((source_scan_id + "|" + source_candidate_id).encode()).hexdigest()[:24]
        with self._connection() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute("SELECT * FROM ai_trade_proposals WHERE idempotency_key=?", (key,)).fetchone()
            if existing is None:
                existing = con.execute("SELECT * FROM ai_trade_proposals WHERE opportunity_id=? AND status IN ('DISCOVERED','UNDER_REVIEW','HUMAN_REVIEWED_BLOCKED','READY') ORDER BY created_at LIMIT 1", (opportunity_id,)).fetchone()
            if existing:
                existing_id = str(existing["proposal_id"])
                con.execute("INSERT OR IGNORE INTO ai_trade_observations(observation_id,proposal_id,opportunity_id,source_scan_id,source_candidate_id,observed_at,candidate_json) VALUES(?,?,?,?,?,?,?)", (observation_id, existing_id, opportunity_id, source_scan_id, source_candidate_id, timestamp, _json(dict(candidate))))
                row = con.execute("SELECT p.*, (SELECT COUNT(*) FROM ai_trade_observations o WHERE o.proposal_id=p.proposal_id) AS observation_count FROM ai_trade_proposals p WHERE p.proposal_id=?", (existing_id,)).fetchone()
                return self._decode(row)
            con.execute(
                """INSERT INTO ai_trade_proposals (
                    proposal_id,idempotency_key,symbol,strategy,direction,opportunity_id,source_scan_id,source_candidate_id,
                    source_timestamp,anchor_date,entry,stop,target,rr,order_type,cash_allocation,max_risk_pct,
                    quantity,risk_per_share,actual_risk,potential_profit,notional_exposure,scanner_status,
                    scanner_lifecycle,scanner_score,ai_reasoning,ai_confidence,ai_model,ai_review_status,ai_review_json,warnings_json,source_json,
                    status,manual_override_status,revision,approval_id,expires_at,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    proposal_id, key, symbol, strategy, direction, opportunity_id, source_scan_id,
                    source_candidate_id, candidate.get("scan_timestamp"),
                    candidate.get("anchor_date"), metrics["entry"], metrics["stop"], metrics["target"], metrics["rr"],
                    str(candidate.get("order_type") or "LIMIT").upper(), metrics["cash_allocation"], metrics["max_risk_pct"],
                    metrics["quantity"], metrics["risk_per_share"], metrics["actual_risk"], metrics["potential_profit"],
                    metrics["notional_exposure"], scanner_status, lifecycle, candidate.get("score"), str(ai_reasoning or ""),
                    float(ai_confidence) if ai_confidence not in (None, "") else None, str(ai_model or ""), str(ai_review_status or "NOT_REVIEWED"), _json({}), _json(warnings),
                    _json(dict(candidate)), "DISCOVERED", "NONE", 0, None, candidate.get("expires_at"), timestamp, timestamp,
                ),
            )
            con.execute("INSERT INTO ai_trade_proposal_events(proposal_id,event_type,actor,details_json,created_at) VALUES(?,?,?,?,?)", (proposal_id, "PROPOSAL_CREATED", "AI", _json({"idempotency_key": key}), timestamp))
            con.execute("INSERT INTO ai_trade_observations(observation_id,proposal_id,opportunity_id,source_scan_id,source_candidate_id,observed_at,candidate_json) VALUES(?,?,?,?,?,?,?)", (observation_id, proposal_id, opportunity_id, source_scan_id, source_candidate_id, timestamp, _json(dict(candidate))))
            row = con.execute("SELECT p.*, (SELECT COUNT(*) FROM ai_trade_observations o WHERE o.proposal_id=p.proposal_id) AS observation_count FROM ai_trade_proposals p WHERE p.proposal_id=?", (proposal_id,)).fetchone()
            return self._decode(row)

    def observations(self, proposal_id: str) -> list[dict[str, Any]]:
        with self._connection() as con:
            rows = con.execute("SELECT * FROM ai_trade_observations WHERE proposal_id=? ORDER BY observed_at, observation_id", (proposal_id,)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                try:
                    item["candidate"] = json.loads(item.pop("candidate_json"))
                except (TypeError, ValueError):
                    item["candidate"] = {}
                result.append(item)
            return result

    def review_observation(self, proposal_id: str, observation_id: str) -> dict[str, Any] | None:
        """Return an observation only when it remains attached to this proposal."""
        for observation in self.observations(proposal_id):
            if str(observation.get("observation_id") or "") == str(observation_id or ""):
                return observation
        return None

    @staticmethod
    def _review_fingerprint(review: Mapping[str, Any]) -> str:
        comparable = {str(key): value for key, value in review.items() if key != "review_timestamp"}
        return hashlib.sha256(_json(comparable).encode()).hexdigest()

    def review_history(self, proposal_id: str) -> list[dict[str, Any]]:
        with self._connection() as con:
            rows = con.execute(
                "SELECT * FROM ai_trade_review_history WHERE proposal_id=? ORDER BY created_at, review_id",
                (proposal_id,),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                try:
                    item["review"] = json.loads(item.pop("review_json"))
                except (TypeError, ValueError):
                    item["review"] = {}
                result.append(item)
            return result

    def latest_reviewable_observation(self, proposal_id: str) -> dict[str, Any]:
        """Select only the newest attached observation; never fall back silently."""
        observations = self.observations(proposal_id)
        if not observations:
            raise ValueError("NO_ATTACHED_OBSERVATION: proposal has no persisted scanner observation")
        latest = max(
            observations,
            key=lambda item: (str(item.get("observed_at") or ""), str(item.get("observation_id") or "")),
        )
        candidate = latest.get("candidate") if isinstance(latest.get("candidate"), Mapping) else {}
        scanner_status = str(candidate.get("scanner_status") or "").upper()
        lifecycle = str(candidate.get("lifecycle_status") or "").upper()
        if scanner_status in {"NO_SETUP", "NO_SCAN", "NOT_SCANNED", "ERROR"}:
            raise ValueError(f"LATEST_OBSERVATION_NOT_REVIEWABLE: scanner_status={scanner_status}")
        if lifecycle in TERMINAL_LIFECYCLES:
            raise ValueError(f"LATEST_OBSERVATION_NOT_REVIEWABLE: lifecycle={lifecycle}")
        required = ("observation_id", "source_scan_id", "source_candidate_id")
        missing = [field for field in required if not str(latest.get(field) or "").strip()]
        if missing:
            raise ValueError("SOURCE_PROVENANCE_UNVERIFIED: observation missing " + ",".join(missing))
        return latest

    def direct_review_payload(self, proposal_id: str) -> dict[str, Any]:
        """Build an authenticated, proposal-scoped review envelope."""
        proposal = self.get(proposal_id)
        if proposal is None:
            raise KeyError("proposal_not_found")
        observation = self.latest_reviewable_observation(proposal_id)
        candidate = observation.get("candidate") if isinstance(observation.get("candidate"), Mapping) else {}
        direction = str(candidate.get("side") or candidate.get("direction") or proposal["direction"]).upper()
        if str(candidate.get("symbol") or proposal["symbol"]).upper() != str(proposal["symbol"]).upper():
            raise ValueError("SOURCE_PROVENANCE_UNVERIFIED: observation symbol does not match proposal")
        if str(candidate.get("strategy") or proposal["strategy"]).upper() != str(proposal["strategy"]).upper():
            raise ValueError("SOURCE_PROVENANCE_UNVERIFIED: observation strategy does not match proposal")
        if direction != str(proposal["direction"]).upper():
            raise ValueError("SOURCE_PROVENANCE_UNVERIFIED: observation direction does not match proposal")
        return {
            "proposal_id": proposal["proposal_id"],
            "opportunity_id": proposal.get("opportunity_id"),
            "proposal_origin_source_scan_id": proposal["source_scan_id"],
            "proposal_origin_source_candidate_id": proposal["source_candidate_id"],
            "reviewed_observation_id": observation["observation_id"],
            "reviewed_source_scan_id": observation["source_scan_id"],
            "reviewed_source_candidate_id": observation["source_candidate_id"],
            # The review contract uses source_scan_id for the observation being
            # analyzed; proposal origin remains separate above.
            "source_scan_id": observation["source_scan_id"],
            "symbol": proposal["symbol"],
            "strategy": proposal["strategy"],
            "direction": direction,
            "lifecycle_status": candidate.get("lifecycle_status") or proposal.get("scanner_lifecycle"),
            "scanner_status": candidate.get("scanner_status") or proposal.get("scanner_status"),
            "scanner_score": candidate.get("score") or proposal.get("scanner_score"),
            "source_timestamp": candidate.get("scan_timestamp") or proposal.get("source_timestamp"),
            "entry": candidate.get("entry", candidate.get("planned_entry", proposal.get("entry"))),
            "stop": candidate.get("stop", proposal.get("stop")),
            "target": candidate.get("target", proposal.get("target")),
            "rr": candidate.get("rr", proposal.get("rr")),
            "quantity": candidate.get("position_size", proposal.get("quantity")),
            "cash_allocation": candidate.get("cash_allocation", proposal.get("cash_allocation")),
            "max_risk_pct": candidate.get("risk_pct", proposal.get("max_risk_pct")),
            "warnings": proposal.get("warnings") or [],
            "source": candidate,
            "human_status": proposal.get("status"),
            "ai_review_status": proposal.get("ai_review_status"),
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
        }

    def update(self, proposal_id: str, changes: Mapping[str, Any], *, actor: str, reason: str = "") -> dict[str, Any]:
        current = self.get(proposal_id)
        if current is None:
            raise KeyError("proposal_not_found")
        if current["status"] in {"INVALIDATED", "EXPIRED", "REJECTED", "SUBMITTED", "FILLED", "CANCELLED"}:
            raise ValueError("terminal proposal cannot be edited")
        direction = str(changes.get("direction", current["direction"])).upper()
        source_direction = str((current.get("source") or {}).get("side") or current["direction"]).upper()
        if direction != source_direction:
            raise ValueError("direction_override_not_allowed")
        expected_revision = changes.get("revision")
        if expected_revision not in (None, "") and int(expected_revision) != int(current["revision"]):
            raise ValueError("revision_conflict")
        order_type = str(changes.get("order_type", current["order_type"])).upper()
        if order_type not in SUPPORTED_ORDER_TYPES:
            raise ValueError("unsupported order type")
        metrics = calculate_trade_metrics(
            direction=direction,
            entry=changes.get("entry", current["entry"]),
            stop=changes.get("stop", current["stop"]),
            target=changes.get("target", current["target"]),
            rr=changes.get("rr", current["rr"]),
            cash_allocation=changes.get("cash_allocation", current["cash_allocation"]),
            max_risk_pct=changes.get("max_risk_pct", current["max_risk_pct"]),
            quantity=changes.get("quantity", current["quantity"]),
            keep_target=bool(changes.get("keep_target", False)),
            calculation_driver=str(changes.get("calculation_driver") or "rr"),
        )
        updated = {**current, **metrics, "direction": direction, "order_type": order_type, "manual_override_status": "OVERRIDDEN", "revision": int(current["revision"]) + 1}
        now = _now()
        with self._connection() as con:
            con.execute("BEGIN IMMEDIATE")
            latest = con.execute("SELECT revision FROM ai_trade_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            if latest is None:
                raise KeyError("proposal_not_found")
            if int(latest["revision"]) != int(current["revision"]):
                raise ValueError("revision_conflict")
            con.execute(
                """UPDATE ai_trade_proposals SET direction=?,entry=?,stop=?,target=?,rr=?,order_type=?,cash_allocation=?,max_risk_pct=?,quantity=?,risk_per_share=?,actual_risk=?,potential_profit=?,notional_exposure=?,manual_override_status=?,revision=?,updated_at=? WHERE proposal_id=? AND revision=?""",
                (updated["direction"], updated["entry"], updated["stop"], updated["target"], updated["rr"], updated["order_type"], updated["cash_allocation"], updated["max_risk_pct"], updated["quantity"], updated["risk_per_share"], updated["actual_risk"], updated["potential_profit"], updated["notional_exposure"], updated["manual_override_status"], updated["revision"], now, proposal_id, current["revision"]),
            )
            con.execute("INSERT INTO ai_trade_proposal_revisions VALUES(?,?,?,?,?,?,?,?)", ("rev-" + uuid.uuid4().hex, proposal_id, updated["revision"], _json(current), _json(updated), actor, reason, now))
            con.execute("INSERT INTO ai_trade_proposal_events(proposal_id,event_type,actor,details_json,created_at) VALUES(?,?,?,?,?)", (proposal_id, "PARAMETERS_UPDATED", actor, _json({"reason": reason}), now))
            row = con.execute("SELECT * FROM ai_trade_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            return self._decode(row)

    def publish_ai_review(self, proposal_id: str, review: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
        current = self.get(proposal_id)
        if current is None:
            raise KeyError("proposal_not_found")
        if str(review.get("proposal_id") or proposal_id) != proposal_id:
            raise ValueError("review proposal_id does not match authoritative proposal")
        if str(review.get("symbol") or "").upper() != str(current["symbol"]).upper():
            raise ValueError("review symbol does not match authoritative proposal")
        if str(review.get("strategy") or "").upper() != str(current["strategy"]).upper():
            raise ValueError("review strategy does not match authoritative proposal")
        required = ("ai_reasoning", "strengths", "weaknesses", "risk_observations", "review_result", "model_provider")
        missing = [key for key in required if key not in review]
        if missing:
            raise ValueError("review missing fields: " + ",".join(missing))
        normalized = dict(review)
        dual_provenance_keys = (
            "opportunity_id",
            "proposal_origin_source_scan_id",
            "reviewed_observation_id",
            "reviewed_source_candidate_id",
        )
        has_dual_provenance = any(key in normalized for key in dual_provenance_keys)
        if has_dual_provenance:
            missing_provenance = [key for key in dual_provenance_keys if not str(normalized.get(key) or "").strip()]
            if missing_provenance:
                raise ValueError("review dual provenance missing fields: " + ",".join(missing_provenance))
            if str(normalized["opportunity_id"]) != str(current.get("opportunity_id") or ""):
                raise ValueError("review opportunity_id does not match authoritative proposal")
            if str(normalized["proposal_origin_source_scan_id"]) != str(current["source_scan_id"]):
                raise ValueError("review proposal origin does not match immutable proposal provenance")
            origin_candidate_id = normalized.get("proposal_origin_source_candidate_id")
            if origin_candidate_id not in (None, "") and str(origin_candidate_id) != str(current["source_candidate_id"]):
                raise ValueError("review proposal origin candidate does not match immutable proposal provenance")
            observation = self.review_observation(proposal_id, str(normalized["reviewed_observation_id"]))
            if observation is None:
                raise ValueError("reviewed observation is not attached to authoritative proposal")
            if str(observation.get("opportunity_id") or "") != str(current.get("opportunity_id") or ""):
                raise ValueError("reviewed observation opportunity does not match authoritative proposal")
            if str(normalized.get("source_scan_id") or "") != str(observation.get("source_scan_id") or ""):
                raise ValueError("review source_scan_id does not match reviewed observation")
            if str(normalized["reviewed_source_candidate_id"]) != str(observation.get("source_candidate_id") or ""):
                raise ValueError("review source candidate does not match reviewed observation")
            candidate = observation.get("candidate") if isinstance(observation.get("candidate"), Mapping) else {}
            reviewed_direction = str(normalized.get("direction") or normalized.get("side") or "").upper()
            candidate_direction = str(candidate.get("side") or candidate.get("direction") or "").upper()
            for field, expected, actual in (
                ("symbol", current["symbol"], candidate.get("symbol")),
                ("strategy", current["strategy"], candidate.get("strategy")),
                ("direction", current["direction"], candidate_direction),
                ("lifecycle", normalized.get("lifecycle_status"), candidate.get("lifecycle_status")),
            ):
                if field == "lifecycle" and expected in (None, ""):
                    raise ValueError("review lifecycle_status is required for reviewed observation")
                if field != "lifecycle" and field == "direction":
                    expected = reviewed_direction
                if str(expected or "").upper() != str(actual or "").upper():
                    raise ValueError(f"review {field} does not match reviewed observation")
            scanner_status = str(candidate.get("scanner_status") or "").upper()
            lifecycle = str(candidate.get("lifecycle_status") or "").upper()
            if scanner_status in {"NO_SETUP", "NO_SCAN", "NOT_SCANNED", "ERROR"} or lifecycle in TERMINAL_LIFECYCLES:
                raise ValueError("reviewed observation is not reviewable")
            normalized["reviewed_source_scan_id"] = str(observation["source_scan_id"])
        elif str(normalized.get("source_scan_id") or "") != str(current["source_scan_id"]):
            # Legacy review clients retain the original immutable-source contract.
            raise ValueError("review source_scan_id does not match authoritative proposal")
        normalized["review_timestamp"] = str(normalized.get("review_timestamp") or _now())
        normalized["source_scan_id"] = current["source_scan_id"]
        normalized["symbol"] = current["symbol"]
        normalized["strategy"] = current["strategy"]
        normalized["proposal_id"] = proposal_id
        normalized["opportunity_id"] = current.get("opportunity_id")
        normalized["proposal_origin_source_scan_id"] = current["source_scan_id"]
        normalized["proposal_origin_source_candidate_id"] = current["source_candidate_id"]
        reviewed_observation_id = str(normalized.get("reviewed_observation_id") or "").strip()
        fingerprint = self._review_fingerprint(normalized)
        legacy_history_seed: tuple[str, str, str, str] | None = None
        if current.get("ai_review_status") == "COMPLETED":
            # A retry for the same observation and content is idempotent. A
            # changed review for that observation requires an explicit policy;
            # a later attached observation is a separate auditable review.
            if not reviewed_observation_id:
                # Preserve the legacy batch-review behavior for old payloads
                # that predate observation-aware review history.
                return current
            existing_history = self.review_history(proposal_id)
            if not existing_history:
                existing = current.get("ai_review") if isinstance(current.get("ai_review"), Mapping) else {}
                existing_observation_id = str(existing.get("reviewed_observation_id") or "")
                existing_fingerprint = self._review_fingerprint(existing) if existing else ""
                if existing_observation_id == reviewed_observation_id and existing_fingerprint == fingerprint:
                    with self._connection() as con:
                        con.execute(
                            "INSERT OR IGNORE INTO ai_trade_review_history(review_id,proposal_id,reviewed_observation_id,reviewed_source_scan_id,review_fingerprint,review_json,created_at) VALUES(?,?,?,?,?,?,?)",
                            ("review-" + uuid.uuid4().hex, proposal_id, reviewed_observation_id, str(normalized.get("reviewed_source_scan_id") or normalized.get("source_scan_id")), fingerprint, _json(existing), str(existing.get("review_timestamp") or _now())),
                        )
                    return current
                if existing_observation_id == reviewed_observation_id:
                    raise ValueError("review_conflict: a different review already exists for this observation")
                if existing_observation_id:
                    legacy_history_seed = (
                        existing_observation_id,
                        str(existing.get("reviewed_source_scan_id") or existing.get("source_scan_id") or current["source_scan_id"]),
                        existing_fingerprint,
                        _json(existing),
                    )
            for history in existing_history:
                if str(history.get("reviewed_observation_id")) == reviewed_observation_id:
                    if str(history.get("review_fingerprint")) == fingerprint:
                        return current
                    raise ValueError("review_conflict: a different review already exists for this observation")
        with self._connection() as con:
            con.execute("UPDATE ai_trade_proposals SET ai_reasoning=?,ai_model=?,ai_review_status='COMPLETED',ai_review_json=?,updated_at=? WHERE proposal_id=?", (str(normalized["ai_reasoning"]), str(normalized["model_provider"]), _json(normalized), normalized["review_timestamp"], proposal_id))
            if legacy_history_seed is not None:
                old_observation_id, old_source_scan_id, old_fingerprint, old_json = legacy_history_seed
                con.execute(
                    "INSERT OR IGNORE INTO ai_trade_review_history(review_id,proposal_id,reviewed_observation_id,reviewed_source_scan_id,review_fingerprint,review_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    ("review-" + uuid.uuid4().hex, proposal_id, old_observation_id, old_source_scan_id, old_fingerprint, old_json, str((current.get("ai_review") or {}).get("review_timestamp") or _now())),
                )
            con.execute(
                "INSERT INTO ai_trade_review_history(review_id,proposal_id,reviewed_observation_id,reviewed_source_scan_id,review_fingerprint,review_json,created_at) VALUES(?,?,?,?,?,?,?)",
                ("review-" + uuid.uuid4().hex, proposal_id, reviewed_observation_id, str(normalized.get("reviewed_source_scan_id") or normalized.get("source_scan_id")), fingerprint, _json(normalized), normalized["review_timestamp"]),
            )
            con.execute("INSERT INTO ai_trade_proposal_events(proposal_id,event_type,actor,details_json,created_at) VALUES(?,?,?,?,?)", (proposal_id, "AI_REVIEW_PUBLISHED", actor, _json({"review_result": normalized.get("review_result"), "opportunity_id": normalized.get("opportunity_id"), "proposal_origin_source_scan_id": normalized.get("proposal_origin_source_scan_id"), "reviewed_observation_id": normalized.get("reviewed_observation_id"), "reviewed_source_scan_id": normalized.get("reviewed_source_scan_id"), "reviewed_source_candidate_id": normalized.get("reviewed_source_candidate_id")}), normalized["review_timestamp"]))
            row = con.execute("SELECT * FROM ai_trade_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            return self._decode(row)

    def transition(self, proposal_id: str, status: str, *, actor: str, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
        normalized = str(status or "").upper()
        if normalized not in PROPOSAL_STATUSES:
            raise ValueError("unsupported proposal status")
        current = self.get(proposal_id)
        if current is None:
            raise KeyError("proposal_not_found")
        if normalized == "READY":
            ready_check = self.ready_transition_check(current)
            if not ready_check["eligible"]:
                raise ValueError(f"READY transition blocked: {ready_check['reason_code']}: {ready_check['reason']}")
        transition_details = dict(details or {})
        if normalized == "HUMAN_REVIEWED_BLOCKED":
            ready_check = self.ready_transition_check(current)
            if ready_check["eligible"]:
                raise ValueError("current setup is READY-eligible and should not be marked execution-blocked")
            transition_details.update({
                "execution_eligibility": "BLOCKED",
                "ready_block_reason_code": ready_check["reason_code"],
                "ready_block_reason": ready_check["reason"],
            })
        allowed = {
            "DISCOVERED": {"UNDER_REVIEW", "READY", "INVALIDATED", "REJECTED"},
            "UNDER_REVIEW": {"DISCOVERED", "HUMAN_REVIEWED_BLOCKED", "READY", "INVALIDATED", "REJECTED"},
            "HUMAN_REVIEWED_BLOCKED": {"UNDER_REVIEW", "SUBMITTED", "INVALIDATED", "REJECTED"},
            "READY": {"UNDER_REVIEW", "SUBMITTED", "APPROVED", "INVALIDATED", "REJECTED"},
            "APPROVED": {"SUBMITTED", "REJECTED", "EXPIRED"},
        }
        if normalized not in allowed.get(current["status"], set()) and normalized != current["status"]:
            raise ValueError(f"invalid transition {current['status']}->{normalized}")
        now = _now()
        with self._connection() as con:
            con.execute("UPDATE ai_trade_proposals SET status=?,updated_at=? WHERE proposal_id=?", (normalized, now, proposal_id))
            con.execute("INSERT INTO ai_trade_proposal_events(proposal_id,event_type,actor,details_json,created_at) VALUES(?,?,?,?,?)", (proposal_id, "STATUS_CHANGED", actor, _json({"from": current["status"], "to": normalized, **transition_details}), now))
            row = con.execute("SELECT * FROM ai_trade_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            return self._decode(row)

    def attach_approval(self, proposal_id: str, approval_id: str, *, actor: str) -> dict[str, Any]:
        current = self.get(proposal_id)
        if current is None:
            raise KeyError("proposal_not_found")
        if current["status"] != "READY":
            raise ValueError("approval can only be attached to READY proposal")
        now = _now()
        with self._connection() as con:
            con.execute("UPDATE ai_trade_proposals SET approval_id=?,updated_at=? WHERE proposal_id=?", (str(approval_id), now, proposal_id))
            con.execute("INSERT INTO ai_trade_proposal_events(proposal_id,event_type,actor,details_json,created_at) VALUES(?,?,?,?,?)", (proposal_id, "APPROVAL_ATTACHED", actor, _json({"approval_id": approval_id}), now))
            row = con.execute("SELECT * FROM ai_trade_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            if row is None:
                raise KeyError("proposal_not_found")
            return self._decode(row)

    def events(self, proposal_id: str) -> list[dict[str, Any]]:
        with self._connection() as con:
            return [dict(row) for row in con.execute("SELECT * FROM ai_trade_proposal_events WHERE proposal_id=? ORDER BY event_id", (proposal_id,)).fetchall()]

    def revisions(self, proposal_id: str) -> list[dict[str, Any]]:
        with self._connection() as con:
            rows = con.execute("SELECT * FROM ai_trade_proposal_revisions WHERE proposal_id=? ORDER BY revision", (proposal_id,)).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def ready_transition_check(proposal: Mapping[str, Any]) -> dict[str, Any]:
        """Return the fail-closed gate for the human READY lane."""
        source = proposal.get("source") if isinstance(proposal.get("source"), Mapping) else {}
        if not str(proposal.get("source_scan_id") or "").strip():
            return {"eligible": False, "reason_code": "SOURCE_PROVENANCE_UNVERIFIED", "reason": "Authoritative source_scan_id is missing."}
        lifecycle = str(proposal.get("scanner_lifecycle") or source.get("lifecycle_status") or "").upper()
        if lifecycle in {"ENTRY_TRIGGERED", "INVALIDATED", "EXPIRED", "AMBIGUOUS_INTRABAR_ORDER", "NO_SETUP"}:
            return {"eligible": False, "reason_code": "SIGNAL_NOT_CURRENTLY_READY", "reason": f"Scanner lifecycle {lifecycle or 'UNKNOWN'} cannot be promoted to READY."}
        if lifecycle not in {"PENDING_ENTRY", "READY"}:
            return {"eligible": False, "reason_code": "SIGNAL_LIFECYCLE_UNVERIFIED", "reason": f"Scanner lifecycle {lifecycle or 'UNKNOWN'} is not a current reviewable setup."}
        if source.get("data_quality_complete") is not True:
            missing = source.get("missing_quality_data") or []
            suffix = ": " + ", ".join(map(str, missing)) if missing else ""
            return {"eligible": False, "reason_code": "DATA_QUALITY_INCOMPLETE", "reason": "Authoritative scanner data quality is incomplete" + suffix + "."}
        timestamp = proposal.get("source_timestamp") or source.get("scan_timestamp")
        if not timestamp:
            return {"eligible": False, "reason_code": "SOURCE_FRESHNESS_UNVERIFIED", "reason": "Authoritative source timestamp is missing."}
        try:
            observed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
        except (TypeError, ValueError):
            return {"eligible": False, "reason_code": "SOURCE_FRESHNESS_UNVERIFIED", "reason": "Authoritative source timestamp is invalid."}
        if age_seconds < 0 or age_seconds > 24 * 60 * 60:
            return {"eligible": False, "reason_code": "STALE_SOURCE_OBSERVATION", "reason": "Authoritative scanner observation is outside the 24-hour freshness window."}
        return {"eligible": True, "reason_code": None, "reason": "Current source-verified setup passed the human READY gate."}
