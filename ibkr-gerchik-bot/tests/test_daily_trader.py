from __future__ import annotations

import json
import time
from datetime import datetime
from threading import Event, Thread
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from dashboard_react import server
from src.daily_trader import DailyTraderScanJobManager, DailyTraderScanRequest, DailyTraderService
from src.radar.models import RadarResult, RadarSettings
from src.radar.models import RadarScannerResult
from src.radar.service import RadarService
from src.storage.trend_radar_store import TrendRadarStore
from tools import daily_trader_api


ET = ZoneInfo("America/New_York")


def _radar(
    symbol: str,
    score: float,
    *,
    breakout: bool = False,
    flip: bool = False,
    as_of: str = "2026-10-09",
    trend: str = "GREEN",
    flip_from: str | None = None,
    flip_to: str | None = None,
) -> RadarResult:
    return RadarResult(
        symbol=symbol,
        as_of=as_of,
        trend=trend,
        trend_score=score,
        radar_score=score,
        breakout=breakout,
        breakout_score=score if breakout else 0,
        trend_flip=flip,
        trend_flip_from=flip_from,
        trend_flip_to=flip_to,
        trend_flip_date=as_of if flip else None,
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
        "current_radar_row_count": 4,
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
    assert result["active"][0]["scanner"]["scanner_status"] == "CANDIDATE"
    assert result["active"][0]["scanner"]["planned_entry_price"] == 101.0


def test_persisted_setups_excludes_historical_as_of_from_current_universe(tmp_path):
    calls: list[str] = []

    def scanner(symbol: str):
        calls.append(symbol)
        return {"signals": []}

    store = TrendRadarStore(tmp_path / "decision.db")
    store.upsert(_radar("OLD", 95, as_of="2026-10-08"))
    store.upsert(_radar("NEW", 90, as_of="2026-10-09"))
    store.upsert_scanner_result(
        RadarScannerResult(
            symbol="OLD",
            radar_as_of="2026-10-08",
            source_scan_id="old-setup",
            scan_timestamp="2026-10-08T14:00:00+00:00",
            scanner_status="CANDIDATE",
            scanner_label="PRB2 LONG",
            strategy="PRB2",
            side="LONG",
            signals_found=1,
            payload={
                "signals": [
                    {
                        "strategy": "PRB2",
                        "side": "LONG",
                        "lifecycle_status": "PENDING_ENTRY",
                    }
                ]
            },
        )
    )
    radar = RadarService(
        store=store,
        settings=RadarSettings(min_avg_dollar_volume=1),
        scanner_runner=scanner,
    )
    service = DailyTraderService(
        radar_service=radar,
        now=lambda: datetime(2026, 10, 12, 10, 35, tzinfo=ET),
    )

    result = service.persisted_setups(side="LONG")

    assert calls == []
    assert result["radar_as_of"] == "2026-10-09"
    assert result["selection"]["persisted_scanner_results"] == 0
    assert result["summary"]["active_candidates"] == 0
    assert result["active"] == []


def test_daily_trader_api_setups_wraps_existing_radar_endpoint(monkeypatch):
    def fake_request(path: str):
        assert path == "/api/daily-trader/setups?side=LONG"
        return {
            "scan_id": "persisted",
            "generated_at": "2026-10-12T14:00:00-04:00",
            "market_status": "OPEN",
            "mode": "persisted_setups",
            "side": "LONG",
            "read_only": True,
            "orders_submitted": 0,
            "radar_as_of": "2026-10-12",
            "selection": {"persisted_scanner_results": 2, "unique_symbols": 2, "max_symbols": 2},
            "scanner": {"requested": 0, "completed": 2, "failed": 0},
            "summary": {
                "active_candidates": 1,
                "entry_triggered": 0,
                "pending_entry": 1,
                "invalidated": 0,
                "expired": 0,
                "ambiguous": 0,
                "no_setup": 0,
                "errors": 0,
            },
            "active": [
                {
                    "symbol": "AAA",
                    "radar": {"trend": "GREEN"},
                    "scanner": {"strategy": "LP1", "side": "LONG", "lifecycle_status": "PENDING_ENTRY"},
                },
            ],
            "terminal": [],
            "no_setup": [],
            "errors": [],
        }

    monkeypatch.setattr(daily_trader_api, "_request", fake_request)

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
        "generated_at": "2026-10-12T14:00:00+00:00",
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
    assert "lifecycle_note=ENTRY_TRIGGERED and READY describe scanner lifecycle states, not broker execution." in report
    assert "generated_at=" in report
    assert "\nscan_timestamp=" not in report
    assert "ticker_integrity=validated_against_source_json" in report


def test_daily_trader_report_marks_invalidated_conflict_as_informational():
    report = daily_trader_api._render_setups_report(
        {
            "mode": "persisted_setups",
            "side": "SHORT",
            "read_only": True,
            "orders_submitted": 0,
            "active": [],
            "terminal": [
                {
                    "symbol": "KDP",
                    "radar": {"trend": "GREEN"},
                    "scanner": {
                        "strategy": "LP2",
                        "side": "SHORT",
                        "status": "INVALIDATED",
                        "lifecycle_status": "INVALIDATED",
                        "direction_conflict": True,
                    },
                }
            ],
        }
    )

    assert "lifecycle_status=INVALIDATED" in report
    assert "invalidation_reason=not_provided" in report
    assert "warning=WARNING: Radar GREEN / Scanner SHORT - direction conflict (informational only)" in report


def test_daily_trader_market_report_renders_historical_error_details_safely():
    base = {
        "status": "ok",
        "production_scanner": {
            "status": "ok",
            "latest_scan": {
                "run_id": "historical",
                "last_scan_at": "2026-10-05T19:08:42Z",
                "last_scan_status": "COMPLETED_WITH_ERRORS",
                "symbols_requested": 96,
                "symbols_scanned": 95,
                "symbols_skipped": 1,
                "error_count": 2,
                "errors": [
                    {"symbol": "BTM", "reason": "NO_PERSISTED_DAILY_DATA"},
                    {"symbol": "XYZ", "reason": "DATA_SOURCE_TIMEOUT"},
                ],
            },
        },
        "persisted_scanner_results": {"available": True, "radar_as_of": "2026-10-13", "current_result_count": 42},
        "radar": {"as_of": "2026-10-13", "current_row_count": 352},
        "radar_setups": {},
        "market_screener": {},
        "read_only": True,
        "orders_submitted": 0,
        "approvals_created": 0,
        "execution_intents_created": 0,
    }

    report = daily_trader_api._render_market_report(base)

    assert "error_details=BTM:NO_PERSISTED_DAILY_DATA;XYZ:DATA_SOURCE_TIMEOUT" in report
    base["production_scanner"]["latest_scan"]["errors"] = []
    base["production_scanner"]["latest_scan"]["error_count"] = 0
    assert "error_details=-" in daily_trader_api._render_market_report(base)


def test_daily_trader_report_shows_direction_conflict_and_legitimate_signal_tiers():
    report = daily_trader_api._render_setups_report(
        {
            "mode": "persisted_setups",
            "side": "LONG",
            "read_only": True,
            "orders_submitted": 0,
            "summary": {"active_candidates": 2},
            "active": [
                {
                    "symbol": "KDP",
                    "radar": {"trend": "GREEN"},
                    "scanner": {
                        "strategy": "PRB2",
                        "side": "LONG",
                        "level": 31.10,
                        "planned_entry_price": 100.0,
                        "entry_price": 100.0,
                        "stop_price": 95.0,
                        "take_profit_price": 110.0,
                        "lifecycle_status": "ENTRY_TRIGGERED",
                        "direction_conflict": False,
                    },
                },
                {
                    "symbol": "KDP",
                    "radar": {"trend": "GREEN"},
                    "scanner": {
                        "strategy": "PRB2",
                        "side": "LONG",
                        "level_price": 30.75,
                        "planned_entry_price": 100.0,
                        "entry_price": 100.0,
                        "stop_price": 95.0,
                        "take_profit_price": 110.0,
                        "lifecycle_status": "READY",
                        "direction_conflict": False,
                    },
                },
                {
                    "symbol": "SO",
                    "radar": {"trend": "GREEN"},
                    "scanner": {
                        "strategy": "LP2",
                        "side": "SHORT",
                        "level_price": 82.5,
                        "lifecycle_status": "PENDING_ENTRY",
                        "direction_conflict": True,
                    },
                },
            ],
            "terminal": [],
        }
    )

    assert "symbol=KDP | strategy=PRB2 | side=LONG | signal_ordinal=1 | level_price=31.1" in report
    assert "symbol=KDP | strategy=PRB2 | side=LONG | signal_ordinal=2 | level_price=30.75" in report
    assert report.count("planned_entry_price=100.0") == 2
    assert report.count(" | entry_price=100.0") == 2
    assert "symbol=SO | strategy=LP2 | side=SHORT | signal_ordinal=3 | level_price=82.5" in report
    assert "warning=WARNING: Radar GREEN / Scanner SHORT - direction conflict (informational only)" in report
    assert "orders_submitted=0" in report


def test_completed_scan_report_includes_full_uuid_candidate_ids_for_active_and_terminal():
    scan_id = "84a89696-f592-47d2-aad8-e1a7fffae9ba"
    report = daily_trader_api._render_scan_report(
        {
            "scan_id": scan_id,
            "status": "COMPLETED",
            "radar_as_of": "2026-10-13",
            "read_only": True,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
            "scanner": {},
            "result": {
                "active": [{
                    "symbol": "KDP",
                    "radar": {"trend": "GREEN"},
                    "scanner": {
                        "strategy": "PRB2",
                        "side": "LONG",
                        "level_price": 32.38,
                        "lifecycle_status": "ENTRY_TRIGGERED",
                    },
                }],
                "terminal": [{
                    "symbol": "KDP",
                    "radar": {"trend": "GREEN"},
                    "scanner": {
                        "strategy": "LP2",
                        "side": "SHORT",
                        "level_price": 32.705,
                        "lifecycle_status": "INVALIDATED",
                    },
                }],
            },
        }
    )
    assert f"candidate_id={scan_id}:active:KDP:1" in report
    assert f"candidate_id={scan_id}:terminal:KDP:1" in report


def test_daily_trader_report_separates_authoritative_invalidation_reason_from_conflict():
    report = daily_trader_api._render_setups_report(
        {
            "mode": "persisted_setups",
            "side": "SHORT",
            "read_only": True,
            "orders_submitted": 0,
            "active": [],
            "terminal": [
                {
                    "symbol": "KDP",
                    "radar": {"trend": "GREEN"},
                    "scanner": {
                        "strategy": "LP2",
                        "side": "SHORT",
                        "status": "INVALIDATED",
                        "lifecycle_status": "INVALIDATED",
                        "lifecycle_reason": "pre_entry_structure_broken",
                        "direction_conflict": True,
                    },
                }
            ],
        }
    )

    assert "lifecycle_status=INVALIDATED" in report
    assert "invalidation_reason=pre_entry_structure_broken" in report
    assert "warning=WARNING: Radar GREEN / Scanner SHORT - direction conflict (informational only)" in report


def test_daily_trader_market_report_labels_historical_artifact_and_current_results():
    report = daily_trader_api._render_market_report(
        {
            "status": "ok",
            "generated_at": "2026-10-14T10:00:00-04:00",
            "radar": {"as_of": "2026-10-13", "current_row_count": 352},
            "production_scanner": {
                "scanner_available": True,
                "status": "COMPLETED_WITH_ERRORS",
                "latest_scan": {
                    "run_id": "scan_20261005T190557Z_9af666",
                    "last_scan_at": "2026-10-05T19:05:57+00:00",
                    "last_scan_status": "COMPLETED_WITH_ERRORS",
                    "symbols_scanned": 95,
                    "symbols_requested": 96,
                    "symbols_skipped": 1,
                    "error_count": 1,
                },
            },
            "persisted_scanner_results": {
                "available": True,
                "radar_as_of": "2026-10-13",
                "current_result_count": 42,
            },
            "radar_setups": {},
            "market_screener": {"status": "ok", "last_scan_status": "COMPLETED_WITH_ERRORS"},
            "read_only": True,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
        }
    )

    assert "current_daily_trader_scanner_results=source_table=radar_scanner_results, snapshot_as_of=2026-10-13, result_count=42" in report
    assert "historical_production_scanner_artifact=run_id=scan_20261005T190557Z_9af666, completed_at=2026-10-05T19:05:57+00:00, status=COMPLETED_WITH_ERRORS, symbols_scanned=95, symbols_requested=96, symbols_skipped=1, errors=1" in report
    assert "orders_submitted=0" in report
    assert "approvals_created=0" in report
    assert "execution_intents_created=0" in report


def test_daily_trader_report_labels_legacy_top_level_scan_timestamp_as_generated_at():
    report = daily_trader_api._render_setups_report(
        {
            "mode": "persisted_setups",
            "side": "LONG",
            "read_only": True,
            "orders_submitted": 0,
            "scan_timestamp": "2026-10-12T14:00:00-04:00",
            "summary": {},
            "active": [],
            "terminal": [],
        }
    )

    assert "generated_at=2026-10-12T14:00:00-04:00" in report
    assert "\nscan_timestamp=2026-10-12T14:00:00-04:00" not in report


def test_daily_trader_report_validation_rejects_rendered_ticker_drift():
    rendered = "active:\nsymbol=VEV | lifecycle_status=PENDING_ENTRY"

    with pytest.raises(RuntimeError, match="Rendered ticker integrity check failed"):
        daily_trader_api._validate_rendered_symbols(rendered, ["VEEV"])


def test_daily_trader_flips_report_groups_by_current_trend_and_preserves_source_fields():
    rows: list[dict] = []
    for index in range(7):
        rows.append(
            {
                "symbol": f"GRN{index}",
                "trend": "GREEN",
                "trend_flip_from": "GREY",
                "trend_flip_to": "GREEN",
                "trend_flip_date": "2026-10-12",
                "as_of": "2026-10-12",
                "radar_score": 90 - index,
            }
        )
    for index in range(7):
        rows.append(
            {
                "symbol": f"GRY{index}",
                "trend": "GREY",
                "trend_flip_from": "RED",
                "trend_flip_to": "GREY",
                "trend_flip_date": "2026-10-12",
                "as_of": "2026-10-12",
                "radar_score": 80 - index,
            }
        )
    rows.append(
        {
            "symbol": "NUE",
            "trend": "RED",
            "trend_flip_from": "GREEN",
            "trend_flip_to": "GREY",
            "trend_flip_date": "2026-10-12",
            "as_of": "2026-10-12",
            "radar_score": 71.5,
        }
    )
    rows.extend(
        {
            "symbol": symbol,
            "trend": "RED",
            "trend_flip_from": "GREY",
            "trend_flip_to": "RED",
            "trend_flip_date": "2026-10-12",
            "as_of": "2026-10-12",
            "radar_score": 70 - index,
        }
        for index, symbol in enumerate(["BRK.B", "BF.B", "R2D2", "RED3", "RED4"], start=1)
    )

    interleaved_rows = [rows[0], rows[8], rows[15], *[row for index, row in enumerate(rows) if index not in {0, 8, 15}]]
    report = daily_trader_api._render_flips_report(
        {
            "mode": "fresh_flips",
            "radar_as_of": "2026-10-12",
            "source_updated_at": "2026-10-12T21:00:00+00:00",
            "limit": 20,
            "count": len(interleaved_rows),
            "read_only": True,
            "orders_submitted": 0,
            "results": interleaved_rows,
        }
    )

    assert "rendered_rows=20" in report
    assert "returned_rows=20" in report
    assert "source_table=radar_snapshots" in report
    assert "GREEN (7):" in report
    assert "GREY (7):" in report
    assert "RED (6):" in report
    assert "symbol=NUE | current_trend=RED | previous_trend=GREEN | flip_to=GREY" in report
    assert report.index("RED (6):") < report.index("symbol=NUE")
    assert "symbol=BRK.B" in report
    assert "symbol=BF.B" in report
    assert "ticker_integrity=validated_against_source_json" in report


def test_daily_trader_flips_report_does_not_invent_missing_optional_fields():
    report = daily_trader_api._render_flips_report(
        {
            "mode": "fresh_flips",
            "limit": 20,
            "count": 1,
            "read_only": True,
            "orders_submitted": 0,
            "results": [{"symbol": "MISSING", "trend": "GREEN", "as_of": "2026-10-12"}],
        }
    )

    assert "symbol=MISSING" in report
    assert "previous_trend=-" in report
    assert "flip_to=-" in report
    assert "flip_date=-" in report
    assert "radar_score=-" in report


def test_daily_trader_flips_report_rejects_count_mismatch():
    with pytest.raises(RuntimeError, match="Rendered flip count integrity check failed"):
        daily_trader_api._render_flips_report(
            {
                "mode": "fresh_flips",
                "limit": 20,
                "count": 2,
                "read_only": True,
                "orders_submitted": 0,
                "results": [{"symbol": "AAA", "trend": "GREEN"}],
            }
        )


def test_fastapi_contract_exposes_daily_trader_routes(tmp_path, monkeypatch):
    service = _service(tmp_path, lambda _symbol: {"signals": []})
    monkeypatch.setattr(server, "_daily_trader_service", lambda: service)
    monkeypatch.setattr(
        server,
        "_production_scanner_health",
        lambda: {"status": "ok", "scanner_available": True, "orders_submitted": 0},
    )
    monkeypatch.setattr(
        server,
        "_radar_setup_universe",
        lambda: {"available": True, "valid": True, "eligible_symbol_count": 1},
    )
    monkeypatch.setattr(server, "_DAILY_TRADER_SCAN_JOBS", None)
    client = TestClient(server.app)

    scan = client.post(
        "/api/daily-trader/scan",
        json={"mode": "top", "top_n": 1, "max_symbols": 1},
    )
    health = client.get("/api/daily-trader/health")
    symbol = client.get("/api/daily-trader/symbol/AAA")
    setups = client.get("/api/daily-trader/setups")
    market = client.get("/api/daily-trader/market")

    assert scan.status_code == 200
    assert scan.json()["read_only"] is True
    assert scan.json()["orders_submitted"] == 0
    assert scan.json()["status"] in {"QUEUED", "RUNNING", "COMPLETED"}
    scan_id = scan.json()["scan_id"]
    scan_status = client.get(f"/api/daily-trader/scans/{scan_id}")
    for _ in range(20):
        if scan_status.json()["status"] == "COMPLETED":
            break
        time.sleep(0.05)
        scan_status = client.get(f"/api/daily-trader/scans/{scan_id}")
    assert scan_status.status_code == 200
    assert scan_status.json()["status"] == "COMPLETED"
    assert scan_status.json()["result"]["scanner"]["requested"] == 1
    assert health.status_code == 200
    assert health.json()["scanner_available"] is True
    assert symbol.status_code == 200
    assert symbol.json()["symbol"] == "AAA"
    assert setups.status_code == 200
    assert setups.json()["mode"] == "persisted_setups"
    assert market.status_code == 200
    assert market.json()["orders_submitted"] == 0
    assert market.json()["radar_setups"]["eligible_count"] == 1


def test_daily_trader_market_report_is_read_only_and_surfaces_required_fields(tmp_path):
    service = _service(tmp_path, lambda _symbol: {"signals": []})

    result = service.market_report(
        dashboard_api={"status": "ok", "api_available": True},
        production_scanner={
            "status": "ok",
            "scanner_available": True,
            "last_scan_status": "COMPLETED",
            "orders_submitted": 0,
        },
        radar_universe={
            "available": True,
            "valid": True,
            "eligible_symbol_count": 3,
            "snapshot_as_of": "2026-10-09",
        },
    )

    assert result["read_only"] is True
    assert result["orders_submitted"] == 0
    assert result["approvals_created"] == 0
    assert result["execution_intents_created"] == 0
    assert result["dashboard_api"]["status"] == "ok"
    assert result["radar"]["as_of"] == "2026-10-09"
    assert result["production_scanner"]["scanner_available"] is True
    assert result["radar_setups"]["eligible_count"] == 3
    assert result["market_session"] == "OPEN"


def test_daily_trader_market_report_renderer_preserves_safety_fields():
    report = daily_trader_api._render_market_report(
        {
            "status": "ok",
            "generated_at": "2026-10-13T10:00:00-04:00",
            "market_session": "OPEN",
            "dashboard_api": {"status": "ok"},
            "radar": {"as_of": "2026-10-12", "current_row_count": 10},
            "production_scanner": {"scanner_available": True, "status": "ok"},
            "persisted_scanner_results": {"available": True, "current_result_count": 4},
            "radar_setups": {"valid": True, "eligible_count": 2},
            "market_screener": {"status": "ok", "last_scan_status": "COMPLETED"},
            "warnings": [],
            "read_only": True,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
        }
    )

    assert "Daily Trader market health" in report
    assert "radar_setups_eligible_count=2" in report
    assert "orders_submitted=0" in report
    assert "approvals_created=0" in report
    assert "execution_intents_created=0" in report


def _wait_for_job(manager: DailyTraderScanJobManager, scan_id: str, terminal: set[str] | None = None) -> dict:
    terminal = terminal or {"COMPLETED", "FAILED"}
    status = manager.status(scan_id)
    for _ in range(50):
        if status and status["status"] in terminal:
            return status
        time.sleep(0.05)
        status = manager.status(scan_id)
    assert status is not None
    return status


def test_async_scan_job_returns_promptly_and_completes_with_status(tmp_path):
    release = Event()
    calls: list[str] = []

    def scanner(symbol: str):
        calls.append(symbol)
        release.wait(2)
        return {"signals": []}

    service = _service(tmp_path, scanner)
    manager = DailyTraderScanJobManager(service_factory=lambda: service)
    started = time.monotonic()
    accepted = manager.submit(DailyTraderScanRequest(mode="top", top_n=1, max_symbols=1))
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert accepted["scan_id"]
    assert accepted["status"] in {"QUEUED", "RUNNING"}
    release.set()
    status = _wait_for_job(manager, accepted["scan_id"])
    assert status["status"] == "COMPLETED"
    assert status["processed_count"] == 1
    assert status["persisted_result_count"] == 1
    assert calls == ["AAA"]


def test_async_scan_job_records_scanner_symbol_errors_without_failing_whole_job(tmp_path):
    def scanner(symbol: str):
        raise RuntimeError(f"boom {symbol}")

    service = _service(tmp_path, scanner)
    manager = DailyTraderScanJobManager(service_factory=lambda: service)
    accepted = manager.submit(DailyTraderScanRequest(mode="top", top_n=1, max_symbols=1))
    status = _wait_for_job(manager, accepted["scan_id"])

    assert status["status"] == "COMPLETED"
    assert status["result"]["summary"]["errors"] == 1
    assert status["orders_submitted"] == 0
    assert status["approvals_created"] == 0
    assert status["execution_intents_created"] == 0


def test_async_scan_job_marks_unhandled_scan_exception_failed(tmp_path, monkeypatch):
    service = _service(tmp_path, lambda _symbol: {"signals": []})
    manager = DailyTraderScanJobManager(service_factory=lambda: service)

    def fail_scan(*_args, **_kwargs):
        raise RuntimeError("scan crashed")

    monkeypatch.setattr(service, "scan", fail_scan)
    accepted = manager.submit(DailyTraderScanRequest(mode="top", top_n=1, max_symbols=1))
    status = _wait_for_job(manager, accepted["scan_id"])

    assert status["status"] == "FAILED"
    assert status["error"]["reason_code"] == "DAILY_TRADER_SCAN_FAILED"


def test_async_scan_idempotency_key_reuses_active_job_and_prevents_duplicate_execution(tmp_path):
    release = Event()
    calls: list[str] = []

    def scanner(symbol: str):
        calls.append(symbol)
        release.wait(2)
        return {"signals": []}

    service = _service(tmp_path, scanner)
    manager = DailyTraderScanJobManager(service_factory=lambda: service)
    request = DailyTraderScanRequest(mode="top", top_n=1, max_symbols=1, idempotency_key="retry-1")
    first = manager.submit(request)
    second = manager.submit(request)

    assert second["scan_id"] == first["scan_id"]
    release.set()
    status = _wait_for_job(manager, first["scan_id"])
    assert status["status"] == "COMPLETED"
    assert calls == ["AAA"]


def test_async_scan_concurrent_duplicate_requests_start_one_execution(tmp_path):
    release = Event()
    calls: list[str] = []

    def scanner(symbol: str):
        calls.append(symbol)
        release.wait(2)
        return {"signals": []}

    service = _service(tmp_path, scanner)
    manager = DailyTraderScanJobManager(service_factory=lambda: service)
    results: list[dict] = []

    def submit():
        results.append(manager.submit(DailyTraderScanRequest(mode="top", top_n=1, max_symbols=1)))

    threads = [Thread(target=submit) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    release.set()

    scan_ids = {item["scan_id"] for item in results}
    assert len(scan_ids) == 1
    status = _wait_for_job(manager, next(iter(scan_ids)))
    assert status["status"] == "COMPLETED"
    assert calls == ["AAA"]


def test_async_scan_manual_and_scheduled_overlap_reuses_active_job(tmp_path):
    release = Event()
    calls: list[str] = []

    def scanner(symbol: str):
        calls.append(symbol)
        release.wait(2)
        return {"signals": []}

    service = _service(tmp_path, scanner)
    manager = DailyTraderScanJobManager(service_factory=lambda: service)
    manual = manager.submit(DailyTraderScanRequest(mode="top", top_n=1, max_symbols=1, trigger_source="manual"))
    scheduled = manager.submit(DailyTraderScanRequest(mode="top", top_n=1, max_symbols=1, trigger_source="scheduled"))
    release.set()

    assert scheduled["scan_id"] == manual["scan_id"]
    status = _wait_for_job(manager, manual["scan_id"])
    assert status["status"] == "COMPLETED"
    assert calls == ["AAA"]


def test_async_scan_reconciles_running_job_after_dashboard_restart(tmp_path):
    service = _service(tmp_path, lambda _symbol: {"signals": []})
    request = DailyTraderScanRequest(mode="top", top_n=1, max_symbols=1)
    job, created = service.radar.store.create_or_reuse_daily_trader_scan_job(
        scan_id="abandoned",
        request_key=request.request_key(),
        request=request.normalized_payload(),
        idempotency_key=None,
        trigger_source="manual",
        radar_as_of="2026-10-09",
    )
    assert created is True
    service.radar.store.mark_daily_trader_scan_job_running(job["scan_id"])
    manager = DailyTraderScanJobManager(service_factory=lambda: service)

    status = manager.status("abandoned")

    assert status is not None
    assert status["status"] == "FAILED"
    assert status["error"]["reason_code"] == "DASHBOARD_RESTART_INTERRUPTED_SCAN"


def test_daily_trader_cli_polling_returns_running_scan_without_ambiguous_failure(monkeypatch):
    monkeypatch.setattr(
        daily_trader_api,
        "_scan_status",
        lambda scan_id: {
            "scan_id": scan_id,
            "status": "RUNNING",
            "mode": "scheduled",
            "top_n": 10,
            "max_symbols": 30,
            "read_only": True,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
        },
    )

    result = daily_trader_api._poll_scan(
        {
            "scan_id": "scan-123",
            "status": "RUNNING",
            "mode": "scheduled",
            "top_n": 10,
            "max_symbols": 30,
            "read_only": True,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
        },
        max_wait=0,
        poll_interval=0.01,
    )

    assert result["status"] == "RUNNING"
    assert result["scan_id"] == "scan-123"
    assert result["polling_exhausted"] is True


def test_daily_trader_scan_status_request_uses_get_only(monkeypatch):
    calls: list[tuple[str, str | None]] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "scan_id": "scan-123",
                    "status": "QUEUED",
                    "read_only": True,
                    "orders_submitted": 0,
                    "approvals_created": 0,
                    "execution_intents_created": 0,
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.get_method()))
        assert request.get_method() == "GET"
        assert request.data is None
        assert request.full_url.endswith("/api/daily-trader/scans/scan-123")
        return FakeResponse()

    monkeypatch.setattr(daily_trader_api, "urlopen", fake_urlopen)

    result = daily_trader_api._scan_status("scan-123")

    assert calls == [("http://127.0.0.1:8550/api/daily-trader/scans/scan-123", "GET")]
    assert result["status"] == "QUEUED"
    assert result["read_only"] is True
    assert result["orders_submitted"] == 0


