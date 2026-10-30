from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from src.decision.audit import DecisionAudit
from src.decision.models import DecisionCandidate
from src.decision import telegram_approval as approval_module
from src.decision.telegram_approval import (
    APPROVED,
    APPROVAL_JOB_PENDING,
    APPROVAL_JOB_PROCESSING,
    PREPARED,
    PENDING_APPROVAL,
    EXECUTION_READY,
    EXPIRED,
    REJECTED,
    REVALIDATION_FAILED,
    RISK_REJECTED,
    ApprovalStore,
    callback_data,
    expected_latest_completed_daily_session,
    fresh_scanner_revalidate,
    plan_hash,
    parse_callback_data,
    telegram_controls,
    telegram_status_message,
)


def candidate() -> DecisionCandidate:
    return DecisionCandidate(
        candidate_id="gerchik-setup-test", created_at=datetime.now(timezone.utc), asset_class="stock",
        symbol="GSHD", strategy="PRB1", direction="long", entry=45.4851, stop=43.2574,
        target=49.9406, level_price=44.0, reward_risk=2.0, confidence=0.6587,
        metadata={"identity_status": "stable"},
    )


def make_store(tmp_path, *, preparation_ttl_seconds=300):
    audit = DecisionAudit(tmp_path / "decision.db")
    audit.record_candidate(candidate())
    return ApprovalStore(
        tmp_path / "decision.db", ttl_seconds=120,
        preparation_ttl_seconds=preparation_ttl_seconds,
        allowed_user_ids=("u1",), allowed_chat_ids=("c1",), paper_execution_enabled=True,
    )


def deliver(store: ApprovalStore, approval_id: str):
    return store.confirm_delivery(approval_id, chat_id="c1", message_id="m1")


