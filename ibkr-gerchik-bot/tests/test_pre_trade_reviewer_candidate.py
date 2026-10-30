from __future__ import annotations

import pytest

from tools import pre_trade_reviewer_api as api


SCAN_ID = "84a89696-f592-47d2-aad8-e1a7fffae9ba"


def _row(*, ordinal=None, level=32.38, lifecycle="READY", conflict=False, terminal=False):
    scanner = {
        "strategy": "PRB2",
        "side": "LONG",
        "level_price": level,
        "source_scan_id": "source-scan-1",
        "scan_timestamp": "2026-10-05T14:30:00+00:00",
        "lifecycle_status": lifecycle,
        "lifecycle_reason": "test_reason",
        "planned_entry": 32.78,
        "entry_price": 32.87,
        "stop": 32.28,
        "target": 34.03,
        "rr": 2.0,
        "direction_conflict": conflict,
        "data_quality_complete": True,
    }
    row = {"symbol": "KDP", "scanner": scanner, "radar": {"trend": "GREEN"}}
    if ordinal is not None:
        row["signal_ordinal"] = ordinal
    return row


def _status(rows=None, *, scan_status="COMPLETED", result=True):
    payload = {
        "status": scan_status,
        "read_only": True,
        "orders_submitted": 0,
        "approvals_created": 0,
        "execution_intents_created": 0,
    }
    if result:
        payload["result"] = {
            "radar_as_of": "2026-10-05T16:00:00+00:00",
            "market_status": "OPEN",
            "active": rows if rows is not None else [_row(ordinal=1), _row(ordinal=2, level=32.50)],
            "terminal": [],
        }
    return payload


def _resolve(status, *, symbol="KDP", section="active", ordinal=1):
    return api._resolve_candidate(
        status,
        scan_id=SCAN_ID,
        symbol=symbol,
        section=section,
        signal_ordinal=ordinal,
    )


def test_exact_candidate_selection_preserves_identity_and_context():
    result = _resolve(_status(), ordinal=2)
    candidate = result["candidate"]
    review = result["reviews"][0]

    assert result["resolution_status"] == "RESOLVED"
    assert candidate["signal_ordinal"] == 2
    assert candidate["level_price"] == 32.50
    assert candidate["source_scan_id"] == "source-scan-1"
    assert candidate["scan_timestamp"] == "2026-10-05T14:30:00+00:00"
    assert review["candidate_identity"] == {
        "scan_id": SCAN_ID,
        "section": "active",
        "symbol": "KDP",
        "signal_ordinal": 2,
    }
    assert result["orders_submitted"] == 0
    assert result["approvals_created"] == 0
    assert result["execution_intents_created"] == 0


def test_same_rounded_prices_still_select_distinct_explicit_tiers():
    rows = [_row(ordinal=1, level=32.38), _row(ordinal=2, level=32.48)]
    rows[1]["scanner"]["planned_entry"] = rows[0]["scanner"]["planned_entry"]
    rows[1]["scanner"]["entry_price"] = rows[0]["scanner"]["entry_price"]
    assert _resolve(_status(rows), ordinal=1)["candidate"]["level_price"] == 32.38
    assert _resolve(_status(rows), ordinal=2)["candidate"]["level_price"] == 32.48


def test_duplicate_explicit_ordinal_is_rejected_as_ambiguous():
    result = _resolve(_status([_row(ordinal=1), _row(ordinal=1, level=32.50)]))
    assert result["resolution_status"] == "AMBIGUOUS"
    assert result["resolution_reason"] == "DAILY_TRADER_CANDIDATE_AMBIGUOUS"
    assert result["reviews"] == []


def test_missing_candidate_and_missing_ordinal_are_explicit():
    missing = _resolve(_status(), symbol="AAPL", ordinal=1)
    absent_ordinal = _resolve(_status(), ordinal=9)
    assert missing["resolution_status"] == "NOT_FOUND"
    assert missing["resolution_reason"] == "DAILY_TRADER_CANDIDATE_NOT_FOUND"
    assert absent_ordinal["resolution_reason"] == "DAILY_TRADER_CANDIDATE_ORDINAL_NOT_FOUND"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (_status(scan_status="RUNNING"), "SCAN_NOT_COMPLETED"),
        (_status(result=False), "STALE_OR_INCOMPLETE"),
        (_status(), "RESOLVED"),
    ],
)
def test_scan_completion_and_persisted_result_requirements(status, expected):
    result = _resolve(status)
    assert result["resolution_status"] == expected