def test_daily_trader_latest_scan_request_uses_completed_get_only(monkeypatch):
    calls: list[tuple[str, str | None]] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "scan_id": "latest-completed",
                    "status": "COMPLETED",
                    "radar_as_of": "2026-10-14",
                    "result": {"active": [], "terminal": []},
                    "read_only": True,
                    "orders_submitted": 0,
                    "approvals_created": 0,
                    "execution_intents_created": 0,
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.get_method()))
        assert request.get_method() == "GET"
        assert request.data is None
        assert request.full_url.endswith("/api/daily-trader/scans/latest")
        return FakeResponse()

    monkeypatch.setattr(daily_trader_api, "urlopen", fake_urlopen)

    result = daily_trader_api._latest_scan_request()

    assert calls == [("http://127.0.0.1:8550/api/daily-trader/scans/latest", "GET")]
    assert result["scan_id"] == "latest-completed"
    assert result["status"] == "COMPLETED"


def test_daily_trader_latest_cli_never_submits_scan(monkeypatch, capsys):
    calls: list[str] = []

    def fake_latest():
        calls.append("latest")
        return {
            "scan_id": "latest-completed",
            "status": "COMPLETED",
            "radar_as_of": "2026-10-14",
            "scanner": {"requested": 2, "completed": 2, "failed": 0},
            "summary": {"active_candidates": 1},
            "result": {"active": [], "terminal": []},
            "read_only": True,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
        }

    def forbidden_submit(_args):
        raise AssertionError("latest must not submit POST /api/daily-trader/scan")

    monkeypatch.setattr(daily_trader_api, "_latest_scan_request", fake_latest)
    monkeypatch.setattr(daily_trader_api, "_submit_scan", forbidden_submit)

    assert daily_trader_api.main(["latest", "--format", "report"]) == 0
    assert calls == ["latest"]
    output = capsys.readouterr().out
    assert "scan_id=latest-completed" in output
    assert "status=COMPLETED" in output
    assert "orders_submitted=0" in output


