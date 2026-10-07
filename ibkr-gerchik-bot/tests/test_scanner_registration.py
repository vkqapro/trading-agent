from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.decision.audit import DecisionAudit
from src.decision.models import DecisionCandidate
from src.decision import telegram_approval as approval_module
from src.decision.telegram_approval import ApprovalStore, plan_hash, register_canonical_setup
from src.mcp import trading_bot_server
from src.mcp.trading_bot_server import _load_scanner_candidate_key


def make_candidate(entry: float = 8.3301) -> DecisionCandidate:
    return DecisionCandidate(
        candidate_id="gerchik-registration-test",
        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        asset_class="stock",
        symbol="ALOY",
        strategy="LP1",
        direction="long",
        entry=entry,
        stop=7.6649,
        target=9.6605,
        level_price=8.0,
        level_type="support",
        reward_risk=2.0,
        confidence=0.6827,
        metadata={"status": "READY", "signal_bar": "2026-10-01"},
    )


def install_source(monkeypatch, candidate: DecisionCandidate):
    snapshot = {
        "scan_id": "canonical-test-run",
        "source": "stock_screener",
        "symbol": candidate.symbol,
        "strategy": candidate.strategy,
        "signal_state": "READY",
        "candidate_class": "EXECUTABLE_ENTRY_SIGNAL",
        "entry": candidate.entry,
        "stop": candidate.stop,
        "target": candidate.target,
        "direction": "LONG",
        "source_timestamp": "2026-10-01",
        "metadata": {"status": "READY"},
    }
    monkeypatch.setattr(
        approval_module,
        "refresh_current_source_snapshot",
        lambda **_: {"sources": {"stock_screener": [snapshot]}},
    )
    monkeypatch.setattr(approval_module, "snapshot_to_candidate", lambda _: (candidate, ("ENTER",)))
    monkeypatch.setattr(trading_bot_server, "snapshot_to_candidate", lambda _: (candidate, ("ENTER",)))
    monkeypatch.setattr(trading_bot_server, "refresh_current_source_snapshot", lambda **_: {"sources": {"stock_screener": [snapshot]}})


def test_entry_signal_registers_exact_identity_and_no_execution_intent(tmp_path, monkeypatch):
    candidate = make_candidate()
    install_source(monkeypatch, candidate)
    result = register_canonical_setup(
        candidate.candidate_id,
        scanner_run_id="scanner-run-1",
        database_path=tmp_path / "decision.db",
    )
    assert result == {
        "setup_id": candidate.candidate_id,
        "plan_hash": plan_hash(candidate),
        "registered": True,
        "reused": False,
        "source": "stock_screener",
    }
    audit = DecisionAudit(tmp_path / "decision.db")
    with audit.connection() as con:
        row = con.execute("SELECT * FROM candidates WHERE candidate_id=?", (candidate.candidate_id,)).fetchone()
        revision = con.execute("SELECT * FROM candidate_revisions WHERE candidate_id=?", (candidate.candidate_id,)).fetchall()
    assert row is not None
    assert plan_hash(candidate) == plan_hash(candidate)
    assert len(revision) == 1
    assert revision[0]["plan_hash"] == plan_hash(candidate)
    ApprovalStore(tmp_path / "decision.db", paper_execution_enabled=False)
    with audit.connection() as con:
        assert con.execute("SELECT count(*) FROM trade_execution_intents").fetchone()[0] == 0


def test_duplicate_registration_is_idempotent(tmp_path, monkeypatch):
    candidate = make_candidate()
    install_source(monkeypatch, candidate)
    first = register_canonical_setup(candidate.candidate_id, database_path=tmp_path / "decision.db")
    second = register_canonical_setup(candidate.candidate_id, database_path=tmp_path / "decision.db")
    assert first["registered"] is True
    assert second["reused"] is True
    with DecisionAudit(tmp_path / "decision.db").connection() as con:
        assert con.execute("SELECT count(*) FROM candidate_revisions").fetchone()[0] == 1