def test_missing_section_is_incomplete():
    status = _status()
    del status["result"]["active"]
    result = _resolve(status)
    assert result["resolution_status"] == "INCOMPLETE"
    assert result["resolution_reason"] == "DAILY_TRADER_SCAN_SECTION_UNAVAILABLE"


def test_terminal_invalidated_and_direction_conflict_are_preserved():
    status = _status([_row(ordinal=1, lifecycle="INVALIDATED", conflict=True)])
    status["result"]["active"] = []
    status["result"]["terminal"] = [_row(ordinal=1, lifecycle="INVALIDATED", conflict=True)]
    result = _resolve(status, section="terminal")
    review = result["reviews"][0]
    assert result["candidate"]["lifecycle_status"] == "INVALIDATED"
    assert review["review_status"] == "NOT_ELIGIBLE_FOR_APPROVAL"
    assert review["lifecycle_status"] == "INVALIDATED"
    assert review["candidate"]["direction_conflict"] is True
    assert result["approvals_created"] == 0


def test_missing_context_remains_insufficient_data_without_side_effects():
    row = _row(ordinal=1)
    for key in ("planned_entry", "stop", "target"):
        row["scanner"].pop(key)
    status = _status([row])
    status["result"]["market_status"] = None
    review = _resolve(status)["reviews"][0]
    assert review["review_status"] == "NOT_ELIGIBLE_FOR_APPROVAL"
    assert any("Missing authoritative setup fields" in reason for reason in review["reasons"])
    assert any("Market status is UNKNOWN" in warning for warning in review["warnings"])
    assert review["orders_submitted"] == 0
    assert review["approval_created"] is False


