from __future__ import annotations

from time import perf_counter
import json

import pandas as pd
import pytest

from src.mcp import trading_data_service as service
from src.mcp import trading_bot_server
from src.mcp.trading_bot_server import _approval_card_payload, approve_setup, get_approval_card_payload, get_symbol_levels, render_symbol_chart


def test_approve_setup_only_authorizes_and_enqueues(monkeypatch):
    approval_id = "approval_" + "d" * 32

    class Store:
        def decide(self, approval_id_arg, **kwargs):
            assert approval_id_arg == approval_id
            assert kwargs["approve"] is True
            return type("Result", (), {"approval_id": approval_id, "status": "APPROVED", "message": "accepted", "reasons": ()})()

        def backend_revalidate_approval(self, *_args, **_kwargs):
            raise AssertionError("scanner revalidation must run in the worker")

        def get(self, requested_id):
            assert requested_id == approval_id
            return {"approval_id": approval_id, "status": "APPROVED"}

        def approval_job(self, requested_id):
            return {"job_id": "approval-job-" + requested_id, "status": "PENDING"}

    monkeypatch.setattr(trading_bot_server, "_approval_store", lambda: Store())
    started = perf_counter()
    result = approve_setup(approval_id, "u1", "c1", "callback-1")
    elapsed = perf_counter() - started
    assert result["status"] == "APPROVED"
    assert result["approval_job_status"] == "PENDING"
    assert result["approval_job_id"] == "approval-job-" + approval_id
    assert elapsed < 1.0


@pytest.fixture
def persisted(monkeypatch):
    frame = pd.DataFrame([
        {"date": "2025-01-01", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100},
        {"date": "2025-01-02", "open": 11, "high": 13, "low": 10, "close": 12, "volume": 110},
    ])
    monkeypatch.setattr(service, "load_bars", lambda symbol, timeframe: frame.copy() if timeframe in {"daily", "intraday_15m"} else pd.DataFrame())
    monkeypatch.setattr(service, "load_watchlist", lambda: {"AAPL": {"daily_atr": 2.0, "technical_atr": 3.0, "security_type": "STK", "level_spacing": {"current_price": 11.0}, "raw_levels": [{"price": 10, "type": "gap"}], "levels": [{"price": 10, "zone_low": 9.5, "zone_high": 10.5, "type": "gap", "touches": 3, "false_breakouts": 1, "strength": 55}, {"price": 11.5, "zone_low": 11.4, "zone_high": 11.6, "type": "gap", "touches": 2, "false_breakouts": 0, "strength": 20}]}})
    return frame


def test_approval_card_payload_projects_authoritative_candidate_fields():
    payload = _approval_card_payload({
        "approval_id": "approval_" + "a" * 32,
        "status": "PENDING_APPROVAL",
        "expires_at": "2030-01-01T00:00:00+00:00",
        "candidate_json": '{"symbol":"ALOY","strategy":"lp1","direction":"LONG","level_price":10.0,"entry":10.1,"stop":9.9,"target":10.5,"reward_risk":2.0,"confidence":0.8,"metadata":{}}',
    })
    assert payload == {
        "approval_id": "approval_" + "a" * 32,
        "status": "PENDING_APPROVAL",
        "expires_at": "2030-01-01T00:00:00+00:00",
        "symbol": "ALOY", "strategy": "lp1", "direction": "LONG", "level": 10.0,
        "entry": 10.1, "stop": 9.9, "target": 10.5, "quantity": None,
        "risk_amount": None, "reward_amount": None, "rr": 2.0, "score": 0.8,
    }


def test_get_approval_card_payload_reads_authoritative_prepared_row(monkeypatch):
    approval_id = "approval_" + "b" * 32
    row = {
        "approval_id": approval_id,
        "status": "PREPARED",
        "expires_at": "placeholder-is-not-used",
        "telegram_chat_id": None,
        "telegram_message_id": None,
        "candidate_json": '{"symbol":"ALOY","strategy":"LP1","direction":"LONG",'
                           '"level_price":8.2,"entry":8.3312,"stop":7.6644,"target":9.6648,'
                           '"reward_risk":2.0,"confidence":0.6684,"metadata":{"score":0.6684}}',
    }

    class Store:
        def get(self, requested_id):
            assert requested_id == approval_id
            return dict(row)

    monkeypatch.setattr(trading_bot_server, "_approval_store", lambda: Store())
    result = get_approval_card_payload(approval_id)

    assert result["delivery_allowed"] is True
    assert result["already_delivered"] is False
    assert result["card_payload"] == {
        "approval_id": approval_id,
        "status": "PREPARED",
        "expires_at": None,
        "symbol": "ALOY", "strategy": "LP1", "direction": "LONG", "level": 8.2,
        "entry": 8.3312, "stop": 7.6644, "target": 9.6648, "quantity": None,
        "risk_amount": None, "reward_amount": None, "rr": 2.0, "score": 0.6684,
    }