def test_legacy_all_history_uniqueness_migrates_to_active_only(tmp_path):
    path = tmp_path / "decision.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE trade_approvals (
            approval_id TEXT PRIMARY KEY, setup_id TEXT NOT NULL, plan_hash TEXT NOT NULL,
            scanner_run_id TEXT, symbol TEXT NOT NULL, strategy TEXT NOT NULL, direction TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            approved_at TEXT, rejected_at TEXT, executed_at TEXT,
            telegram_chat_id TEXT, telegram_user_id TEXT, telegram_message_id TEXT,
            telegram_callback_id TEXT, approval_source TEXT NOT NULL,
            execution_intent_id TEXT, broker_order_id TEXT, failure_code TEXT, failure_message TEXT,
            candidate_json TEXT NOT NULL, UNIQUE(setup_id, plan_hash)
        );
    """)
    con.close()
    ApprovalStore(path)
    con = sqlite3.connect(path)
    schema = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='trade_approvals'").fetchone()[0]
    indexes = {row[1] for row in con.execute("PRAGMA index_list(trade_approvals)")}
    con.close()
    assert "UNIQUE(setup_id, plan_hash)" not in schema.replace(" ", "").replace("\n", "").replace("\t", "")
    assert "uq_trade_approvals_active_identity" in indexes


def test_native_callback_contract_is_opaque_and_deterministic():
    approval_id = "approval_" + "a" * 32
    assert parse_callback_data(f"approve:{approval_id}") == ("approve", approval_id)
    controls = telegram_controls(approval_id)
    callbacks = [button["callback_data"] for row in controls for button in row]
    assert callbacks == [f"approve:{approval_id}", f"reject:{approval_id}", f"details:{approval_id}"]
    assert all("GSHD" not in value and "entry" not in value for value in callbacks)
    assert "EXECUTED" in telegram_status_message("EXECUTED", approval_id=approval_id)
    for malformed in ("approve", "approve:not-an-id", "unknown:" + approval_id):
        try:
            parse_callback_data(malformed)
            assert False, "malformed callback must fail"
        except ValueError:
            pass


def test_create_message_and_callback_reference_only(tmp_path):
    store = make_store(tmp_path)
    result = store.create(candidate().candidate_id, scanner_run_id="run-1")
    assert result.status == PREPARED
    assert "TTL begins after card delivery" in result.message
    delivered = deliver(store, result.approval_id)
    assert delivered.status == PENDING_APPROVAL
    assert callback_data("approve", result.approval_id) == f"approve:{result.approval_id}"
    assert candidate().symbol not in callback_data("approve", result.approval_id)


def test_fresh_undelivered_prepared_approval_is_reused(tmp_path):
    store = make_store(tmp_path, preparation_ttl_seconds=60)
    first = store.create(candidate().candidate_id)
    second = store.create(candidate().candidate_id)
    assert second.approval_id == first.approval_id
    assert store.get(first.approval_id)["status"] == PREPARED


def test_stale_undelivered_prepared_approval_is_retired_and_replaced(tmp_path):
    store = make_store(tmp_path, preparation_ttl_seconds=60)
    first = store.create(candidate().candidate_id, scanner_run_id="old-run")
    old_created = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with store.connection() as con:
        con.execute(
            "UPDATE trade_approvals SET created_at=?,expires_at=? WHERE approval_id=?",
            (old_created, old_created, first.approval_id),
        )

    second = store.create(candidate().candidate_id, scanner_run_id="new-run")
    old = store.get(first.approval_id)
    assert second.approval_id != first.approval_id
    assert second.status == PREPARED
    assert old["status"] == EXPIRED
    assert old["failure_code"] == "preparation_expired"
    assert old["failure_message"] == "undelivered preparation TTL elapsed"
    with store.connection() as con:
        events = [row[0] for row in con.execute(
            "SELECT event_type FROM trade_approval_events WHERE approval_id=? ORDER BY event_id",
            (first.approval_id,),
        ).fetchall()]
    assert "PREPARATION_EXPIRED" in events


def test_active_pending_approval_is_reused(tmp_path):
    store = make_store(tmp_path, preparation_ttl_seconds=1)
    first = store.create(candidate().candidate_id)
    deliver(store, first.approval_id)
    second = store.create(candidate().candidate_id)
    assert second.approval_id == first.approval_id
    assert second.status == PENDING_APPROVAL


def test_stale_prepared_retirement_does_not_leave_active_identity_conflict(tmp_path):
    store = make_store(tmp_path, preparation_ttl_seconds=60)
    first = store.create(candidate().candidate_id)
    old_created = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with store.connection() as con:
        con.execute("UPDATE trade_approvals SET created_at=? WHERE approval_id=?", (old_created, first.approval_id))
    second = store.create(candidate().candidate_id)
    with store.connection() as con:
        active = con.execute(
            "SELECT count(*) FROM trade_approvals WHERE setup_id=? AND plan_hash=? "
            "AND status IN ('PREPARED','PENDING_APPROVAL')",
            (candidate().candidate_id, plan_hash(candidate())),
        ).fetchone()[0]
    assert second.approval_id != first.approval_id
    assert active == 1


def test_terminal_card_reconciliation_returns_persisted_message_correlation(tmp_path):
    store = make_store(tmp_path)
    prepared = store.create(candidate().candidate_id, scanner_run_id="run-terminal")
    deliver(store, prepared.approval_id)
    store.decide(prepared.approval_id, approve=True, user_id="u1", chat_id="c1", callback_id="cb1")
    with sqlite3.connect(tmp_path / "decision.db") as con:
        con.execute(
            "UPDATE trade_approvals SET status=?, failure_code=?, failure_message=? WHERE approval_id=?",
            (RISK_REJECTED, "risk_gate_rejected", "market_closed;reward_risk_too_low", prepared.approval_id),
        )
    updates = store.list_telegram_card_reconciliation()
    row = next(item for item in updates if item["approval_id"] == prepared.approval_id)
    assert row["status"] == RISK_REJECTED
    assert row["telegram_chat_id"] == "c1"
    assert row["telegram_message_id"] == "m1"
    assert row["failure_message"] == "market_closed;reward_risk_too_low"


def test_only_delivered_pending_approval_is_actionable(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    ignored = store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1")
    assert ignored.status == PREPARED
    assert store.get(approval.approval_id)["execution_intent_id"] is None
    deliver(store, approval.approval_id)
    approved = store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1")
    assert approved.status == APPROVED


def test_preparation_delay_does_not_consume_human_review_ttl(tmp_path):
    store = make_store(tmp_path, preparation_ttl_seconds=7200)
    approval = store.create(candidate().candidate_id)
    assert approval.status == PREPARED
    with store.connection() as con:
        con.execute("UPDATE trade_approvals SET created_at=?,expires_at=? WHERE approval_id=?", ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), approval.approval_id))
    assert store.expire_stale() == 0
    assert store.get(approval.approval_id)["status"] == PREPARED
    delivered = deliver(store, approval.approval_id)
    row = store.get(approval.approval_id)
    assert delivered.status == PENDING_APPROVAL
    assert datetime.fromisoformat(row["expires_at"]) > datetime.now(timezone.utc)
    assert row["delivered_at"] is not None


def test_delivery_starts_once_and_details_never_extends_ttl(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    assert store.get(approval.approval_id)["delivered_at"] is None
    deliver(store, approval.approval_id)
    first = store.get(approval.approval_id)
    # Read-only DETAILS maps to get(), which may expire but never extends TTL.
    assert store.get(approval.approval_id)["expires_at"] == first["expires_at"]
    duplicate = deliver(store, approval.approval_id)
    second = store.get(approval.approval_id)
    assert duplicate.status == PENDING_APPROVAL
    assert second["expires_at"] == first["expires_at"]
    assert second["delivered_at"] == first["delivered_at"]


def test_ack_unknown_remains_prepared_until_explicit_reconciliation(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    recorded = store.record_delivery_ack_unknown(approval.approval_id, chat_id="c1", reason="adapter response timeout")
    row = store.get(approval.approval_id)
    assert recorded.status == PREPARED
    assert row["status"] == PREPARED
    assert row["delivered_at"] is None
    assert row["failure_code"] == "delivery_ack_unknown"
    assert row["execution_intent_id"] is None
    delivered = deliver(store, approval.approval_id)
    assert delivered.status == PENDING_APPROVAL
    assert store.get(approval.approval_id)["delivered_at"] is not None


def test_failed_delivery_never_creates_review_window(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    row = store.get(approval.approval_id)
    assert row["status"] == PREPARED
    assert row["delivered_at"] is None
    assert store.expire_stale() == 0


def test_delivered_expiration_still_fails_closed(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    deliver(store, approval.approval_id)
    expired_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with store.connection() as con:
        con.execute("UPDATE trade_approvals SET expires_at=? WHERE approval_id=?", (expired_at, approval.approval_id))
    assert store.get(approval.approval_id)["status"] == EXPIRED
    result = store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1")
    assert result.status == EXPIRED


def test_expired_approval_is_not_reused_and_history_is_immutable(tmp_path):
    store = make_store(tmp_path)
    first = store.create(candidate().candidate_id)
    deliver(store, first.approval_id)
    expired_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with store.connection() as con:
        con.execute("UPDATE trade_approvals SET expires_at=? WHERE approval_id=?", (expired_at, first.approval_id))
    second = store.create(candidate().candidate_id)
    deliver(store, second.approval_id)
    assert second.approval_id != first.approval_id
    assert datetime.fromisoformat(store.get(second.approval_id)["expires_at"]) > datetime.now(timezone.utc)
    assert store.get(first.approval_id)["status"] == EXPIRED
    assert store.get(first.approval_id)["expires_at"] == expired_at


def test_rejected_and_executed_approvals_are_not_reused(tmp_path):
    store = make_store(tmp_path)
    rejected = store.create(candidate().candidate_id)
    deliver(store, rejected.approval_id)
    store.decide(rejected.approval_id, approve=False, user_id="u1", chat_id="c1")
    fresh = store.create(candidate().candidate_id)
    assert fresh.approval_id != rejected.approval_id
    with store.connection() as con:
        con.execute("UPDATE trade_approvals SET status=? WHERE approval_id=?", ("EXECUTED", fresh.approval_id))
    newest = store.create(candidate().candidate_id)
    assert newest.approval_id != fresh.approval_id


def test_decided_and_terminal_approvals_are_not_reused(tmp_path):
    statuses = (
        "APPROVED", "REJECTED", "EXPIRED", "REVALIDATION_FAILED", "RISK_REJECTED",
        "EXECUTION_READY", "SUBMITTING", "EXECUTED", "EXECUTION_FAILED", "CANCELLED",
    )
    store = make_store(tmp_path)

    for status in statuses:
        prior = store.create(candidate().candidate_id)
        if status == "APPROVED":
            deliver(store, prior.approval_id)
            decided = store.decide(prior.approval_id, approve=True, user_id="u1", chat_id="c1")
            assert decided.status == APPROVED
        elif status == "REJECTED":
            deliver(store, prior.approval_id)
            decided = store.decide(prior.approval_id, approve=False, user_id="u1", chat_id="c1")
            assert decided.status == REJECTED
        else:
            with store.connection() as con:
                con.execute("UPDATE trade_approvals SET status=? WHERE approval_id=?", (status, prior.approval_id))

        fresh = store.create(candidate().candidate_id, scanner_run_id=f"new-run-{status.lower()}")
        assert fresh.approval_id != prior.approval_id
        assert fresh.status == PREPARED
        assert store.get(fresh.approval_id)["setup_id"] == candidate().candidate_id
        assert store.get(fresh.approval_id)["plan_hash"] == plan_hash(candidate())


def test_active_preparation_states_are_reused(tmp_path):
    store = make_store(tmp_path)
    prepared = store.create(candidate().candidate_id, scanner_run_id="run-prepared")
    assert store.create(candidate().candidate_id, scanner_run_id="run-prepared-duplicate").approval_id == prepared.approval_id

    deliver(store, prepared.approval_id)
    assert store.create(candidate().candidate_id, scanner_run_id="run-pending-duplicate").approval_id == prepared.approval_id


def test_concurrent_prepare_creates_at_most_one_active_approval(tmp_path):
    store = make_store(tmp_path)
    def create_one():
        return store.create(candidate().candidate_id).approval_id
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: create_one(), range(4)))
    assert len(set(ids)) == 1
    with store.connection() as con:
        assert con.execute(
            "SELECT count(*) FROM trade_approvals WHERE setup_id=? AND status IN ('PREPARED','PENDING_APPROVAL')",
            (candidate().candidate_id,),
        ).fetchone()[0] == 1


def test_authorized_decisions_are_idempotent(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    deliver(store, approval.approval_id)
    approved = store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1")
    again = store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1")
    assert approved.status == APPROVED
    assert again.status == APPROVED
    assert store.get(approval.approval_id)["status"] == APPROVED


def test_approved_decision_enqueues_one_durable_worker_job(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    deliver(store, approval.approval_id)

    first = store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1", callback_id="cb-1")
    second = store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1", callback_id="cb-1")

    job = store.approval_job(approval.approval_id)
    assert first.status == APPROVED and second.status == APPROVED
    assert job["status"] == APPROVAL_JOB_PENDING
    claimed = store.claim_approved_jobs()
    assert len(claimed) == 1
    assert claimed[0]["job_id"] == job["job_id"]
    assert claimed[0]["status"] == APPROVAL_JOB_PROCESSING
    assert claimed[0]["claimed_at"]
    assert store.claim_approved_jobs() == []


def test_unauthorized_and_reject_are_fail_closed(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    deliver(store, approval.approval_id)
    try:
        store.decide(approval.approval_id, approve=True, user_id="bad", chat_id="c1")
        assert False, "unauthorized approval must fail"
    except PermissionError:
        pass
    rejected = store.decide(approval.approval_id, approve=False, user_id="u1", chat_id="c1")
    assert rejected.status == REJECTED


def test_revalidation_and_intent_are_once_only(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    deliver(store, approval.approval_id)
    store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1")
    enqueued = []
    result = store.process_approved(
        approval.approval_id,
        revalidate=lambda _: {"valid": True},
        risk_gate=lambda _: {"approved": True, "quantity": 7, "account_id": "DU123", "paper_verified": True, "account_allowed": True},
        enqueue=lambda intent: enqueued.append(intent),
    )
    assert result.status == EXECUTION_READY and len(enqueued) == 1
    reviewed = store.get(approval.approval_id)
    assert reviewed["reviewed_quantity"] == 7
    assert reviewed["risk_amount"] == pytest.approx(abs(candidate().entry - candidate().stop) * 7)
    assert reviewed["reward_amount"] == pytest.approx(abs(candidate().target - candidate().entry) * 7)
    second = store.process_approved(
        approval.approval_id,
        revalidate=lambda _: {"valid": True},
        risk_gate=lambda _: {"approved": True, "quantity": 7, "paper_verified": True, "account_allowed": True},
        enqueue=lambda _: enqueued.append("duplicate"),
    )
    assert second.status == EXECUTION_READY and enqueued != ["duplicate"]
    with store.connection() as con:
        events = [
            row["event_type"]
            for row in con.execute(
                "SELECT event_type FROM trade_approval_events WHERE approval_id=? ORDER BY event_id",
                (approval.approval_id,),
            )
        ]
    assert events.index("REVALIDATION_PASSED") < events.index("EXECUTION_INTENT_CREATED")


def test_reconcile_approved_after_callback_timeout_creates_one_intent(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    deliver(store, approval.approval_id)
    store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1", callback_id="cb-1")
    with store.connection() as con:
        store._event(con, approval.approval_id, "REVALIDATION_PASSED", source="stock_screener")

    enqueued = []
    risk = lambda _: {"approved": True, "quantity": 7, "account_id": "DU123", "paper_verified": True, "account_allowed": True}
    result = store.reconcile_approved_execution(approval.approval_id, risk_gate=risk, enqueue=enqueued.append)
    again = store.reconcile_approved_execution(approval.approval_id, risk_gate=risk, enqueue=lambda item: enqueued.append("duplicate"))

    assert result.status == EXECUTION_READY
    assert result.execution_intent_id
    assert again.status == EXECUTION_READY
    assert again.execution_intent_id == result.execution_intent_id
    assert len(enqueued) == 1
    assert store.get(approval.approval_id)["execution_intent_id"] == result.execution_intent_id


def test_reconcile_requires_recorded_revalidation(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    with store.connection() as con:
        con.execute("UPDATE trade_approvals SET status=? WHERE approval_id=?", (APPROVED, approval.approval_id))
    with pytest.raises(ValueError, match="revalidation_not_recorded"):
        store.reconcile_approved_execution(approval.approval_id, risk_gate=lambda _: {}, enqueue=lambda _: None)


def test_reconcile_fails_closed_on_existing_broker_evidence(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    with store.connection() as con:
        con.execute("UPDATE trade_approvals SET status=?,broker_order_id=? WHERE approval_id=?", (APPROVED, "101", approval.approval_id))
        store._event(con, approval.approval_id, "REVALIDATION_PASSED")
    with pytest.raises(ValueError, match="broker_evidence_exists"):
        store.reconcile_approved_execution(approval.approval_id, risk_gate=lambda _: {}, enqueue=lambda _: None)


def test_risk_reject_creates_no_intent(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    deliver(store, approval.approval_id)
    store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1")
    result = store.process_approved(
        approval.approval_id, revalidate=lambda _: {"valid": True},
        risk_gate=lambda _: {"approved": False, "quantity": 0, "reasons": ["position_cap"]}, enqueue=lambda _: None,
    )
    assert result.status == RISK_REJECTED
    assert store.get(approval.approval_id)["execution_intent_id"] is None


def test_unchanged_setup_proceeds_through_backend_revalidation(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    deliver(store, approval.approval_id)
    store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1")
    result = store.process_approved(
        approval.approval_id, revalidate=lambda item: {"valid": True, "candidate": item},
        risk_gate=lambda _: {"approved": True, "quantity": 1, "paper_verified": True, "account_allowed": True}, enqueue=lambda _: None,
    )
    assert result.status == EXECUTION_READY


def test_setup_disappeared_fails_closed(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    deliver(store, approval.approval_id)
    store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1")
    result = store.process_approved(approval.approval_id, revalidate=lambda _: {"valid": False, "reasons": ["setup_disappeared"]}, risk_gate=lambda _: {"approved": True, "quantity": 1}, enqueue=lambda _: None)
    assert result.status == REVALIDATION_FAILED


def test_fresh_scanner_revalidate_distinguishes_stale_source(tmp_path, monkeypatch):
    candidate_value = candidate()
    candidate_value = candidate_value.__class__(**{**candidate_value.__dict__, "strategy": "INTRADAY"})
    monkeypatch.setattr(
        approval_module,
        "refresh_current_source_snapshot",
        lambda **_: {
            "sources": {"stock_screener": [{
                "scan_id": "fresh-scan",
                "source": "stock_screener",
                "symbol": "GSHD",
                "strategy": "PRB1",
                "signal_state": "READY",
                "candidate_class": "EXECUTABLE_ENTRY_SIGNAL",
            }]},
            "source_metadata": {
                "stock_screener": {"available": True, "data_age_seconds": 31.0},
            },
        },
    )
    result = fresh_scanner_revalidate(candidate_value, max_data_age_seconds=30.0)
    assert result["valid"] is False
    assert result["reasons"] == ("source_data_stale", "stock_screener:31.0>30")
    assert result["stale_sources"] == ("stock_screener:31.0>30",)


def test_fresh_scanner_revalidate_passes_matching_fresh_candidate(monkeypatch):
    candidate_value = candidate()
    monkeypatch.setattr(
        approval_module,
        "refresh_current_source_snapshot",
        lambda **_: {
            "sources": {"stock_screener": [{
                "scan_id": "fresh-scan",
                "source": "stock_screener",
                "symbol": "GSHD",
                "strategy": "PRB1",
                "signal_state": "READY",
                "candidate_class": "EXECUTABLE_ENTRY_SIGNAL",
            }]},
            "source_metadata": {
                "stock_screener": {
                    "available": True,
                    "data_age_seconds": 999999.0,
                    "latest_bar_timestamp": "2026-10-02",
                },
            },
        },
    )
    monkeypatch.setattr(approval_module, "snapshot_to_candidate", lambda _: (candidate_value, ("ENTER",)))
    result = fresh_scanner_revalidate(
        candidate_value,
        max_data_age_seconds=30.0,
        current_time=datetime(2026, 10, 3, 12, tzinfo=ZoneInfo("America/New_York")),
    )
    assert result["valid"] is True
    assert result["candidate"] == candidate_value
    assert result["source"] == "stock_screener"


def _install_daily_source(monkeypatch, latest_bar_timestamp, candidate_value=None):
    candidate_value = candidate_value or candidate()
    monkeypatch.setattr(
        approval_module,
        "refresh_current_source_snapshot",
        lambda **_: {
            "sources": {"stock_screener": [{
                "scan_id": "daily-scan",
                "source": "stock_screener",
                "symbol": candidate_value.symbol,
                "strategy": candidate_value.strategy,
                "signal_state": "READY",
                "candidate_class": "EXECUTABLE_ENTRY_SIGNAL",
            }]},
            "source_metadata": {
                "stock_screener": {
                    "available": True,
                    "data_age_seconds": 999999.0,
                    "latest_bar_timestamp": latest_bar_timestamp,
                },
            },
        },
    )
    monkeypatch.setattr(approval_module, "snapshot_to_candidate", lambda _: (candidate_value, ("ENTER",)))
    return candidate_value


def test_daily_friday_close_is_valid_on_saturday(monkeypatch):
    value = _install_daily_source(monkeypatch, "2026-10-02")
    now = datetime(2026, 10, 3, 12, tzinfo=ZoneInfo("America/New_York"))
    assert expected_latest_completed_daily_session(now).isoformat() == "2026-10-02"
    assert fresh_scanner_revalidate(value, current_time=now)["valid"] is True


def test_daily_friday_close_is_valid_on_sunday(monkeypatch):
    value = _install_daily_source(monkeypatch, "2026-10-02")
    now = datetime(2026, 10, 4, 12, tzinfo=ZoneInfo("America/New_York"))
    assert expected_latest_completed_daily_session(now).isoformat() == "2026-10-02"
    assert fresh_scanner_revalidate(value, current_time=now)["valid"] is True


def test_daily_friday_close_is_valid_monday_pre_market(monkeypatch):
    value = _install_daily_source(monkeypatch, "2026-10-02")
    now = datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    assert expected_latest_completed_daily_session(now).isoformat() == "2026-10-02"
    assert fresh_scanner_revalidate(value, current_time=now)["valid"] is True


def test_daily_thursday_data_is_stale_on_saturday(monkeypatch):
    value = _install_daily_source(monkeypatch, "2026-10-01")
    now = datetime(2026, 10, 3, 12, tzinfo=ZoneInfo("America/New_York"))
    result = fresh_scanner_revalidate(value, current_time=now)
    assert result["valid"] is False
    assert result["reasons"][0] == "source_data_stale"
    assert any("latest=2026-10-01<expected=2026-10-02" in reason for reason in result["reasons"])


def test_daily_missing_latest_session_is_stale(monkeypatch):
    value = _install_daily_source(monkeypatch, None)
    now = datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    result = fresh_scanner_revalidate(value, current_time=now)
    assert result["valid"] is False
    assert result["reasons"] == ("source_data_stale", "stock_screener:latest=unknown<expected=2026-10-02")


def test_daily_matching_latest_session_same_setup_passes(monkeypatch):
    value = _install_daily_source(monkeypatch, "2026-10-02")
    now = datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    result = fresh_scanner_revalidate(value, current_time=now)
    assert result["valid"] is True
    assert result["candidate"] == value


def test_daily_matching_session_with_changed_plan_fails_closed(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    deliver(store, approval.approval_id)
    store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1")
    original = candidate()
    changed = original.__class__(**{**original.__dict__, "entry": 99.0})
    _install_daily_source(monkeypatch, "2026-10-02", changed)
    result = store.backend_revalidate_approval(approval.approval_id)
    assert result.status == REVALIDATION_FAILED
    assert result.reasons == ("plan_hash_changed",)
    assert store.get(approval.approval_id)["failure_code"] == "plan_hash_changed"


def test_status_no_longer_actionable_fails_closed(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    deliver(store, approval.approval_id)
    store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1")
    result = store.process_approved(approval.approval_id, revalidate=lambda _: {"valid": False, "reasons": ["setup_not_actionable"]}, risk_gate=lambda _: {"approved": True, "quantity": 1}, enqueue=lambda _: None)
    assert result.status == REVALIDATION_FAILED


def test_stale_market_data_fails_closed(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    deliver(store, approval.approval_id)
    store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1")
    result = store.process_approved(approval.approval_id, revalidate=lambda _: {"valid": False, "reasons": ["data_stale"]}, risk_gate=lambda _: {"approved": True, "quantity": 1}, enqueue=lambda _: None)
    assert result.status == REVALIDATION_FAILED


def test_duplicate_approve_during_revalidation_has_one_intent(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    deliver(store, approval.approval_id)
    first = store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1")
    second = store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1")
    assert first.status == APPROVED and second.status == APPROVED
    intents = []
    result = store.process_approved(approval.approval_id, revalidate=lambda _: {"valid": True}, risk_gate=lambda _: {"approved": True, "quantity": 1, "paper_verified": True, "account_allowed": True}, enqueue=lambda item: intents.append(item))
    again = store.process_approved(approval.approval_id, revalidate=lambda _: {"valid": True}, risk_gate=lambda _: {"approved": True, "quantity": 1, "paper_verified": True, "account_allowed": True}, enqueue=lambda item: intents.append(item))
    assert result.status == EXECUTION_READY and again.status == EXECUTION_READY and len(intents) == 1


def test_broker_result_metadata_is_persisted_for_card_and_dashboard(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    deliver(store, approval.approval_id)
    store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1")
    store.process_approved(
        approval.approval_id, revalidate=lambda _: {"valid": True},
        risk_gate=lambda _: {"approved": True, "quantity": 3, "paper_verified": True, "account_allowed": True},
        enqueue=lambda _: None,
    )
    assert store.mark_execution(
        approval.approval_id, "SUBMITTING", broker_order_id="101",
        broker_details={"account_id": "DU123", "broker_status": "PreSubmitted", "stop_order_id": 102,
                        "limit_order_id": 103, "broker_statuses": {"market_order": "PreSubmitted"},
                        "submitted_at": "2026-10-03T12:00:00+00:00", "outside_rth": False,
                        "submission_outcome": "KNOWN"},
    )
    row = store.get(approval.approval_id)
    assert row["broker_account_id"] == "DU123"
    assert row["broker_order_id"] == "101"
    assert row["broker_status"] == "PreSubmitted"
    assert row["broker_stop_order_id"] == "102"
    assert row["broker_limit_order_id"] == "103"
    assert row["broker_outside_rth"] == 0


def test_plan_hash_changes_fail_revalidation(tmp_path):
    store = make_store(tmp_path)
    approval = store.create(candidate().candidate_id)
    deliver(store, approval.approval_id)
    store.decide(approval.approval_id, approve=True, user_id="u1", chat_id="c1")
    # Change the authoritative candidate before processing.
    changed = candidate().to_dict(); changed["entry"] = 99.0
    with store.connection() as con:
        import json
        con.execute("UPDATE candidates SET candidate_json=? WHERE candidate_id=?", (json.dumps(changed), candidate().candidate_id))
    result = store.process_approved(
        approval.approval_id, revalidate=lambda _: {"valid": True},
        risk_gate=lambda _: {"approved": True, "quantity": 1}, enqueue=lambda _: None,
    )
    assert result.status == REVALIDATION_FAILED