def test_daily_trader_scan_status_cli_never_posts_or_executes_scanner(monkeypatch, capsys):
    calls: list[str] = []

    def forbidden_submit(_args):
        raise AssertionError("scan-status must not submit POST /api/daily-trader/scan")

    def fake_status(scan_id: str):
        calls.append(scan_id)
        return {
            "scan_id": scan_id,
            "status": "RUNNING",
            "mode": "scheduled",
            "top_n": 10,
            "max_symbols": 30,
            "read_only": True,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
        }

    monkeypatch.setattr(daily_trader_api, "_submit_scan", forbidden_submit)
    monkeypatch.setattr(daily_trader_api, "_scan_status", fake_status)

    exit_code = daily_trader_api.main(["scan-status", "--scan-id", "scan-456", "--format", "report"])

    assert exit_code == 0
    assert calls == ["scan-456"]
    output = capsys.readouterr().out
    assert "status=RUNNING" in output
    assert "result=not available yet" in output
    assert "orders_submitted=0" in output
    assert "approvals_created=0" in output
    assert "execution_intents_created=0" in output


def test_daily_trader_scan_status_report_supports_queued_and_running():
    for status in ("QUEUED", "RUNNING"):
        report = daily_trader_api._render_scan_report(
            {
                "scan_id": f"{status.lower()}-id",
                "status": status,
                "mode": "scheduled",
                "read_only": True,
                "orders_submitted": 0,
                "approvals_created": 0,
                "execution_intents_created": 0,
            }
        )

        assert f"status={status}" in report
        assert "result=not available yet" in report
        assert "orders_submitted=0" in report