@pytest.mark.parametrize(
    ("direction", "entry", "stop", "target", "quantity", "risk", "reward"),
    [
        ("LONG", 10.0, 9.0, 12.0, 7, 7.0, 14.0),
        ("SHORT", 10.0, 11.5, 7.0, 4, 6.0, 12.0),
    ],
)
def test_approval_card_payload_exposes_reviewed_trade_economics(
    direction, entry, stop, target, quantity, risk, reward,
):
    payload = _approval_card_payload({
        "approval_id": "approval_" + "e" * 32,
        "status": "PENDING_APPROVAL",
        "expires_at": "2030-01-01T00:00:00+00:00",
        "reviewed_quantity": quantity,
        "candidate_json": json.dumps({
            "symbol": "TEST", "strategy": "LP1", "direction": direction,
            "level_price": 9.5, "entry": entry, "stop": stop, "target": target,
            "reward_risk": 2.0, "confidence": 0.8, "metadata": {},
        }),
    })
    assert payload["quantity"] == quantity
    assert payload["risk_amount"] == risk
    assert payload["reward_amount"] == reward


def test_get_approval_card_payload_blocks_default_duplicate_delivery(monkeypatch):
    approval_id = "approval_" + "c" * 32

    class Store:
        def get(self, requested_id):
            return {
                "approval_id": requested_id, "status": "PENDING_APPROVAL",
                "expires_at": "2030-01-01T00:00:00+00:00",
                "telegram_chat_id": "6618121491", "telegram_message_id": "123",
                "candidate_json": '{"symbol":"ALOY","strategy":"LP1","direction":"LONG",'
                                   '"level_price":8.2,"entry":8.3312,"stop":7.6644,"target":9.6648,'
                                   '"reward_risk":2.0,"confidence":0.6684,"metadata":{}}',
            }

    monkeypatch.setattr(trading_bot_server, "_approval_store", lambda: Store())
    result = get_approval_card_payload(approval_id)

    assert result["already_delivered"] is True
    assert result["delivery_allowed"] is False
    assert result["telegram_message_id"] == "123"


def test_history_excludes_incomplete_by_default(persisted):
    result = service.get_symbol_history(" aapl ", lookback_days=60)
    assert result["symbol"] == "AAPL"
    assert all(item["closed"] for item in result["candles"])
    assert result["latest_closed_price"] == result["candles"][-1]["close"]
    assert result["latest_closed_bar_timestamp"] == result["candles"][-1]["timestamp"]


def test_snapshot_separates_current_and_latest_closed_prices(persisted):
    result = service.get_symbol_snapshot("AAPL")
    assert result["current_price"] == 12
    assert result["last_price"] == result["current_price"]
    assert result["latest_closed_price"] == 12


def test_levels_and_context_use_persisted_values(persisted):
    result = service.get_symbol_levels("AAPL")
    assert result["levels"][0]["zone_low"] == 9.5
    assert result["current_price"] == 12
    assert result["level_reference_price"] == 11
    assert result["levels_as_of"] is None
    assert result["levels"][0]["distance_from_current_price"]["dollars"] == -2
    context = service.get_level_context("AAPL", 10.0001)
    assert context["level"]["touches"] == 3
    assert context["current_price"] == 12
    assert context["level_reference_price"] == 11
    assert context["distance_from_current_price"] == -2
    assert context["distance_from_level_reference_price"] == -1


def test_level_side_filter_uses_current_price_not_reference(persisted):
    below = service.get_symbol_levels("AAPL", side="below_price")
    above = service.get_symbol_levels("AAPL", side="above_price")
    assert [level["price"] for level in below["levels"]] == [10, 11.5]
    assert above["levels"] == []


def test_chart_renders_closed_candles_and_filtered_levels(persisted):
    result = service.render_symbol_chart("AAPL", lookback_days=60, show_levels=True, min_strength=30, width=800, height=500)
    assert result["metadata"]["actual_bar_count"] == 2
    assert result["metadata"]["latest_closed_price"] == 12
    assert result["metadata"]["levels_total"] == 1
    assert result["metadata"]["levels_rendered"] == 1
    assert result["image_bytes"].startswith(b"\x89PNG")


def test_server_returns_mixed_metadata_and_image_content(persisted):
    result = render_symbol_chart("AAPL", lookback_days=60, show_levels=True, width=800, height=500)
    assert not result.isError
    assert [item.type for item in result.content] == ["text", "image"]
    assert result.content[1].mimeType == "image/png"


def test_invalid_level_set_is_stable_error():
    result = get_symbol_levels("AAPL", level_set="calculated")
    assert result["error"]["code"] == "INVALID_LEVEL_SET"


def test_mcp_package_has_no_broker_write_surface_imports():
    from pathlib import Path
    package = Path(__file__).parents[1] / "src" / "mcp"
    source = "\\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    for forbidden in ("OrderManager", "execute_trade", "placeOrder", "place_market_order", "cancel_order"):
        assert forbidden not in source
