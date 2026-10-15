from src.trading_orchestrator_contract import reconcile_workflow


SCAN_ID = "84a89696-f592-47d2-aad8-e1a7fffae9ba"


def _candidate(section, symbol, ordinal):
    return {"candidate_identity": {"section": section, "symbol": symbol, "signal_ordinal": ordinal}}


def _payloads():
    active = [_candidate("active", "KDP", 1), _candidate("active", "KDP", 2)]
    terminal = [_candidate("terminal", "KDP", 1)]
    ops = {"status": "HEALTHY", "component_health": {}, "broker_readiness": {}, "warnings": [], "timestamp": "2026-10-14T12:00:00Z"}
    daily = {
        "scan_id": SCAN_ID, "scan_status": "COMPLETED", "radar_as_of": "2026-10-13",
        "scan_timestamp": "2026-10-14T12:27:44-04:00", "scanner": {"requested": 30, "completed": 30, "failed": 0},
        "active": active, "terminal": terminal, "warnings": [],
    }
    reviewer = {
        "scan_id": SCAN_ID, "active_reviews": active, "terminal_reviews": terminal, "warnings": [],
        "orders_submitted": 0, "approvals_created": 0, "execution_intents_created": 0,
    }
    return ops, daily, reviewer


def test_reconciles_exact_scan_and_same_symbol_tiers():
    result = reconcile_workflow(*_payloads())
    assert result["status"] == "READY"
    assert result["scan_id"] == SCAN_ID
    assert result["active_candidates"] == 2
    assert result["terminal_candidates"] == 1


def test_rejects_mismatched_scan_id_and_preserves_zero_side_effects():
    ops, daily, reviewer = _payloads()
    reviewer["scan_id"] = "7c5f7d4a-18ce-4bb1-b7bd-f2af64c40b72"
    result = reconcile_workflow(ops, daily, reviewer)
    assert result["status"] == "INCOMPLETE"
    assert "scan_id" in result["reason"]
    assert result["orders_submitted"] == 0
    assert result["approvals_created"] == 0
    assert result["execution_intents_created"] == 0


def test_rejects_stale_or_partial_scan_and_nonzero_side_effect_counter():
    ops, daily, reviewer = _payloads()
    daily["scan_status"] = "RUNNING"
    assert reconcile_workflow(ops, daily, reviewer)["status"] == "INCOMPLETE"
    ops, daily, reviewer = _payloads()
    reviewer["orders_submitted"] = 1
    result = reconcile_workflow(ops, daily, reviewer)
    assert result["status"] == "INCOMPLETE"
    assert "side-effect" in result["reason"]


def test_rejects_duplicate_or_missing_candidate_identity():
    ops, daily, reviewer = _payloads()
    daily["active"][1] = _candidate("active", "KDP", 1)
    assert reconcile_workflow(ops, daily, reviewer)["status"] == "INCOMPLETE"
    ops, daily, reviewer = _payloads()
    del reviewer["active_reviews"][1]["candidate_identity"]["signal_ordinal"]
    assert reconcile_workflow(ops, daily, reviewer)["status"] == "INCOMPLETE"


def test_rejects_terminal_count_mismatch():
    ops, daily, reviewer = _payloads()
    reviewer["terminal_reviews"] = []
    result = reconcile_workflow(ops, daily, reviewer)
    assert result["status"] == "INCOMPLETE"
    assert "terminal" in result["reason"]