def test_daily_trader_scan_status_completed_returns_persisted_results_and_exact_tickers():
    report = daily_trader_api._render_scan_report(
        {
            "scan_id": "completed-1",
            "status": "COMPLETED",
            "mode": "scheduled",
            "read_only": True,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
            "result": {
                "radar_as_of": "2026-10-13",
                "scanner": {"requested": 4, "completed": 4, "failed": 1},
                "summary": {
                    "active_candidates": 1,
                    "entry_triggered": 0,
                    "pending_entry": 1,
                    "invalidated": 1,
                    "expired": 0,
                    "ambiguous": 0,
                    "no_setup": 1,
                    "errors": 1,
                },
                "active": [
                    {
                        "symbol": "BRK.B",
                        "radar": {"trend": "GREEN", "radar_score": 88.0},
                        "scanner": {
                            "strategy": "LP1",
                            "side": "LONG",
                            "status": "READY",
                            "lifecycle_status": "PENDING_ENTRY",
                            "scanner_status": "CANDIDATE",
                            "scanner_label": "LP1 LONG",
                        },
                    }
                ],
                "terminal": [
                    {
                        "symbol": "BF.B",
                        "radar": {"trend": "RED", "radar_score": 77.0},
                        "scanner": {
                            "strategy": "PRB1",
                            "side": "SHORT",
                            "status": "INVALIDATED",
                            "lifecycle_status": "INVALIDATED",
                            "scanner_status": "CANDIDATE",
                        },
                    }
                ],
                "no_setup": [{"symbol": "VEEV", "scanner_status": "NO_SETUP"}],
                "errors": [{"symbol": "R2D2", "scanner_status": "ERROR", "error": "scanner timeout"}],
            },
        }
    )

    assert "scan_id=completed-1" in report
    assert "scan_status_command=/scan-status scan_id=completed-1" in report
    assert "scanner=requested=4, symbols_processed=4, symbols_failed=1" in report
    assert "active_signal_candidates=1" in report
    assert "invalidated_signals=1" in report
    assert "symbols_without_setups=1" in report
    assert "symbol=BRK.B" in report
    assert "symbol=BF.B" in report
    assert "symbol=VEEV | scanner_status=NO_SETUP" in report
    assert "symbol=R2D2 | scanner_status=ERROR | error=scanner timeout" in report
    assert "ticker_integrity=validated_against_source_json" in report


