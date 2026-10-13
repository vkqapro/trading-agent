from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from dashboard_react import server
from src.daily_trader import DailyTraderScanRequest, DailyTraderService
from src.radar.models import RadarResult, RadarSettings
from src.radar.models import RadarScannerResult
from src.radar.service import RadarService
from src.storage.trend_radar_store import TrendRadarStore
from tools import daily_trader_api


ET = ZoneInfo("America/New_York")


def _radar(symbol: str, score: float, *, breakout: bool = False, flip: bool = False) -> RadarResult:
    return RadarResult(
        symbol=symbol,
        as_of="2026-10-09",
        trend="GREEN",
        trend_score=score,
        radar_score=score,
        breakout=breakout,
        breakout_score=score if breakout else 0,
        trend_flip=flip,
        price=100.0,
    )


def _service(tmp_path, scanner):
    store = TrendRadarStore(tmp_path / "decision.db")
    store.upsert(_radar("AAA", 90, breakout=True))
    store.upsert(_radar("BBB", 80, flip=True))
    store.upsert(_radar("CCC", 70))
    store.upsert(_radar("DDD", 60, breakout=True, flip=True))
    radar = RadarService(
        store=store,
        settings=RadarSettings(min_avg_dollar_volume=1),
        scanner_runner=scanner,
    )
    return DailyTraderService(
        radar_service=radar,
        now=lambda: datetime(2026, 10, 12, 10, 35, tzinfo=ET),
    )


def test_scheduled_selection_is_top_plus_breakouts_plus_flips_and_deduplicated(tmp_path):
    calls: list[str] = []

    def scanner(symbol: str):
        calls.append(symbol)
        return {"signals": []}

    service = _service(tmp_path, scanner)
    result = service.scan(DailyTraderScanRequest(max_symbols=5))

    assert result["selection"] == {
        "top_n": 10,
        "top": 4,
        "breakouts": 2,
        "fresh_flips": 2,
        "unique_symbols": 4,
        "max_symbols": 5,
    }
    assert set(calls) == {"AAA", "BBB", "CCC", "DDD"}
    assert result["scanner"] == {"requested": 4, "completed": 4, "failed": 0}
    assert result["summary"]["no_setup"] == 4
    assert result["read_only"] is True
    assert result["orders_submitted"] == 0


def test_lifecycle_and_authoritative_fields_are_classified_without_recomputation(tmp_path):
    def scanner(symbol: str):
        if symbol == "AAA":
            return {
                "signals": [
                    {
                        "strategy": "PRB1",
                        "side": "SHORT",
                        "status": "pending_entry",
                        "lifecycle_status": "PENDING_ENTRY",
                        "lifecycle_reason": "waiting_for_entry",
                        "entry_triggered": False,
                        "level_price": 44.0,
                        "planned_entry_price": 45.47,
                        "entry_price": 45.47,
                        "stop_price": 43.26,
                        "take_profit_price": 49.89,
                        "rr": 2.0,
                        "score": 0.70,
                        "pattern_quality": 0.81,
                        "position_size_shares": 80,
                        "direction_conflict": True,
                        "data_quality_complete": False,
                        "missing_quality_data": ["spread"],
                    }
                ]
            }
        if symbol == "BBB":
            return {
                "signals": [
                    {
                        "strategy": "LP2",
                        "side": "SHORT",
                        "lifecycle_status": "INVALIDATED",
                        "lifecycle_reason": "pre_entry_structure_broken",
                        "entry_triggered": False,
                    }
                ]
            }
        return {"signals": []}

    service = _service(tmp_path, scanner)
    result = service.scan(DailyTraderScanRequest(mode="top", top_n=2, max_symbols=2))

    assert [item["symbol"] for item in result["active"]] == ["AAA"]
    assert [item["symbol"] for item in result["terminal"]] == ["BBB"]
    assert result["summary"]["active_candidates"] == 1
    assert result["summary"]["pending_entry"] == 1
    assert result["summary"]["invalidated"] == 1
    assert result["active"][0]["scanner"]["planned_entry"] == 45.47
    assert result["active"][0]["scanner"]["direction_conflict"] is True
    assert result["active"][0]["scanner"]["missing_quality_data"] == ["spread"]
    assert result["orders_submitted"] == 0


