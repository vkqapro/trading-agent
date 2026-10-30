from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backtest_engine_v24.4"))

from starlette.requests import Request

from dashboard_react import server
from scanner.data_adapter import LoadedBars
from scanner_mcp.service import StrategyScannerService


def _request() -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/api/market-screener",
        "query_string": b"timeframe=1D&strategies=PRB1&side=LONG&anchor_date=2026-10-01",
        "headers": [], "scheme": "http", "server": ("test", 80), "client": ("test", 1),
    })


def test_production_scanner_health_is_lightweight_and_never_runs_universe_scan(monkeypatch) -> None:
    def fail_scan(*args, **kwargs):
        raise AssertionError("health probe must not run the production universe scan")

    monkeypatch.setattr(server, "run_market_screener", fail_scan)
    started = time.perf_counter()
    payload = server.api_market_screener_health()
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0
    assert payload["status"] == "ok"
    assert payload["read_only"] is True
    assert payload["scanner_available"] is True
    assert payload["orders_submitted"] == 0
    assert payload["latest_artifact_readable"] is True


def test_no_setup_scan_status_is_still_scanner_healthy(monkeypatch) -> None:
    from ops.trading_platform_ops import Supervisor

    monkeypatch.setattr(
        "ops.trading_platform_ops._get_json",
        lambda url: {
            "status": "ok",
            "read_only": True,
            "scanner_available": True,
            "last_scan_status": "NO_SETUP",
            "orders_submitted": 0,
        },
    )
    result = Supervisor(sleep=lambda _: None)._api(
        "http://127.0.0.1:8550/api/market-screener/health", "Production Scanner"
    )

    assert result["status"] == "HEALTHY"
    assert result["response"]["scanner_available"] is True
    assert result["response"]["last_scan_status"] == "NO_SETUP"


def test_production_web_endpoint_and_mcp_runner_share_snapshot() -> None:
    as_of = "2026-10-01T16:25:02Z"
    snapshot = server._daily_live_frame("GSHD", "stock")
    loaded = LoadedBars(
        "GSHD", "1D", snapshot.set_index("date").rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}),
        str(snapshot.iloc[0]["date"]), str(snapshot.iloc[-1]["date"]), False, len(snapshot), "fixture",
    )
    with (
        patch("src.symbol_universe.load_stock_symbols", return_value=["GSHD"]),
        patch.object(server.da, "load_watchlist", return_value={}),
        patch.object(server, "_screener_frame", return_value=snapshot),
        patch("scanner.runner.available_daily_symbols", return_value=["GSHD"]),
        patch("scanner.runner.load_closed_daily_bars", return_value=loaded),
    ):
        web = server.api_market_screener(_request(), strategies="PRB1", side="LONG", anchor_date="2026-10-01")
        with TemporaryDirectory() as root:
            service = StrategyScannerService(root)
            mcp = service.run_universe_scan(symbols=["GSHD"], strategies=["prb1"], as_of=as_of)
            mcp_signal = service.get_symbol_scan(mcp["run_id"], "GSHD")["strategies"]["prb1"]

    web_signal = web["signals"][0]
    diagnostic = {"web": web_signal, "mcp": mcp_signal, "anchor_date": "2026-10-01", "as_of": as_of}
    print("PRODUCTION_PARITY_DIAGNOSTIC=" + json.dumps(diagnostic, sort_keys=True, default=str))
    assert mcp_signal["status"] == "ENTRY_SIGNAL"
    assert web_signal["status"] == "READY"
    assert mcp_signal["level"]["price"] == web_signal["level_price"]
    assert mcp_signal["trade_plan"]["entry"] == web_signal["entry_price"]
    assert mcp_signal["trade_plan"]["stop"] == web_signal["stop_price"]
    assert mcp_signal["trade_plan"]["target"] == web_signal["take_profit_price"]
    assert mcp_signal["market"]["atr"] == web_signal["atr_clean_14"]
    assert mcp_signal["quality"]["score"] == web_signal["score"]