def test_daily_trader_scan_report_preserves_full_id_and_copy_command_when_missing_id():
    full_id = "123e4567-e89b-12d3-a456-426614174000"
    report = daily_trader_api._render_scan_report(
        {
            "scan_id": full_id,
            "status": "RUNNING",
            "mode": "scheduled",
            "read_only": True,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
        }
    )

    assert f"scan_id={full_id}" in report
    assert f"scan_status_command=/scan-status scan_id={full_id}" in report
    assert full_id in report

    missing = daily_trader_api._render_scan_report(
        {
            "status": "RUNNING",
            "mode": "scheduled",
            "read_only": True,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
        }
    )
    assert "scan_id=not_available" in missing
    assert "scan_status_command=not_available" in missing


def test_daily_trader_scan_status_failed_returns_sanitized_error_and_side_effects():
    report = daily_trader_api._render_scan_report(
        {
            "scan_id": "failed-1",
            "status": "FAILED",
            "read_only": True,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
            "error": {
                "reason_code": "daily trader scan failed!",
                "type": "RuntimeError",
                "message": "first line\nsecond line",
            },
        }
    )

    assert "reason_code=DAILY_TRADER_SCAN_FAILED" in report
    assert "error_type=RuntimeError" in report
    assert "error_details=first line second line" in report
    assert "orders_submitted=0" in report
    assert "approvals_created=0" in report
    assert "execution_intents_created=0" in report


