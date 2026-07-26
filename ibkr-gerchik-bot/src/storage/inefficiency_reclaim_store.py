"""SQLite audit store for IRS zones, setups, transitions, and execution records."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from src.strategy.inefficiency_reclaim import (
    InefficiencyZone,
    SetupState,
    StrategyCandidate,
)
from src.strategy.inefficiency_reclaim_state import StateTransition, advance_setup_state


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "001_inefficiency_reclaim_up.sql"
)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Cannot serialize {type(value).__name__}.")


def _json(value: Any) -> str:
    return json.dumps(value, default=_json_default, sort_keys=True, separators=(",", ":"))


class ImmutableZoneError(ValueError):
    """Raised when a scan attempts to move persisted zone boundaries."""


class InefficiencyReclaimStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        schema = MIGRATION_PATH.read_text(encoding="utf-8")
        with self.connection() as connection:
            connection.executescript(schema)

    def record_scanner_run(
        self,
        *,
        run_id: str,
        mode: str,
        started_at: datetime,
        strategy_version: str,
        config_snapshot: Mapping[str, Any],
        git_commit: str | None = None,
        status: str = "RUNNING",
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO scanner_runs
                    (run_id, mode, started_at, status, strategy_version, config_json, git_commit)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    mode,
                    started_at.isoformat(),
                    status,
                    strategy_version,
                    _json(config_snapshot),
                    git_commit,
                ),
            )

    def finish_scanner_run(
        self,
        run_id: str,
        *,
        finished_at: datetime,
        status: str,
        symbols_scanned: int,
        candidates: int,
        rejections: int,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE scanner_runs
                   SET finished_at = ?, status = ?, symbols_scanned = ?,
                       candidates = ?, rejections = ?, diagnostics_json = ?
                 WHERE run_id = ?
                """,
                (
                    finished_at.isoformat(),
                    status,
                    symbols_scanned,
                    candidates,
                    rejections,
                    _json(diagnostics or {}),
                    run_id,
                ),
            )

    def upsert_zone(self, zone: InefficiencyZone) -> bool:
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT zone_low, zone_high FROM inefficiency_zones WHERE zone_id = ?",
                (zone.zone_id,),
            ).fetchone()
            if existing is not None:
                if Decimal(existing["zone_low"]) != zone.zone_low or Decimal(existing["zone_high"]) != zone.zone_high:
                    raise ImmutableZoneError(f"Zone {zone.zone_id} boundaries are immutable.")
                return False
            connection.execute(
                """
                INSERT INTO inefficiency_zones (
                    zone_id, symbol, direction, zone_type, source_timeframe,
                    created_at, displacement_bar_time, zone_low, zone_high,
                    zone_mid, zone_width, zone_width_atr,
                    displacement_atr_multiple, body_ratio, close_location,
                    relative_volume, source_bar_ids_json, structure_reference_id,
                    expires_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    zone.zone_id,
                    zone.symbol,
                    zone.direction.value,
                    zone.zone_type.value,
                    zone.source_timeframe,
                    zone.created_at.isoformat(),
                    zone.displacement_bar_time.isoformat(),
                    str(zone.zone_low),
                    str(zone.zone_high),
                    str(zone.zone_mid),
                    str(zone.zone_width),
                    str(zone.zone_width_atr),
                    str(zone.displacement_atr_multiple),
                    str(zone.body_ratio),
                    str(zone.close_location),
                    str(zone.relative_volume),
                    _json(zone.source_bar_ids),
                    zone.structure_reference_id,
                    zone.expires_at.isoformat(),
                    SetupState.INEFFICIENCY_REGISTERED.value,
                ),
            )
            return True

    def upsert_candidate(self, candidate: StrategyCandidate, *, run_id: str) -> bool:
        self.upsert_zone(candidate.zone)
        payload = candidate.to_dict()
        setup_id = candidate.setup_id
        with self.connection() as connection:
            existing_signal = connection.execute(
                "SELECT signal_id FROM strategy_signals WHERE signal_id = ?",
                (candidate.signal_id,),
            ).fetchone()
            existing_setup = connection.execute(
                "SELECT state FROM strategy_setups WHERE setup_id = ?",
                (setup_id,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO strategy_setups (
                    setup_id, signal_id, symbol, strategy, profile, direction,
                    zone_id, state, score, created_at, updated_at,
                    expires_at, strategy_version, run_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(setup_id) DO UPDATE SET
                    signal_id = excluded.signal_id,
                    updated_at = excluded.updated_at,
                    score = excluded.score,
                    expires_at = excluded.expires_at,
                    payload_json = excluded.payload_json
                """,
                (
                    setup_id,
                    candidate.signal_id,
                    candidate.symbol,
                    candidate.strategy,
                    candidate.profile.value,
                    candidate.direction.value,
                    candidate.zone.zone_id,
                    (
                        SetupState.SEARCHING.value
                        if existing_setup is None
                        else str(existing_setup["state"])
                    ),
                    str(candidate.score),
                    candidate.zone.created_at.isoformat(),
                    datetime.now(candidate.zone.created_at.tzinfo).isoformat(),
                    candidate.expires_at.isoformat() if candidate.expires_at else None,
                    candidate.strategy_version,
                    run_id,
                    _json(payload),
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO strategy_signals (
                    signal_id, setup_id, idempotency_key, symbol, strategy,
                    profile, direction, zone_id, confirmation_time, state,
                    score, payload_json, strategy_version, run_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.signal_id,
                    setup_id,
                    candidate.signal_id,
                    candidate.symbol,
                    candidate.strategy,
                    candidate.profile.value,
                    candidate.direction.value,
                    candidate.zone.zone_id,
                    candidate.confirmation.timestamp.isoformat() if candidate.confirmation else None,
                    candidate.state.value,
                    str(candidate.score),
                    _json(payload),
                    candidate.strategy_version,
                    run_id,
                    datetime.now(candidate.zone.created_at.tzinfo).isoformat(),
                ),
            )
            for reason in candidate.hard_rejections:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO strategy_rejections
                        (signal_id, reason, state, run_id, created_at, diagnostics_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.signal_id,
                        reason,
                        candidate.state.value,
                        run_id,
                        datetime.now(candidate.zone.created_at.tzinfo).isoformat(),
                        _json(candidate.diagnostics),
                    ),
                )
            if candidate.order_plan is not None:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO order_plans (
                        signal_id, order_ref, entry_stop, entry_limit, stop_price,
                        target_price, quantity, risk_cash, structural_r,
                        estimated_costs, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.signal_id,
                        f"IRS-{candidate.signal_id}",
                        str(candidate.order_plan.entry_stop),
                        str(candidate.order_plan.entry_limit),
                        str(candidate.order_plan.stop),
                        str(candidate.order_plan.target),
                        candidate.order_plan.quantity,
                        str(candidate.order_plan.risk_cash),
                        str(candidate.order_plan.structural_r),
                        str(candidate.order_plan.estimated_costs),
                        _json(payload["order_plan"]),
                        datetime.now(candidate.zone.created_at.tzinfo).isoformat(),
                    ),
                )
            current_state = (
                SetupState.SEARCHING
                if existing_setup is None
                else SetupState(str(existing_setup["state"]))
            )
            path = self._state_path(current_state, candidate.state)
            previous = current_state
            base_time = (
                candidate.confirmation.timestamp
                if candidate.confirmation is not None
                else candidate.zone.created_at
            )
            for offset, new_state in enumerate(path, start=1):
                exchange_timestamp = base_time + timedelta(microseconds=offset)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO strategy_state_transitions (
                        setup_id, previous_state, new_state, exchange_timestamp,
                        reason, source_bar_id, strategy_version, run_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        setup_id,
                        previous.value,
                        new_state.value,
                        exchange_timestamp.isoformat(),
                        "scanner lifecycle reconstruction",
                        candidate.zone.source_bar_ids[-1],
                        candidate.strategy_version,
                        run_id,
                    ),
                )
                previous = new_state
            if path:
                connection.execute(
                    "UPDATE strategy_setups SET state = ?, updated_at = ? WHERE setup_id = ?",
                    (previous.value, base_time.isoformat(), setup_id),
                )
            return existing_signal is None

    @staticmethod
    def _state_path(current: SetupState, target: SetupState) -> tuple[SetupState, ...]:
        if current == target:
            return ()
        terminal = {
            SetupState.POSITION_CLOSED,
            SetupState.EXPIRED,
            SetupState.INVALIDATED,
            SetupState.REJECTED_BY_DATA,
            SetupState.REJECTED_BY_REGIME,
            SetupState.REJECTED_BY_NEWS,
            SetupState.REJECTED_BY_LIQUIDITY,
            SetupState.REJECTED_BY_TARGET_SPACE,
            SetupState.REJECTED_BY_RISK,
            SetupState.CANCELLED,
        }
        if current in terminal:
            return ()
        linear = (
            SetupState.SEARCHING,
            SetupState.DISPLACEMENT_FOUND,
            SetupState.INEFFICIENCY_REGISTERED,
            SetupState.WAITING_FOR_RETRACE,
            SetupState.RETRACE_DETECTED,
            SetupState.WAITING_FOR_CONFIRMATION,
            SetupState.ENTRY_ARMED,
        )
        if target in terminal:
            try:
                current_index = linear.index(current)
            except ValueError:
                return ()
            waiting_index = linear.index(SetupState.WAITING_FOR_RETRACE)
            prefix = (
                linear[current_index + 1 : waiting_index + 1]
                if current_index < waiting_index
                else ()
            )
            return (*prefix, target)
        try:
            current_index = linear.index(current)
            target_index = linear.index(target)
        except ValueError:
            return ()
        if target_index <= current_index:
            return ()
        return linear[current_index + 1 : target_index + 1]

    def record_transition(self, transition: StateTransition) -> bool:
        with self.connection() as connection:
            current = connection.execute(
                "SELECT state FROM strategy_setups WHERE setup_id = ?",
                (transition.setup_id,),
            ).fetchone()
            if current is None:
                raise KeyError(f"Unknown IRS setup {transition.setup_id}.")
            if current["state"] != transition.previous_state.value:
                if current["state"] == transition.new_state.value:
                    return False
                raise ValueError(
                    f"Persisted state is {current['state']}, expected {transition.previous_state.value}."
                )
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO strategy_state_transitions (
                    setup_id, previous_state, new_state, exchange_timestamp,
                    reason, source_bar_id, strategy_version, run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transition.setup_id,
                    transition.previous_state.value,
                    transition.new_state.value,
                    transition.exchange_timestamp.isoformat(),
                    transition.reason,
                    transition.source_bar_id,
                    transition.strategy_version,
                    transition.run_id,
                ),
            )
            if cursor.rowcount:
                connection.execute(
                    "UPDATE strategy_setups SET state = ?, updated_at = ? WHERE setup_id = ?",
                    (
                        transition.new_state.value,
                        transition.exchange_timestamp.isoformat(),
                        transition.setup_id,
                    ),
                )
            return bool(cursor.rowcount)

    def has_order_ref(self, order_ref: str) -> bool:
        with self.connection() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM orders WHERE order_ref = ? LIMIT 1", (order_ref,)
                ).fetchone()
                is not None
            )

    def get_setup(self, setup_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM strategy_setups WHERE setup_id = ?",
                (setup_id,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["payload"] = json.loads(result["payload_json"])
            return result

    def get_order(self, order_ref: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM orders WHERE order_ref = ?",
                (order_ref,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["payload"] = json.loads(result["payload_json"])
            return result

    def list_orders(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM orders ORDER BY submitted_at, order_id"
            ).fetchall()
            return [
                {
                    **dict(row),
                    "payload": json.loads(row["payload_json"]),
                }
                for row in rows
            ]

    def record_order(
        self,
        *,
        signal_id: str,
        order_ref: str,
        broker_order_id: int,
        status: str,
        quantity: int,
        submitted_at: datetime,
        payload: Mapping[str, Any],
    ) -> bool:
        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO orders (
                    signal_id, order_ref, broker_order_id, status, quantity,
                    filled_quantity, submitted_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    signal_id,
                    order_ref,
                    broker_order_id,
                    status,
                    quantity,
                    submitted_at.isoformat(),
                    _json(payload),
                ),
            )
            return bool(cursor.rowcount)

    def update_order_status(
        self,
        order_ref: str,
        *,
        status: str,
        updated_at: datetime,
    ) -> bool:
        with self.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE orders
                   SET status = ?, updated_at = ?
                 WHERE order_ref = ?
                """,
                (status, updated_at.isoformat(), order_ref),
            )
            return bool(cursor.rowcount)

    def record_risk_snapshot(
        self,
        *,
        signal_id: str,
        captured_at: datetime,
        payload: Mapping[str, Any],
    ) -> bool:
        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO risk_snapshots
                    (signal_id, captured_at, payload_json)
                VALUES (?, ?, ?)
                """,
                (signal_id, captured_at.isoformat(), _json(payload)),
            )
            return bool(cursor.rowcount)

    def record_news_check(
        self,
        *,
        signal_id: str,
        symbol: str,
        checked_at: datetime,
        news_status: str,
        earnings_status: str,
        payload: Mapping[str, Any],
    ) -> bool:
        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO news_checks (
                    signal_id, symbol, checked_at, news_status,
                    earnings_status, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    symbol,
                    checked_at.isoformat(),
                    news_status,
                    earnings_status,
                    _json(payload),
                ),
            )
            return bool(cursor.rowcount)

    def record_fill(
        self,
        *,
        order_ref: str,
        execution_id: str,
        quantity: int,
        price: Decimal,
        filled_at: datetime,
        payload: Mapping[str, Any] | None = None,
    ) -> bool:
        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO fills
                    (order_ref, execution_id, quantity, price, filled_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    order_ref,
                    execution_id,
                    quantity,
                    str(price),
                    filled_at.isoformat(),
                    _json(payload or {}),
                ),
            )
            if cursor.rowcount:
                connection.execute(
                    """
                    UPDATE orders
                       SET filled_quantity = (
                           SELECT COALESCE(SUM(quantity), 0) FROM fills WHERE fills.order_ref = orders.order_ref
                       )
                     WHERE order_ref = ?
                    """,
                    (order_ref,),
                )
            return bool(cursor.rowcount)

    def active_setups(self) -> list[dict[str, Any]]:
        terminal = (
            SetupState.POSITION_CLOSED.value,
            SetupState.EXPIRED.value,
            SetupState.INVALIDATED.value,
            SetupState.CANCELLED.value,
            SetupState.REJECTED_BY_DATA.value,
            SetupState.REJECTED_BY_REGIME.value,
            SetupState.REJECTED_BY_NEWS.value,
            SetupState.REJECTED_BY_LIQUIDITY.value,
            SetupState.REJECTED_BY_TARGET_SPACE.value,
            SetupState.REJECTED_BY_RISK.value,
        )
        placeholders = ",".join("?" for _ in terminal)
        with self.connection() as connection:
            rows = connection.execute(
                f"""
                SELECT setup_id, symbol, direction, state, score, zone_id,
                       expires_at, updated_at, payload_json
                  FROM strategy_setups
                 WHERE state NOT IN ({placeholders})
                 ORDER BY updated_at DESC
                """,
                terminal,
            ).fetchall()
            return [
                {
                    **dict(row),
                    "payload": json.loads(row["payload_json"]),
                }
                for row in rows
            ]

    def expire_due(self, *, as_of: datetime, run_id: str) -> int:
        eligible = {
            SetupState.WAITING_FOR_RETRACE,
            SetupState.WAITING_FOR_CONFIRMATION,
            SetupState.ENTRY_ARMED,
        }
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT setup_id, state, expires_at
                  FROM strategy_setups
                 WHERE expires_at IS NOT NULL AND expires_at <= ?
                """,
                (as_of.isoformat(),),
            ).fetchall()
        expired = 0
        for row in rows:
            previous = SetupState(str(row["state"]))
            if previous not in eligible:
                continue
            transition = advance_setup_state(
                setup_id=str(row["setup_id"]),
                previous=previous,
                new=SetupState.EXPIRED,
                exchange_timestamp=as_of,
                reason="setup expiration reached",
                source_bar_id=None,
                run_id=run_id,
            )
            if self.record_transition(transition):
                expired += 1
        return expired

    def table_count(self, table: str) -> int:
        allowed = {
            "inefficiency_zones",
            "strategy_setups",
            "strategy_state_transitions",
            "strategy_signals",
            "strategy_rejections",
            "order_plans",
            "orders",
            "fills",
            "risk_snapshots",
            "news_checks",
            "scanner_runs",
        }
        if table not in allowed:
            raise ValueError("Unsupported IRS table.")
        with self.connection() as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
