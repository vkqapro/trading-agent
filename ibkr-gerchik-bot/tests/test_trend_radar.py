from __future__ import annotations

import sqlite3
from dataclasses import replace

import pandas as pd

from src.radar.engine import analyze_daily_bars
from src.radar.models import RadarScannerResult, RadarSettings
from src.radar.service import RadarService, direction_conflict
from src.storage.trend_radar_store import TrendRadarStore


def _bars(*, breakout: bool = False) -> pd.DataFrame:
    rows = []
    price = 100.0
    for index in range(180):
        if index < 140:
            price += 0.20
        elif index < 179:
            price = 128.0 + (index % 3) * 0.08
        else:
            price = 131.0 if breakout else 128.2
        rows.append({
            "date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=index),
            "open": price - 0.2,
            "high": price + (1.0 if index == 179 and breakout else 0.25),
            "low": price - 0.25,
            "close": price,
            "volume": 2_000_000 if index == 179 and breakout else 1_000_000,
        })
    return pd.DataFrame(rows)


def test_engine_is_deterministic_and_explainable() -> None:
    bars = _bars(breakout=True)
    first = analyze_daily_bars("TEST", bars, settings=RadarSettings(min_avg_dollar_volume=1_000_000))
    second = analyze_daily_bars("TEST", bars, settings=RadarSettings(min_avg_dollar_volume=1_000_000))
    assert first is not None
    assert first.to_dict() == second.to_dict()
    assert 0 <= first.trend_score <= 100
    assert 0 <= first.breakout_score <= 100
    assert 0 <= first.momentum_score <= 100
    assert 0 <= first.radar_score <= 100
    assert "trend_green" in first.reasons


def test_persistence_drives_previous_trend_and_flip(tmp_path) -> None:
    store = TrendRadarStore(tmp_path / "decision.db")
    settings = RadarSettings(min_avg_dollar_volume=1_000_000)
    prior_bars = _bars().copy()
    prior_bars["date"] = pd.to_datetime(prior_bars["date"]) - pd.Timedelta(days=1)
    old = analyze_daily_bars("TEST", prior_bars, settings=settings)
    assert old is not None
    store.upsert(old)
    service = RadarService(
        store=store,
        settings=settings,
        universe_loader=lambda: ["TEST"],
        bars_loader=lambda _symbol, _timeframe: _bars(breakout=True),
    )
    payload = service.refresh()
    assert payload["results"]
    latest = store.latest("TEST")
    assert latest is not None
    assert latest.previous_trend == old.trend
    assert latest.trend_flip is False or latest.trend != old.trend


def test_store_uses_unique_symbol_date_key(tmp_path) -> None:
    path = tmp_path / "decision.db"
    store = TrendRadarStore(path)
    result = analyze_daily_bars("TEST", _bars(), settings=RadarSettings(min_avg_dollar_volume=1_000_000))
    assert result is not None
    store.upsert(result)
    store.upsert(result)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM radar_snapshots").fetchone()[0] == 1


def test_radar_serializes_authoritative_closed_daily_price(tmp_path) -> None:
    result = analyze_daily_bars("TEST", _bars(), settings=RadarSettings(min_avg_dollar_volume=1_000_000))
    assert result is not None
    payload = result.to_dict()
    assert payload["price"] == result.price
    assert payload["close_price"] == result.price
    restored = TrendRadarStore(tmp_path / "decision.db")
    restored.upsert(result)
    assert restored.latest("TEST").price == result.price


def test_legacy_snapshot_price_is_backfilled_from_closed_daily_bar(tmp_path) -> None:
    result = analyze_daily_bars("TEST", _bars(), settings=RadarSettings(min_avg_dollar_volume=1_000_000))
    assert result is not None
    store = TrendRadarStore(tmp_path / "decision.db")
    store.upsert(replace(result, price=None))
    service = RadarService(
        store=store,
        settings=RadarSettings(min_avg_dollar_volume=1_000_000),
        bars_loader=lambda _symbol, _timeframe: _bars(),
    )
    payload = service.get("TEST")
    assert payload is not None
    assert payload["price"] == result.price
    assert store.latest("TEST").price == result.price


def test_direction_conflict_is_deterministic_and_neutral_for_grey() -> None:
    expected = {
        ("GREEN", "SHORT"): True,
        ("GREEN", "LONG"): False,
        ("RED", "LONG"): True,
        ("RED", "SHORT"): False,
        ("GREY", "LONG"): False,
        ("GREY", "SHORT"): False,
    }
    assert {key: direction_conflict(*key) for key in expected} == expected


