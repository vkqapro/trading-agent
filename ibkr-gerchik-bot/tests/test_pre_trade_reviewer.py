import sys
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.pre_trade_reviewer import (
    REVIEW_CAUTION,
    REVIEW_ELIGIBLE,
    REVIEW_NOT_ELIGIBLE,
    router,
    review_symbol_payload,
)


def _candidate(**overrides):
    scanner = {
        "strategy": "PRB1",
        "side": "LONG",
        "lifecycle_status": "PENDING_ENTRY",
        "lifecycle_reason": "waiting_for_entry",
        "entry_triggered": False,
        "level": 45.0,
        "level_type": "resistance",
        "planned_entry": 45.47,
        "stop": 43.26,
        "target": 49.89,
        "rr": 2.0,
        "score": 0.70,
        "pattern_quality": 0.81,
        "position_size": 80,
        "risk_amount": 176.0,
        "risk_pct": 0.18,
        "reward_amount": 352.0,
        "direction_conflict": False,
        "data_quality_complete": True,
        "missing_quality_data": [],
        "chronology_source": "production",
        "chronology_event": "pending",
    }
    scanner.update(overrides)
    return {
        "symbol": "GSHD",
        "market_status": "OPEN",
        "read_only": True,
        "orders_submitted": 0,
        "active": [{"radar": {"trend": "GREEN", "radar_score": 84.2}, "scanner": scanner}],
        "terminal": [],
        "no_setup": [],
        "scanner_status": "CANDIDATE",
    }


def test_active_clean_candidate_is_eligible_without_recomputing_risk():
    result = review_symbol_payload(_candidate())[0]
    assert result.review_status == REVIEW_ELIGIBLE
    assert result.candidate["risk_amount"] == 176.0
    assert result.candidate["position_size"] == 80
    assert result.candidate["read_only"] is True
    assert result.orders_submitted == 0
    assert result.approval_created is False


def test_pending_entry_explicitly_warns_not_triggered():
    result = review_symbol_payload(_candidate())[0]
    assert "Entry has not triggered yet." in result.reasons


def test_counter_trend_and_partial_quality_are_caution():
    result = review_symbol_payload(_candidate(direction_conflict=True, data_quality_complete=False, missing_quality_data=["spread"]))[0]
    assert result.review_status == REVIEW_CAUTION
    assert any("Counter-trend" in warning for warning in result.warnings)
    assert any("DATA QUALITY PARTIAL" in warning for warning in result.warnings)


def test_terminal_lifecycles_are_not_eligible():
    for lifecycle in ("INVALIDATED", "EXPIRED", "AMBIGUOUS_INTRABAR_ORDER"):
        result = review_symbol_payload(_candidate(lifecycle_status=lifecycle))[0]
        assert result.review_status == REVIEW_NOT_ELIGIBLE


def test_no_setup_cannot_be_constructed_from_radar():
    result = review_symbol_payload({
        "symbol": "SNOW", "scanner_status": "NO_SETUP", "market_status": "OPEN",
        "radar": {"trend": "GREEN", "radar_score": 99}, "read_only": True, "orders_submitted": 0,
        "active": [], "terminal": [], "no_setup": [{"symbol": "SNOW", "radar": {"radar_score": 99}}],
    })[0]
    assert result.review_status == REVIEW_NOT_ELIGIBLE
    assert result.candidate is None
    assert result.reasons == ("No production setup exists.",)


def test_missing_authoritative_fields_are_not_eligible():
    result = review_symbol_payload(_candidate(target=None))[0]
    assert result.review_status == REVIEW_NOT_ELIGIBLE
    assert any("target" in reason for reason in result.reasons)


def test_safety_contract_is_hard_rule():
    payload = _candidate()
    payload["read_only"] = False
    payload["orders_submitted"] = 1
    result = review_symbol_payload(payload)[0]
    assert result.review_status == REVIEW_NOT_ELIGIBLE
    assert result.approval_created is False


def test_health_route_is_static_endpoint_not_symbol_review(monkeypatch):
    class DailyTraderStub:
        def health(self):
            return {"status": "ok", "read_only": True, "orders_submitted": 0}

        def symbol(self, symbol):
            raise AssertionError(f"health route was treated as symbol {symbol}")

    monkeypatch.setitem(
        sys.modules,
        "dashboard_react.server",
        SimpleNamespace(_daily_trader_service=lambda: DailyTraderStub()),
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/api/pre-trade-review/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "read_only": True,
        "orders_submitted": 0,
        "approval_created": False,
        "reviewer": "pre-trade-reviewer",
    }
    assert client.get("/api/pre-trade-reviewer/health").status_code == 404