def test_changed_plan_preserves_revision_history(tmp_path, monkeypatch):
    original = make_candidate()
    changed = make_candidate(entry=8.5)
    install_source(monkeypatch, original)
    register_canonical_setup(original.candidate_id, database_path=tmp_path / "decision.db")
    install_source(monkeypatch, changed)
    result = register_canonical_setup(changed.candidate_id, database_path=tmp_path / "decision.db")
    assert result["registered"] is True
    with DecisionAudit(tmp_path / "decision.db").connection() as con:
        rows = con.execute(
            "SELECT plan_hash, candidate_json FROM candidate_revisions WHERE candidate_id=? ORDER BY revision_id",
            (original.candidate_id,),
        ).fetchall()
        current = con.execute("SELECT candidate_json FROM candidates WHERE candidate_id=?", (original.candidate_id,)).fetchone()
    assert [row["plan_hash"] for row in rows] == [plan_hash(original), plan_hash(changed)]
    assert json.loads(current["candidate_json"])["entry"] == 8.5


def test_prepare_setup_approval_resolves_scanner_key_and_reuses(tmp_path, monkeypatch):
    import json

    candidate = make_candidate()
    install_source(monkeypatch, candidate)
    run_root = tmp_path / "runs"
    run_dir = run_root / "scan_20261002T163311Z_7f7c1d"
    run_dir.mkdir(parents=True)
    (run_dir / "candidates.json").write_text(json.dumps({"candidates": [{
        "symbol": "ALOY", "strategy_id": "lp1", "direction": "LONG",
        "status": "ENTRY_SIGNAL", "matched": True,
        "signal": {"signal_bar_date": "2026-10-01"},
    }]}), encoding="utf-8")
    monkeypatch.setattr(trading_bot_server, "_SCANNER_RUNS_ROOT", run_root)
    store = ApprovalStore(tmp_path / "decision.db", paper_execution_enabled=False)
    monkeypatch.setattr(trading_bot_server, "_approval_store", lambda: store)
    monkeypatch.setattr(
        trading_bot_server,
        "register_canonical_setup",
        lambda setup_id, scanner_run_id=None, database_path=None: approval_module.register_canonical_setup(
            setup_id, scanner_run_id=scanner_run_id, database_path=tmp_path / "decision.db"
        ),
    )
    first = trading_bot_server.prepare_setup_approval("scan_20261002T163311Z_7f7c1d", "ALOY", "lp1", "LONG")
    second = trading_bot_server.prepare_setup_approval("scan_20261002T163311Z_7f7c1d", "ALOY", "lp1", "LONG")
    assert first, first
    assert "setup_id" in first, repr(first)
    assert first["setup_id"] == candidate.candidate_id
    assert first["plan_hash"] == plan_hash(candidate)
    assert first["status"] == "PREPARED"
    assert first["card_payload"]["expires_at"] is None
    assert second["approval_id"] == first["approval_id"]
    with store.connection() as con:
        assert con.execute("SELECT count(*) FROM trade_execution_intents").fetchone()[0] == 0


def test_same_symbol_lp1_and_lp2_resolve_independently(tmp_path, monkeypatch):
    import json
    run_root = tmp_path / "runs"
    run_dir = run_root / "scan_20261002T163311Z_7f7c1d"
    run_dir.mkdir(parents=True)
    items = []
    for strategy in ("lp1", "lp2"):
        items.append({"symbol": "ALOY", "strategy_id": strategy, "direction": "LONG", "status": "ENTRY_SIGNAL", "matched": True, "signal": {"signal_bar_date": "2026-10-01"}})
    (run_dir / "candidates.json").write_text(json.dumps({"candidates": items}), encoding="utf-8")
    monkeypatch.setattr(trading_bot_server, "_SCANNER_RUNS_ROOT", run_root)
    assert _load_scanner_candidate_key(run_dir.name, "ALOY", "LP1", "LONG")["strategy"] == "LP1"
    assert _load_scanner_candidate_key(run_dir.name, "ALOY", "LP2", "LONG")["strategy"] == "LP2"


def test_stored_run_srad_lp1_and_lp2_resolve_independently():
    run = "scan_20261003T032556Z_3fda37"
    resolved = {}
    for strategy in ("LP1", "LP2"):
        key = trading_bot_server._load_scanner_candidate_key(run, "SRAD", strategy, "LONG")
        resolved[strategy] = trading_bot_server._resolve_canonical_setup_for_key(key)
    assert resolved["LP1"][0] != resolved["LP2"][0]
    assert resolved["LP1"][1] != resolved["LP2"][1]