def test_scanner_signals_receive_per_signal_direction_conflict_metadata(tmp_path) -> None:
    store = TrendRadarStore(tmp_path / "decision.db")
    radar = analyze_daily_bars("AMZN", _bars(), settings=RadarSettings(min_avg_dollar_volume=1_000_000))
    assert radar is not None
    store.upsert(replace(radar, trend="GREEN"))
    service = RadarService(
        store=store,
        settings=RadarSettings(min_avg_dollar_volume=1_000_000),
        scanner_runner=lambda _symbol: {
            "signals": [
                {"strategy": "LP2", "side": "SHORT"},
                {"strategy": "LP1", "side": "LONG"},
            ],
        },
    )
    payload = service.scan_symbol("AMZN")
    assert [signal["direction_conflict"] for signal in payload["signals"]] == [True, False]
    assert payload["scanner_status"] == "CANDIDATE"


def test_terminal_scanner_lifecycle_is_exposed_by_single_and_batch_radar_scans(tmp_path) -> None:
    def scanner(_symbol: str) -> dict:
        return {
            "signals_found": 1,
            "signals": [{
                "strategy": "LP2",
                "side": "SHORT",
                "status": "invalidated",
                "lifecycle_status": "INVALIDATED",
                "lifecycle_reason": "pre_entry_structure_broken",
                "entry_triggered": False,
                "trade_occurred": False,
                "realized_pnl": 0.0,
                "realized_r": 0.0,
            }],
        }

    service = RadarService(
        store=TrendRadarStore(tmp_path / "decision.db"),
        settings=RadarSettings(min_avg_dollar_volume=1_000_000),
        scanner_runner=scanner,
    )

    single = service.scan_symbol("AMZN")
    batch = service.scan_symbols(["AMZN"], max_workers=1)

    assert single["scanner_status"] == "INVALIDATED"
    assert single["signals"][0]["lifecycle_status"] == "INVALIDATED"
    assert single["signals"][0]["trade_occurred"] is False
    assert batch["statuses"] == {"AMZN": "INVALIDATED"}
    assert batch["results"][0]["label"] == "LP2 SHORT · INVALIDATED"
    assert batch["orders_submitted"] == 0


def test_batch_scan_isolated_results_and_no_order_submission(tmp_path) -> None:
    calls: list[str] = []

    def scanner(symbol: str) -> dict:
        calls.append(symbol)
        if symbol == "FAIL":
            raise RuntimeError("scanner unavailable")
        if symbol == "GOOD":
            return {"signals_found": 1, "signals": [{"strategy": "PRB1", "side": "LONG"}]}
        return {"signals_found": 0, "signals": []}

    service = RadarService(
        store=TrendRadarStore(tmp_path / "decision.db"),
        settings=RadarSettings(min_avg_dollar_volume=1_000_000),
        scanner_runner=scanner,
    )
    batch = service.scan_symbols(["GOOD", "NONE", "FAIL"], max_workers=2)
    assert batch["completed"] == 3
    assert batch["orders_submitted"] == 0
    by_symbol = {item["symbol"]: item for item in batch["results"]}
    assert by_symbol["GOOD"]["status"] == "CANDIDATE"
    assert by_symbol["GOOD"]["label"] == "PRB1 LONG"
    assert by_symbol["NONE"]["status"] == "NO_SETUP"
    assert by_symbol["FAIL"]["status"] == "ERROR"
    assert set(calls) == {"GOOD", "NONE", "FAIL"}


def test_single_scanner_result_persists_and_hydrates_after_reload(tmp_path) -> None:
    store = TrendRadarStore(tmp_path / "decision.db")
    radar = analyze_daily_bars("GOOD", _bars(), settings=RadarSettings(min_avg_dollar_volume=1_000_000))
    assert radar is not None
    store.upsert(radar)
    service = RadarService(
        store=store,
        settings=RadarSettings(min_avg_dollar_volume=1_000_000),
        scanner_runner=lambda _symbol: {
            "scan_timestamp": "2026-10-12T21:20:00+00:00",
            "signals_found": 1,
            "signals": [{"strategy": "PRB2", "side": "LONG"}],
        },
    )

    payload = service.scan_symbol("GOOD")
    reloaded = RadarService(store=TrendRadarStore(tmp_path / "decision.db")).list_results(limit=10)

    assert payload["scanner_status"] == "CANDIDATE"
    row = next(item for item in reloaded if item["symbol"] == "GOOD")
    assert row["scanner_status"] == "CANDIDATE"
    assert row["scanner_label"] == "PRB2 LONG"
    assert row["scanner_strategy"] == "PRB2"
    assert row["scanner_side"] == "LONG"
    assert row["scanner_scan_timestamp"] == "2026-10-12T21:20:00+00:00"


