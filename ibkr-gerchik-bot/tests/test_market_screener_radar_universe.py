from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from dashboard_react import server
from src.radar.models import RadarResult, RadarScannerResult, RadarSettings
from src.radar.service import RadarService
from src.storage.trend_radar_store import TrendRadarStore


def _request(path: str = "/api/market-screener") -> Request:
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


def _radar(symbol: str, status: str, *, as_of: str = "2026-10-12") -> RadarResult:
    return RadarResult(
        symbol=symbol,
        as_of=as_of,
        trend="GREEN",
        trend_score=70.0,
        radar_score=80.0,
        scanner_status=status,
        created_at=f"{as_of}T21:15:00+00:00",
    )


def _service(tmp_path, *rows: RadarResult) -> RadarService:
    store = TrendRadarStore(tmp_path / "decision.db")
    for row in rows:
        store.upsert(row)
    return RadarService(
        store=store,
        settings=RadarSettings(min_avg_dollar_volume=1_000_000),
        scanner_runner=lambda symbol: {"signals": []},
    )


def _service_with_scans(tmp_path, rows: list[RadarResult], scans: list[RadarScannerResult]) -> RadarService:
    store = TrendRadarStore(tmp_path / "decision.db")
    for row in rows:
        store.upsert(row)
    for scan in scans:
        store.upsert_scanner_result(scan)
    return RadarService(
        store=store,
        settings=RadarSettings(min_avg_dollar_volume=1_000_000),
        scanner_runner=lambda symbol: {"signals": []},
    )


def _scan(symbol: str, status: str, *, radar_as_of: str = "2026-10-12", scan_timestamp: str = "2026-10-12T21:20:00+00:00") -> RadarScannerResult:
    return RadarScannerResult(
        symbol=symbol,
        radar_as_of=radar_as_of,
        source_scan_id=f"scan:{radar_as_of}:{symbol}:{scan_timestamp}",
        scan_timestamp=scan_timestamp,
        scanner_status=status,
        scanner_label="PRB2 LONG" if status in {"CANDIDATE", "READY", "ENTRY_TRIGGERED", "PENDING_ENTRY"} else status,
        strategy="PRB2" if status in {"CANDIDATE", "READY", "ENTRY_TRIGGERED", "PENDING_ENTRY"} else None,
        side="LONG" if status in {"CANDIDATE", "READY", "ENTRY_TRIGGERED", "PENDING_ENTRY"} else None,
        signals_found=1 if status in {"CANDIDATE", "READY", "ENTRY_TRIGGERED", "PENDING_ENTRY"} else 0,
        payload={"scanner_status": status, "signals": []},
    )


def _scanner_result() -> dict:
    return {"signals": [], "rejected": [], "near_misses": [], "signals_found": 0, "universe_size": 0}


def test_default_all_universe_behavior_is_unchanged(tmp_path) -> None:
    result = _scanner_result()
    with (
        patch("src.symbol_universe.load_stock_symbols", return_value=["AAPL"]),
        patch.object(server.da, "load_watchlist", return_value={"MSFT": {}}),
        patch.object(server, "run_market_screener", return_value=result) as scanner,
    ):
        payload = server.api_market_screener(_request())

    assert scanner.call_args.kwargs["symbols"] == ["AAPL", "MSFT"]
    assert scanner.call_args.kwargs["params"].asset_class == "stock"
    assert payload["universe_source"] == "ALL_UNIVERSE"
    assert payload["orders_submitted"] == 0


def test_radar_setups_with_multiple_valid_structured_statuses_scope_backend_symbols(tmp_path) -> None:
    rows = [_radar(symbol, "NOT_SCANNED") for symbol in ("AAA", "BBB", "CCC", "DDD", "EEE")]
    service = _service_with_scans(
        tmp_path,
        rows,
        [
            _scan("AAA", "CANDIDATE"),
            _scan("BBB", "READY"),
            _scan("CCC", "ENTRY_TRIGGERED"),
            _scan("DDD", "PENDING_ENTRY"),
            _scan("EEE", "CANDIDATE"),
        ],
    )
    result = _scanner_result()
    with (
        patch.object(server, "_radar_service", return_value=service),
        patch("src.symbol_universe.load_stock_symbols", return_value=["AAA", "BBB", "CCC", "DDD", "EEE", "ZZZ"]),
        patch.object(server.da, "load_watchlist", return_value={}),
        patch.object(server, "run_market_screener", return_value=result) as scanner,
    ):
        payload = server.api_market_screener(_request(), scan_universe="radar_setups")

    assert scanner.call_args.kwargs["symbols"] == ["AAA", "BBB", "CCC", "DDD", "EEE"]
    assert payload["eligible_symbol_count"] == 5
    assert payload["actual_scanned_symbol_count"] == 5
    assert payload["radar_universe"]["source_scan_id"].startswith("radar:2026-10-12:")
    assert payload["radar_universe"]["selected"][0]["scan_timestamp"] == "2026-10-12T21:20:00+00:00"


