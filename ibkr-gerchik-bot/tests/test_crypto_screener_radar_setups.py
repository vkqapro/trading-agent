from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from dashboard_react import server
from src.crypto_radar.screener_store import CryptoScreenerStore


def _request() -> Request:
    path = "/api/crypto/market-screener"
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [],
            "client": ("test", 123),
            "server": ("testserver", 80),
        }
    )


def _scan_payload(*, ticker: str = "BTC-USDT") -> dict:
    return {
        "asset_class": "crypto",
        "scan_timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "anchor_date": "2026-10-27",
        "timeframe": "1D",
        "strategies": ["LP1", "PRB2"],
        "side": "ALL",
        "signals": [
            {
                "ticker": ticker,
                "strategy": "PRB2",
                "side": "LONG",
                "status": "pending_entry",
                "score": 0.82,
            }
        ],
        "rejected": [],
        "near_misses": [],
        "signals_found": 1,
        "universe_size": 17,
    }


def test_crypto_screener_store_exposes_latest_signal_symbols(tmp_path) -> None:
    store = CryptoScreenerStore(tmp_path / "decision.db")
    scan_id = store.save(_scan_payload())

    metadata = store.radar_setup_universe()

    assert metadata["valid"] is True
    assert metadata["source_scan_id"] == scan_id
    assert metadata["selected_symbols"] == ["BTC-USDT"]
    assert metadata["selected"][0]["strategy"] == "PRB2"
    assert metadata["selected"][0]["side"] == "LONG"


def test_crypto_screener_store_empty_universe_is_explicit(tmp_path) -> None:
    store = CryptoScreenerStore(tmp_path / "decision.db")

    metadata = store.radar_setup_universe()

    assert metadata["valid"] is False
    assert metadata["reason_code"] == "CRYPTO_RADAR_EMPTY"


def test_crypto_market_screener_radar_setups_scans_latest_all_universe_symbols(tmp_path) -> None:
    store = CryptoScreenerStore(tmp_path / "decision.db")
    result = _scan_payload()
    store.save(result, universe_source="ALL_UNIVERSE")
    scanner_result = {**result, "universe_source": "ALL_UNIVERSE", "orders_submitted": 0}
    with (
        patch.object(server, "_crypto_screener_store", return_value=store),
        patch.object(server.da, "load_crypto_symbols", return_value=[{"inst_id": "BTC-USDT"}, {"inst_id": "ETH-USDT"}]),
        patch.object(server.da, "load_crypto_dashboard_state", return_value={"watchlist": {}}),
        patch.object(server, "run_market_screener", return_value=scanner_result) as scanner,
    ):
        payload = server.api_market_screener(_request(), scan_universe="radar_setups")

    assert scanner.call_args.kwargs["symbols"] == ["BTC-USDT"]
    assert payload["universe_source"] == "RADAR_SETUPS"
    assert payload["radar_universe"]["source"] == "CRYPTO_SCREENER"
    assert payload["radar_universe"]["selected_symbols"] == ["BTC-USDT"]


def test_crypto_market_screener_radar_setups_does_not_fall_back_to_all_universe(tmp_path) -> None:
    store = CryptoScreenerStore(tmp_path / "decision.db")
    with (
        patch.object(server, "_crypto_screener_store", return_value=store),
        patch.object(server.da, "load_crypto_symbols", return_value=[{"inst_id": "BTC-USDT"}]),
        patch.object(server.da, "load_crypto_dashboard_state", return_value={"watchlist": {}}),
        patch.object(server, "run_market_screener") as scanner,
    ):
        with pytest.raises(HTTPException) as raised:
            server.api_market_screener(_request(), scan_universe="radar_setups")

    assert raised.value.status_code == 409
    assert raised.value.detail["reason_code"] == "CRYPTO_RADAR_EMPTY"
    scanner.assert_not_called()