def test_no_setup_persists_and_supersedes_older_positive_result(tmp_path) -> None:
    store = TrendRadarStore(tmp_path / "decision.db")
    radar = analyze_daily_bars("GOOD", _bars(), settings=RadarSettings(min_avg_dollar_volume=1_000_000))
    assert radar is not None
    store.upsert(radar)
    store.upsert_scanner_result(
        RadarScannerResult(
            symbol="GOOD",
            radar_as_of=radar.as_of,
            source_scan_id="old-positive",
            scan_timestamp="2026-10-12T20:00:00+00:00",
            scanner_status="CANDIDATE",
            scanner_label="PRB2 LONG",
            strategy="PRB2",
            side="LONG",
            signals_found=1,
            payload={"scanner_status": "CANDIDATE"},
        )
    )
    service = RadarService(
        store=store,
        settings=RadarSettings(min_avg_dollar_volume=1_000_000),
        scanner_runner=lambda _symbol: {
            "scan_timestamp": "2026-10-12T21:00:00+00:00",
            "signals_found": 0,
            "signals": [],
        },
    )

    service.scan_symbol("GOOD")
    row = next(item for item in service.list_results(limit=10) if item["symbol"] == "GOOD")

    assert row["scanner_status"] == "NO_SETUP"
    assert row["scanner_label"] == "NO_SETUP"
    assert row["scanner_scan_timestamp"] == "2026-10-12T21:00:00+00:00"


def test_out_of_order_scanner_completion_does_not_replace_newer_result(tmp_path) -> None:
    store = TrendRadarStore(tmp_path / "decision.db")
    radar = analyze_daily_bars("GOOD", _bars(), settings=RadarSettings(min_avg_dollar_volume=1_000_000))
    assert radar is not None
    store.upsert(radar)
    newer = RadarScannerResult(
        symbol="GOOD",
        radar_as_of=radar.as_of,
        source_scan_id="newer",
        scan_timestamp="2026-10-12T21:00:00+00:00",
        scanner_status="NO_SETUP",
        scanner_label="NO_SETUP",
        signals_found=0,
        payload={"scanner_status": "NO_SETUP"},
    )
    older = RadarScannerResult(
        symbol="GOOD",
        radar_as_of=radar.as_of,
        source_scan_id="older",
        scan_timestamp="2026-10-12T20:00:00+00:00",
        scanner_status="CANDIDATE",
        scanner_label="PRB2 LONG",
        strategy="PRB2",
        side="LONG",
        signals_found=1,
        payload={"scanner_status": "CANDIDATE"},
    )

    store.upsert_scanner_result(newer)
    store.upsert_scanner_result(older)
    row = next(item for item in RadarService(store=store).list_results(limit=10) if item["symbol"] == "GOOD")

    assert row["scanner_status"] == "NO_SETUP"
    assert row["scanner_source_scan_id"] == "newer"


def test_same_day_radar_refresh_preserves_scanner_result_and_new_day_excludes_it(tmp_path) -> None:
    store = TrendRadarStore(tmp_path / "decision.db")
    radar = analyze_daily_bars("GOOD", _bars(), settings=RadarSettings(min_avg_dollar_volume=1_000_000))
    assert radar is not None
    store.upsert(radar)
    store.upsert_scanner_result(
        RadarScannerResult(
            symbol="GOOD",
            radar_as_of=radar.as_of,
            source_scan_id="same-day",
            scan_timestamp="2026-10-12T21:00:00+00:00",
            scanner_status="CANDIDATE",
            scanner_label="PRB2 LONG",
            strategy="PRB2",
            side="LONG",
            signals_found=1,
            payload={"scanner_status": "CANDIDATE"},
        )
    )
    store.upsert(replace(radar, radar_score=radar.radar_score - 1.0))
    same_day = next(item for item in RadarService(store=store).list_results(limit=10) if item["symbol"] == "GOOD")
    store.upsert(replace(radar, as_of="2026-10-13"))
    new_day = next(item for item in RadarService(store=store).list_results(limit=10) if item["symbol"] == "GOOD")

    assert same_day["scanner_status"] == "CANDIDATE"
    assert same_day["scanner_label"] == "PRB2 LONG"
    assert new_day["scanner_status"] == "NOT_SCANNED"
    assert "scanner_label" not in new_day