def test_one_symbol_failure_isolated_and_zero_candidate_report_is_useful(tmp_path):
    def scanner(symbol: str):
        if symbol == "AAA":
            raise RuntimeError("scanner unavailable")
        return {"signals": []}

    service = _service(tmp_path, scanner)
    result = service.scan(DailyTraderScanRequest(mode="top", top_n=2, max_symbols=2))

    assert result["scanner"]["failed"] == 1
    assert result["summary"]["errors"] == 1
    assert result["summary"]["no_setup"] == 1
    assert result["active"] == []
    assert result["orders_submitted"] == 0


def test_health_is_read_only_and_reports_latest_radar(tmp_path):
    service = _service(tmp_path, lambda _symbol: {"signals": []})

    assert service.health() == {
        "status": "ok",
        "read_only": True,
        "orders_submitted": 0,
        "radar_available": True,
        "scanner_available": True,
        "latest_radar_as_of": "2026-10-09",
    }


def test_persisted_setups_reads_scanner_store_without_scanning(tmp_path):
    calls: list[str] = []

    def scanner(symbol: str):
        calls.append(symbol)
        return {"signals": []}

    service = _service(tmp_path, scanner)
    service.radar.store.upsert_scanner_result(
        RadarScannerResult(
            symbol="AAA",
            radar_as_of="2026-10-09",
            source_scan_id="persisted-1",
            scan_timestamp="2026-10-12T14:00:00+00:00",
            scanner_status="CANDIDATE",
            scanner_label="LP1 LONG",
            strategy="LP1",
            side="LONG",
            signals_found=1,
            payload={
                "scanner_status": "CANDIDATE",
                "signals": [
                    {
                        "strategy": "LP1",
                        "side": "LONG",
                        "lifecycle_status": "PENDING_ENTRY",
                        "planned_entry_price": 101.0,
                    }
                ],
            },
        )
    )

    result = service.persisted_setups(side="LONG")

    assert calls == []
    assert result["read_only"] is True
    assert result["orders_submitted"] == 0
    assert result["scanner"]["requested"] == 0
    assert result["summary"]["active_candidates"] == 1
    assert [item["symbol"] for item in result["active"]] == ["AAA"]


def test_daily_trader_api_setups_wraps_existing_radar_endpoint(monkeypatch):
    def fake_raw(path: str):
        assert path == "/api/radar?limit=5000"
        return {
            "updated_at": "2026-10-12T14:00:00+00:00",
            "results": [
                {
                    "symbol": "AAA",
                    "scanner_result": {
                        "scanner_status": "CANDIDATE",
                        "scanner_label": "LP1 LONG",
                        "scan_timestamp": "2026-10-12T14:00:00+00:00",
                        "source_scan_id": "persisted-1",
                        "payload": {
                            "signals": [
                                {
                                    "strategy": "LP1",
                                    "side": "LONG",
                                    "lifecycle_status": "PENDING_ENTRY",
                                }
                            ]
                        },
                    },
                },
                {
                    "symbol": "BBB",
                    "scanner_result": {
                        "scanner_status": "CANDIDATE",
                        "payload": {"signals": [{"strategy": "PRB1", "side": "SHORT"}]},
                    },
                },
            ],
        }

    monkeypatch.setattr(daily_trader_api, "_raw_request", fake_raw)

    result = daily_trader_api._setups("LONG")

    assert result["read_only"] is True
    assert result["orders_submitted"] == 0
    assert result["side"] == "LONG"
    assert result["summary"]["active_candidates"] == 1
    assert [item["symbol"] for item in result["active"]] == ["AAA"]