def test_radar_setups_exclude_no_setup_no_scan_empty_unknown_error_and_terminal(tmp_path) -> None:
    symbols = ("AAA", "NOPE", "SKIP", "MISS", "UNKN", "FAIL", "DONE")
    service = _service_with_scans(
        tmp_path,
        [_radar(symbol, "NOT_SCANNED") for symbol in symbols],
        [
            _scan("AAA", "CANDIDATE"),
            _scan("NOPE", "NO_SETUP"),
            _scan("SKIP", "NO_SCAN"),
            _scan("MISS", ""),
            _scan("UNKN", "SOMETHING_ELSE"),
            _scan("FAIL", "ERROR"),
            _scan("DONE", "INVALIDATED"),
        ],
    )
    with (
        patch.object(server, "_radar_service", return_value=service),
        patch("src.symbol_universe.load_stock_symbols", return_value=["AAA", "NOPE", "SKIP"]),
        patch.object(server.da, "load_watchlist", return_value={}),
        patch.object(server, "run_market_screener", return_value=_scanner_result()) as scanner,
    ):
        payload = server.api_market_screener(_request(), scan_universe="radar_setups")

    assert scanner.call_args.kwargs["symbols"] == ["AAA"]
    excluded = payload["radar_universe"]["excluded_status_counts"]
    assert excluded["NO_SETUP"] == 1
    assert excluded["NO_SCAN"] == 1
    assert excluded["UNKNOWN_STATUS"] == 1
    assert excluded["ERROR"] == 1
    assert excluded["INVALIDATED"] == 1


def test_empty_radar_setup_list_does_not_fall_back_to_full_universe(tmp_path) -> None:
    service = _service_with_scans(
        tmp_path,
        [_radar("AAA", "NOT_SCANNED"), _radar("BBB", "NOT_SCANNED")],
        [_scan("AAA", "NO_SETUP"), _scan("BBB", "NO_SCAN")],
    )
    with (
        patch.object(server, "_radar_service", return_value=service),
        patch("src.symbol_universe.load_stock_symbols", return_value=["AAA", "BBB", "FULL"]),
        patch.object(server.da, "load_watchlist", return_value={}),
        patch.object(server, "run_market_screener") as scanner,
    ):
        with pytest.raises(HTTPException) as raised:
            server.api_market_screener(_request(), scan_universe="radar_setups")

    assert raised.value.status_code == 409
    assert raised.value.detail["reason_code"] == "RADAR_NO_ELIGIBLE_SETUPS"
    scanner.assert_not_called()


def test_stale_older_radar_setup_is_not_scanned_when_latest_snapshot_has_no_setup(tmp_path) -> None:
    service = _service_with_scans(
        tmp_path,
        [_radar("OLD", "NOT_SCANNED", as_of="2026-10-09"), _radar("NEW", "NOT_SCANNED", as_of="2026-10-12")],
        [_scan("OLD", "CANDIDATE", radar_as_of="2026-10-09")],
    )
    with (
        patch.object(server, "_radar_service", return_value=service),
        patch("src.symbol_universe.load_stock_symbols", return_value=["OLD", "NEW", "FULL"]),
        patch.object(server.da, "load_watchlist", return_value={}),
        patch.object(server, "run_market_screener") as scanner,
    ):
        with pytest.raises(HTTPException) as raised:
            server.api_market_screener(_request(), scan_universe="radar_setups")

    assert raised.value.detail["radar_universe"]["stale_symbol_count"] == 1
    scanner.assert_not_called()


