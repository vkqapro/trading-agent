from __future__ import annotations

import pandas as pd
import pytest

from src.mcp import trading_data_service as service
from src.mcp.trading_bot_server import get_symbol_levels


@pytest.fixture
def persisted(monkeypatch):
    frame = pd.DataFrame([
        {"date": "2025-01-01", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100},
        {"date": "2025-01-02", "open": 11, "high": 13, "low": 10, "close": 12, "volume": 110},
    ])
    monkeypatch.setattr(service, "load_bars", lambda symbol, timeframe: frame.copy() if timeframe in {"daily", "intraday_15m"} else pd.DataFrame())
    monkeypatch.setattr(service, "load_watchlist", lambda: {"AAPL": {"daily_atr": 2.0, "technical_atr": 3.0, "security_type": "STK", "level_spacing": {"current_price": 11.0}, "raw_levels": [{"price": 10, "type": "gap"}], "levels": [{"price": 10, "zone_low": 9.5, "zone_high": 10.5, "type": "gap", "touches": 3, "false_breakouts": 1, "strength": 55}, {"price": 11.5, "zone_low": 11.4, "zone_high": 11.6, "type": "gap", "touches": 2, "false_breakouts": 0, "strength": 20}]}})
    return frame


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


def test_invalid_level_set_is_stable_error():
    result = get_symbol_levels("AAPL", level_set="calculated")
    assert result["error"]["code"] == "INVALID_LEVEL_SET"


def test_mcp_package_has_no_broker_write_surface_imports():
    from pathlib import Path
    package = Path(__file__).parents[1] / "src" / "mcp"
    source = "\\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    for forbidden in ("OrderManager", "execute_trade", "placeOrder", "place_market_order", "cancel_order"):
        assert forbidden not in source