def test_daily_trader_report_preserves_authoritative_tickers_and_source_fields():
    result = {
        "mode": "persisted_setups",
        "side": "LONG",
        "read_only": True,
        "orders_submitted": 0,
        "updated_at": "2026-10-12T14:00:00+00:00",
        "summary": {"active_candidates": 2, "terminal": 2, "persisted_setups": 4},
        "active": [
            {
                "symbol": "VEEV",
                "radar": {"trend": "GREEN", "radar_score": 91.2},
                "scanner": {
                    "strategy": "LP1",
                    "side": "LONG",
                    "status": "READY",
                    "lifecycle_status": "PENDING_ENTRY",
                    "scanner_status": "CANDIDATE",
                    "scanner_label": "LP1 LONG",
                    "planned_entry_price": 101.23,
                    "entry_price": 101.45,
                    "stop_price": 98.0,
                    "take_profit_price": 108.0,
                    "rr": 2.5,
                },
            },
            {
                "symbol": "BRK.B",
                "radar": {"trend": "GREEN", "radar_score": 88.0},
                "scanner": {
                    "strategy": "LP2",
                    "side": "LONG",
                    "status": "READY",
                    "lifecycle_status": "ENTRY_TRIGGERED",
                    "scanner_status": "READY",
                    "planned_entry_price": 500.01,
                    "entry_price": 500.25,
                },
            },
        ],
        "terminal": [
            {
                "symbol": "BF.B",
                "radar": {"trend": "RED", "radar_score": 72.0},
                "scanner": {
                    "strategy": "PRB1",
                    "side": "LONG",
                    "status": "INVALIDATED",
                    "lifecycle_status": "INVALIDATED",
                    "scanner_status": "CANDIDATE",
                },
            },
            {
                "symbol": "AB12",
                "radar": {"trend": "GREEN", "radar_score": 61.0},
                "scanner": {
                    "strategy": "LP1",
                    "side": "LONG",
                    "status": "EXPIRED",
                    "lifecycle_status": "EXPIRED",
                    "scanner_status": "CANDIDATE",
                },
            },
        ],
    }

    report = daily_trader_api._render_setups_report(result)

    assert "symbol=VEEV" in report
    assert "symbol=VEV" not in report
    assert "symbol=BRK.B" in report
    assert "symbol=BF.B" in report
    assert "symbol=AB12" in report
    assert "planned_entry_price=101.23" in report
    assert "entry_price=101.45" in report
    assert "lifecycle_status=PENDING_ENTRY" in report
    assert "scanner_trade_status=READY" in report
    assert "ticker_integrity=validated_against_source_json" in report


def test_daily_trader_report_validation_rejects_rendered_ticker_drift():
    rendered = "active:\nsymbol=VEV | lifecycle_status=PENDING_ENTRY"

    with pytest.raises(RuntimeError, match="Rendered ticker integrity check failed"):
        daily_trader_api._validate_rendered_symbols(rendered, ["VEEV"])


def test_fastapi_contract_exposes_daily_trader_routes(tmp_path, monkeypatch):
    service = _service(tmp_path, lambda _symbol: {"signals": []})
    monkeypatch.setattr(server, "_daily_trader_service", lambda: service)
    client = TestClient(server.app)

    scan = client.post(
        "/api/daily-trader/scan",
        json={"mode": "top", "top_n": 1, "max_symbols": 1},
    )
    health = client.get("/api/daily-trader/health")
    symbol = client.get("/api/daily-trader/symbol/AAA")

    assert scan.status_code == 200
    assert scan.json()["read_only"] is True
    assert scan.json()["orders_submitted"] == 0
    assert scan.json()["scanner"]["requested"] == 1
    assert health.status_code == 200
    assert health.json()["scanner_available"] is True
    assert symbol.status_code == 200
    assert symbol.json()["symbol"] == "AAA"