def test_same_symbol_strategy_resolver_matches_real_srad_shape(monkeypatch):
    candidates = {
        "LP1": make_candidate(entry=8.33),
        "LP2": make_candidate(entry=8.44),
    }
    candidates["LP1"] = candidates["LP1"].__class__(**{**candidates["LP1"].__dict__, "candidate_id": "srad-lp1", "symbol": "SRAD", "strategy": "LP1"})
    candidates["LP2"] = candidates["LP2"].__class__(**{**candidates["LP2"].__dict__, "candidate_id": "srad-lp2", "symbol": "SRAD", "strategy": "LP2"})
    snapshots = [{"scan_id": "srad-run", "source": "stock_screener", "symbol": "SRAD", "strategy": strategy, "direction": "LONG", "signal_state": "READY", "candidate_class": "EXECUTABLE_ENTRY_SIGNAL", "source_timestamp": "2026-10-01", "entry": candidate.entry, "stop": candidate.stop, "target": candidate.target, "score": candidate.confidence, "reward_risk": candidate.reward_risk} for strategy, candidate in candidates.items()]
    monkeypatch.setattr(trading_bot_server, "refresh_current_source_snapshot", lambda **_: {"sources": {"stock_screener": snapshots}})
    monkeypatch.setattr(trading_bot_server, "snapshot_to_candidate", lambda snapshot: (candidates[snapshot.strategy], ("ENTER",)))
    for strategy in ("LP1", "LP2"):
        setup_id, digest = trading_bot_server._resolve_canonical_setup_for_key({"symbol": "SRAD", "strategy": strategy, "direction": "LONG", "signal_bar": "2026-10-01"})
        assert setup_id == f"srad-{strategy.lower()}"
        assert digest == plan_hash(candidates[strategy])


def test_prepare_setup_approval_fails_on_ambiguous_scanner_key(tmp_path, monkeypatch):
    import json

    run_root = tmp_path / "runs"
    run_dir = run_root / "scan_20261002T163311Z_7f7c1d"
    run_dir.mkdir(parents=True)
    item = {"symbol": "ALOY", "strategy_id": "lp1", "direction": "LONG", "status": "ENTRY_SIGNAL", "matched": True, "signal": {"signal_bar_date": "2026-10-01"}}
    (run_dir / "candidates.json").write_text(json.dumps({"candidates": [item, item]}), encoding="utf-8")
    monkeypatch.setattr(trading_bot_server, "_SCANNER_RUNS_ROOT", run_root)
    result = trading_bot_server.prepare_setup_approval("scan_20261002T163311Z_7f7c1d", "ALOY", "lp1", "LONG")
    assert result["error"]["code"] == "ambiguous_or_unknown_scanner_candidate"


def test_create_setup_approval_registers_then_succeeds(tmp_path, monkeypatch):
    candidate = make_candidate()
    install_source(monkeypatch, candidate)
    store = ApprovalStore(tmp_path / "decision.db", paper_execution_enabled=False)
    monkeypatch.setattr(trading_bot_server, "_approval_store", lambda: store)
    monkeypatch.setattr(
        trading_bot_server,
        "register_canonical_setup",
        lambda setup_id, scanner_run_id=None, database_path=None: approval_module.register_canonical_setup(
            setup_id, scanner_run_id=scanner_run_id, database_path=tmp_path / "decision.db"
        ),
    )
    result = trading_bot_server.create_setup_approval(
        candidate.candidate_id,
        scanner_run_id="scanner-run-1",
        telegram_chat_id="chat-1",
    )
    assert result["approval_id"].startswith("approval_")
    assert result["status"] == "PREPARED"
    assert store.get(result["approval_id"])["setup_id"] == candidate.candidate_id
    assert store.get(result["approval_id"])["plan_hash"] == plan_hash(candidate)
    with store.connection() as con:
        assert con.execute("SELECT count(*) FROM trade_execution_intents").fetchone()[0] == 0


def test_unknown_setup_still_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(approval_module, "refresh_current_source_snapshot", lambda **_: {"sources": {}})
    DecisionAudit(tmp_path / "decision.db")
    store = ApprovalStore(tmp_path / "decision.db", paper_execution_enabled=False)
    monkeypatch.setattr(trading_bot_server, "_approval_store", lambda: store)
    monkeypatch.setattr(trading_bot_server, "register_canonical_setup", lambda *args, **kwargs: None)
    result = trading_bot_server.create_setup_approval("unknown-setup")
    assert result["error"]["code"] == "setup_not_found"
