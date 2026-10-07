from __future__ import annotations

from unittest.mock import patch

from dashboard_react import server


def test_submitted_approval_order_is_exposed_by_the_existing_trades_api() -> None:
    request = {
        "id": "approval-intent-1",
        "status": "done",
        "source": "telegram_approval",
        "approval_id": "approval_1",
        "execution_intent_id": "intent_1",
        "symbol": "AAPL",
        "strategy": "PRB1",
        "direction": "long",
        "quantity": 5,
        "entry": 100.0,
        "stop": 99.0,
        "target": 103.0,
        "result": {
            "status": "executed",
            "market_order_id": 101,
            "stop_order_id": 102,
            "limit_order_id": 103,
            "broker_statuses": {
                "market_order": "PreSubmitted",
                "stop_order": "Submitted",
                "limit_order": "Submitted",
            },
        },
    }
    with (
        patch.object(server.da, "load_tracked_positions", return_value=[]),
        patch.object(server.da, "load_intraday_snapshot", return_value={}),
        patch.object(server.da, "load_order_requests", return_value=[request]),
        patch.object(server.da, "latest_trade_log_sections", return_value=[]),
        patch.object(server, "load_stock_tradingview_state", return_value={}),
        patch("src.execution.order_requests.worker_is_alive", return_value=True),
        patch("src.execution.order_requests.worker_age_seconds", return_value=0.1),
    ):
        payload = server.api_trades()

    assert payload["order_requests"] == [request]
    assert payload["order_requests"][0]["approval_id"] == "approval_1"
    assert payload["order_requests"][0]["result"]["market_order_id"] == 101
    assert payload["order_requests"][0]["result"]["broker_statuses"]["market_order"] == "PreSubmitted"
    assert payload["worker_alive"] is True