def test_missing_or_invalid_radar_data_blocks_scan() -> None:
    service = type("BrokenRadar", (), {"list_results": lambda self, limit=5000: [{"symbol": "AAA", "scanner_status": "CANDIDATE"}]})()
    with (
        patch.object(server, "_radar_service", return_value=service),
        patch.object(server, "run_market_screener") as scanner,
    ):
        with pytest.raises(HTTPException) as raised:
            server.api_market_screener(_request(), scan_universe="radar_setups")

    assert raised.value.detail["reason_code"] == "RADAR_SNAPSHOT_INVALID"
    scanner.assert_not_called()


def test_duplicate_radar_tickers_are_deduplicated_before_scanning() -> None:
    service = type(
        "DuplicateRadar",
        (),
        {
            "list_results": lambda self, limit=5000: [
                _radar("AAA", "CANDIDATE").to_dict(),
                replace(_radar("AAA", "READY"), radar_score=70.0).to_dict(),
                _radar("BBB", "CANDIDATE").to_dict(),
            ]
        },
    )()
    with (
        patch.object(server, "_radar_service", return_value=service),
        patch("src.symbol_universe.load_stock_symbols", return_value=["AAA", "BBB", "FULL"]),
        patch.object(server.da, "load_watchlist", return_value={}),
        patch.object(server, "run_market_screener", return_value=_scanner_result()) as scanner,
    ):
        payload = server.api_market_screener(_request(), scan_universe="radar_setups")

    assert scanner.call_args.kwargs["symbols"] == ["AAA", "BBB"]
    assert payload["eligible_symbol_count"] == 2


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_radar_universe_preserves_screener_side_filter(tmp_path, side: str) -> None:
    service = _service_with_scans(tmp_path, [_radar("AAA", "NOT_SCANNED")], [_scan("AAA", "CANDIDATE")])
    with (
        patch.object(server, "_radar_service", return_value=service),
        patch("src.symbol_universe.load_stock_symbols", return_value=["AAA", "FULL"]),
        patch.object(server.da, "load_watchlist", return_value={}),
        patch.object(server, "run_market_screener", return_value=_scanner_result()) as scanner,
    ):
        server.api_market_screener(_request(), scan_universe="radar_setups", side=side)

    assert scanner.call_args.kwargs["symbols"] == ["AAA"]
    assert scanner.call_args.kwargs["params"].side_filter == side


def test_radar_universe_preserves_multiple_selected_screener_strategies(tmp_path) -> None:
    service = _service_with_scans(tmp_path, [_radar("AAA", "NOT_SCANNED")], [_scan("AAA", "CANDIDATE")])
    with (
        patch.object(server, "_radar_service", return_value=service),
        patch("src.symbol_universe.load_stock_symbols", return_value=["AAA", "FULL"]),
        patch.object(server.da, "load_watchlist", return_value={}),
        patch.object(server, "run_market_screener", return_value=_scanner_result()) as scanner,
    ):
        server.api_market_screener(_request(), scan_universe="radar_setups", strategies="LP1,PRB2")

    assert scanner.call_args.kwargs["params"].strategies == ("LP1", "PRB2")


def test_radar_failure_has_no_trading_side_effects(tmp_path) -> None:
    service = _service_with_scans(tmp_path, [_radar("AAA", "NOT_SCANNED")], [_scan("AAA", "NO_SETUP")])
    with (
        patch.object(server, "_radar_service", return_value=service),
        patch.object(server, "run_market_screener") as scanner,
    ):
        with pytest.raises(HTTPException) as raised:
            server.api_market_screener(_request(), scan_universe="radar_setups")

    assert raised.value.detail["orders_submitted"] == 0
    scanner.assert_not_called()


def test_existing_crypto_full_universe_scanning_remains_functional() -> None:
    result = _scanner_result()
    with (
        patch.object(
            server,
            "_okx_demo_spot_universe",
            return_value={
                "valid": True,
                "source": "OKX_DEMO_ACCOUNT",
                "selected_symbols": ["BTC-USDT"],
                "eligible_symbol_count": 1,
            },
        ),
        patch.object(server.da, "load_crypto_dashboard_state", return_value={"watchlist": {}}),
        patch.object(server, "run_market_screener", return_value=result) as scanner,
    ):
        payload = server.api_market_screener(_request("/api/crypto/market-screener"))

    assert scanner.call_args.kwargs["symbols"] == ["BTC-USDT"]
    assert scanner.call_args.kwargs["params"].asset_class == "crypto"
    assert payload["universe_source"] == "ALL_UNIVERSE"