def test_daily_trader_scan_status_unknown_scan_id_is_graceful(monkeypatch):
    from urllib.error import HTTPError

    def fake_urlopen(_request, timeout):
        raise HTTPError(
            url="http://127.0.0.1:8550/api/daily-trader/scans/missing",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(daily_trader_api, "urlopen", fake_urlopen)

    result = daily_trader_api._scan_status("missing")
    report = daily_trader_api._render_scan_report(result)

    assert result["status"] == "NOT_FOUND"
    assert result["read_only"] is True
    assert result["orders_submitted"] == 0
    assert "reason_code=DAILY_TRADER_SCAN_NOT_FOUND" in report
    assert "No Daily Trader scan found for missing" in report


def test_daily_trader_scan_polling_exhaustion_points_to_scan_status(monkeypatch):
    monkeypatch.setattr(
        daily_trader_api,
        "_scan_status",
        lambda scan_id: {
            "scan_id": scan_id,
            "status": "RUNNING",
            "read_only": True,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
        },
    )

    result = daily_trader_api._poll_scan(
        {
            "scan_id": "scan-789",
            "status": "RUNNING",
            "read_only": True,
            "orders_submitted": 0,
            "approvals_created": 0,
            "execution_intents_created": 0,
        },
        max_wait=0,
        poll_interval=0.01,
    )
    report = daily_trader_api._render_scan_report(result)

    assert result["polling_exhausted"] is True
    assert "scan-status --scan-id scan-789" in result["message"]
    assert "scan-status --scan-id scan-789" in report


def test_daily_trader_reports_use_distinct_timestamp_labels(tmp_path):
    service = _service(tmp_path, lambda _symbol: {"signals": []})
    service.radar.store.upsert(
        _radar(
            "FLIP",
            88,
            as_of="2026-10-12",
            trend="RED",
            flip=True,
            flip_from="GREEN",
            flip_to="RED",
        )
    )

    radar = service.radar_rows(flips_only=True, limit=20)
    market = service.market_report(
        dashboard_api={"status": "ok", "api_available": True},
        production_scanner={"status": "ok", "scanner_available": True},
        radar_universe={"available": True, "valid": True, "eligible_symbol_count": 1},
    )
    market_report = daily_trader_api._render_market_report(market)
    flips_report = daily_trader_api._render_flips_report(
        {
            "mode": radar["mode"],
            "radar_as_of": radar["radar_as_of"],
            "source_updated_at": None,
            "limit": 20,
            "count": radar["count"],
            "read_only": radar["read_only"],
            "orders_submitted": radar["orders_submitted"],
            "results": radar["results"],
        }
    )

    assert "generated_at=2026-10-12T10:35:00-04:00" in market_report
    assert "market_session=OPEN" in market_report
    assert "radar_as_of=2026-10-12" in market_report
    assert "scan_timestamp=" not in market_report
    assert "radar_as_of=2026-10-12" in flips_report
    assert "flip_date=2026-10-12" in flips_report
    assert "scan_timestamp=" not in flips_report


def test_daily_trader_script_references_use_existing_repo_script_path():
    repo = Path(__file__).resolve().parents[1]
    script = repo / "tools" / "daily_trader_api.py"
    assert script.exists()

    daily_profile = Path("C:/Users/Vitaly/AppData/Local/hermes/profiles/daily-trader")
    if not daily_profile.exists():
        pytest.skip("installed daily-trader Hermes profile is not available")

    files = [daily_profile / "SOUL.md", *sorted((daily_profile / "skills").glob("daily-trader-*/SKILL.md"))]
    assert files
    stale = "trading-agent\\tools\\daily_trader_api.py"
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert stale not in text
        if "Join-Path $repo 'tools\\daily_trader_api.py'" in text:
            assert str(repo) in text
            assert "Join-Path $repo 'tools\\daily_trader_api.py'" in text

    flips_skill = daily_profile / "skills" / "daily-trader-flips" / "SKILL.md"
    assert "radar --flips --limit 20 --format report" in flips_skill.read_text(encoding="utf-8")

    scan_status_skill = daily_profile / "skills" / "daily-trader-scan-status" / "SKILL.md"
    scan_status_bundle = daily_profile / "skill-bundles" / "scan-status.yaml"
    assert scan_status_skill.exists()
    assert scan_status_bundle.exists()
    scan_status_text = scan_status_skill.read_text(encoding="utf-8")
    bundle_text = scan_status_bundle.read_text(encoding="utf-8")
    assert "scan-status --scan-id $scanId --format report" in scan_status_text
    assert "GET /api/daily-trader/scans/{scan_id}" in scan_status_text
    assert "POST /api/daily-trader/scan" in scan_status_text
    assert "must never" in scan_status_text
    assert "daily-trader-scan-status" in bundle_text
    assert "scan-status" in bundle_text

    latest_skill = daily_profile / "skills" / "daily-trader-latest-scan" / "SKILL.md"
    assert latest_skill.exists()
    latest_text = latest_skill.read_text(encoding="utf-8")
    assert "cmd.exe /d /c" in latest_text
    assert "daily_trader_api.py latest --format report" in latest_text
    assert "GET /api/daily-trader/scans/latest" in latest_text
    assert "POST /api/daily-trader/scan" in latest_text
