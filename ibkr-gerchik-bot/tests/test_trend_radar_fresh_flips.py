from pathlib import Path
from dataclasses import replace

from src.radar.engine import analyze_daily_bars
from src.radar.models import RadarSettings
from src.radar.service import RadarService
from src.storage.trend_radar_store import TrendRadarStore


PROJECT_ROOT = Path(__file__).parents[1]
RADAR_PAGE = PROJECT_ROOT / "dashboard_react" / "index.html"


def _bars():
    import pandas as pd

    rows = []
    for index in range(180):
        price = 100.0 + index * 0.2
        rows.append(
            {
                "date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=index),
                "open": price - 0.2,
                "high": price + 0.25,
                "low": price - 0.25,
                "close": price,
                "volume": 1_000_000,
            }
        )
    return pd.DataFrame(rows)


def test_three_fresh_flips_resolve_to_exact_symbols_and_use_batch_scanner(tmp_path) -> None:
    store = TrendRadarStore(tmp_path / "decision.db")
    settings = RadarSettings(min_avg_dollar_volume=1_000_000)
    calls: list[str] = []

    for symbol in ("AAA", "BBB", "CCC"):
        result = analyze_daily_bars(symbol, _bars(), settings=settings)
        assert result is not None
        store.upsert(
            replace(
                result,
                trend_flip=True,
                trend_flip_from="RED",
                trend_flip_to=result.trend,
                trend_flip_date=result.as_of,
                trend_flip_age_days=0,
            )
        )

    def scanner(symbol: str) -> dict:
        calls.append(symbol)
        return {"signals_found": 0, "signals": []}

    service = RadarService(store=store, settings=settings, scanner_runner=scanner)
    fresh = service.list_results(filters={"trend_flip": True})
    symbols = [row["symbol"] for row in fresh]
    batch = service.scan_symbols(symbols, max_workers=3)

    assert symbols == ["CCC", "BBB", "AAA"] or set(symbols) == {"AAA", "BBB", "CCC"}
    assert set(calls) == {"AAA", "BBB", "CCC"}
    assert len(calls) == 3
    assert batch["completed"] == 3
    assert batch["orders_submitted"] == 0
    assert {item["status"] for item in batch["results"]} == {"NO_SETUP"}


def test_zero_fresh_flips_do_not_call_scanner(tmp_path) -> None:
    calls: list[str] = []

    def scanner(symbol: str) -> dict:
        calls.append(symbol)
        return {"signals_found": 0, "signals": []}

    service = RadarService(
        store=TrendRadarStore(tmp_path / "decision.db"),
        settings=RadarSettings(min_avg_dollar_volume=1_000_000),
        scanner_runner=scanner,
    )

    assert service.list_results(filters={"trend_flip": True}) == []
    assert calls == []


def test_fresh_flip_button_and_trend_flips_view_share_predicate_and_feedback() -> None:
    source = RADAR_PAGE.read_text(encoding="utf-8")

    assert "const isFreshTrendFlip = row => row?.trend_flip === true;" in source
    assert "filter==='FLIP'&&isFreshTrendFlip(row)" in source
    assert "rows.filter(isFreshTrendFlip).map(row=>row.symbol)" in source
    assert "onClick={runFreshFlipBatch} disabled={batchBusy}" in source
    assert "NO FRESH TREND FLIPS TO SCAN" in source
    assert "state==='error'" in source
    assert "BATCH SCAN FAILED" in source
    assert "fetch(`${API}/radar/batch-scan`" in source