def test_candidate_lookup_uses_get_only_scan_endpoint(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            import json

            return json.dumps(_status()).encode()

    def fake_urlopen(request, timeout):
        seen["method"] = request.method
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(api, "urlopen", fake_urlopen)
    api._scan_status_request(SCAN_ID)
    assert seen == {
        "method": "GET",
        "url": f"http://127.0.0.1:8550/api/daily-trader/scans/{SCAN_ID}",
        "timeout": 30,
    }


def test_scan_service_failure_is_not_reclassified(monkeypatch):
    def fail(_scan_id):
        raise RuntimeError("persisted scan unavailable")

    monkeypatch.setattr(api, "_scan_status_request", fail)
    with pytest.raises(RuntimeError, match="persisted scan unavailable"):
        api.review_candidate(scan_id=SCAN_ID, symbol="KDP", section="active", signal_ordinal=1)


def test_full_candidate_reference_parses_without_prefix_lookup():
    assert api.parse_candidate_reference(f"{SCAN_ID}:active:KDP:1") == {
        "scan_id": SCAN_ID,
        "section": "active",
        "symbol": "KDP",
        "signal_ordinal": 1,
    }


@pytest.mark.parametrize("reference", [
    "84a89696:active:KDP:1",
    f"{SCAN_ID}:unknown:KDP:1",
    f"{SCAN_ID}:active:KDP:not-an-ordinal",
])
def test_candidate_reference_rejects_prefix_or_invalid_identifiers(reference):
    with pytest.raises(ValueError):
        api.parse_candidate_reference(reference)


def test_structurally_valid_unknown_reference_is_rejected_by_persisted_resolution():
    reference = api.parse_candidate_reference(f"{SCAN_ID}:active:KDP:9")
    result = _resolve(
        _status(),
        symbol=reference["symbol"],
        section=reference["section"],
        ordinal=reference["signal_ordinal"],
    )
    assert result["resolution_status"] == "NOT_FOUND"
    assert result["resolution_reason"] == "DAILY_TRADER_CANDIDATE_ORDINAL_NOT_FOUND"


def test_latest_review_includes_all_active_tiers_and_separates_terminal(monkeypatch):
    status = _status([_row(ordinal=1), _row(ordinal=2, level=32.50)])
    status["scan_id"] = SCAN_ID
    status["completed_at"] = "2026-10-14T15:00:00+00:00"
    status["result"]["terminal"] = [_row(ordinal=1, lifecycle="INVALIDATED", terminal=True)]
    monkeypatch.setattr(api, "_latest_scan_request", lambda: status)

    result = api.review_latest()

    assert result["active_candidates_reviewed"] == 2
    assert result["terminal_candidates_count"] == 1
    assert [item["candidate_identity"]["signal_ordinal"] for item in result["reviews"]] == [1, 2]
    assert result["terminal_candidates"][0]["lifecycle_status"] == "INVALIDATED"
    assert result["orders_submitted"] == 0
    assert result["approvals_created"] == 0
    assert result["execution_intents_created"] == 0


def test_latest_review_symbol_filter_keeps_all_matching_tiers(monkeypatch):
    status = _status([_row(ordinal=1), _row(ordinal=2, level=32.50)])
    status["scan_id"] = SCAN_ID
    status["result"]["active"].append({**_row(ordinal=3), "symbol": "SO"})
    monkeypatch.setattr(api, "_latest_scan_request", lambda: status)

    result = api.review_latest(symbol="KDP")

    assert result["active_candidates_reviewed"] == 2
    assert {item["symbol"] for item in result["reviews"]} == {"KDP"}


def test_latest_scan_lookup_is_get_only(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            import json

            return json.dumps(_status()).encode()

    def fake_urlopen(request, timeout):
        seen["method"] = request.method
        seen["url"] = request.full_url
        return Response()

    monkeypatch.setattr(api, "urlopen", fake_urlopen)
    api._latest_scan_request()
    assert seen == {
        "method": "GET",
        "url": "http://127.0.0.1:8550/api/daily-trader/scans/latest",
    }


def test_exact_scan_review_preserves_active_tiers_and_terminal_candidates(monkeypatch):
    status = _status([_row(ordinal=1), _row(ordinal=2, level=32.50)])
    status["completed_at"] = "2026-10-14T16:27:44+00:00"
    status["result"]["scan_timestamp"] = "2026-10-14T16:27:37+00:00"
    status["result"]["terminal"] = [_row(ordinal=1, lifecycle="INVALIDATED", terminal=True)]
    monkeypatch.setattr(api, "_scan_status_request", lambda scan_id: status)

    result = api.review_scan(scan_id=SCAN_ID, scope="all_active", include_terminal=True, read_only=True)

    assert result["scan_id"] == SCAN_ID
    assert result["scan_status"] == "COMPLETED"
    assert result["active_candidates_reviewed"] == 2
    assert result["terminal_candidates_reviewed"] == 1
    assert [item["candidate_identity"]["signal_ordinal"] for item in result["active_reviews"]] == [1, 2]
    assert result["active_reviews"][1]["candidate"]["level_price"] == 32.50
    assert result["terminal_reviews"][0]["candidate_identity"]["section"] == "terminal"
    assert result["terminal_reviews"][0]["review_status"] == "NOT_ELIGIBLE_FOR_APPROVAL"
    assert result["orders_submitted"] == 0
    assert result["approvals_created"] == 0
    assert result["execution_intents_created"] == 0


def test_exact_scan_review_is_get_only_and_requires_canonical_uuid(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            import json

            return json.dumps(_status()).encode()

    def fake_urlopen(request, timeout):
        seen["method"] = request.method
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(api, "urlopen", fake_urlopen)
    result = api.review_scan(scan_id=SCAN_ID, include_terminal=True)

    assert result["scan_id"] == SCAN_ID
    assert seen == {
        "method": "GET",
        "url": f"http://127.0.0.1:8550/api/daily-trader/scans/{SCAN_ID}",
        "timeout": 30,
    }
    uppercase = api.review_scan(scan_id=SCAN_ID.upper())
    assert uppercase["scan_id"] == SCAN_ID


def test_exact_scan_review_rejects_non_completed_or_invalid_scope(monkeypatch):
    monkeypatch.setattr(api, "_scan_status_request", lambda scan_id: _status(scan_status="RUNNING"))
    with pytest.raises(RuntimeError, match="COMPLETED"):
        api.review_scan(scan_id=SCAN_ID)
    with pytest.raises(ValueError, match="scope must be all_active"):
        api.review_scan(scan_id=SCAN_ID, scope="latest")
