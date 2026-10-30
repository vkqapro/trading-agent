from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from trading_research.analytics_service import ReviewRequest, TradingResearchService
from trading_research.candle_reader import CandleReader
from trading_research.config import ProductionDataPaths
from trading_research.journal_reader import OrderJournalReader, parse_timestamp
from trading_research.models import Candle
from trading_research.timezones import USNewYorkFallback


def _row(
    trade_id: str,
    *,
    symbol: str,
    strategy: str | None,
    side: str,
    entry: float | None,
    exit_price: float | None,
    stop: float | None,
    target: float | None,
    status: str = "CLOSED",
    closed_at: str = "2026-10-01T10:00:00-04:00",
) -> dict:
    return {
        "id": trade_id,
        "symbol": symbol,
        "market": "STOCK",
        "source": "fixture",
        "strategy": strategy,
        "side": side,
        "status": status,
        "created_at": "2026-10-01T09:29:00-04:00",
        "opened_at": "2026-10-01T09:30:00-04:00",
        "closed_at": closed_at,
        "planned_entry": entry,
        "planned_stop": stop,
        "planned_target": target,
        "actual_entry": entry,
        "actual_exit": exit_price,
        "quantity": 1,
    }


def _candle(timestamp: str, high: float, low: float, close: float) -> Candle:
    return Candle(
        timestamp=parse_timestamp(timestamp),
        open=Decimal("10"),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=Decimal("100"),
    )


def _service() -> TradingResearchService:
    rows = [
        _row("long-win", symbol="LONGWIN", strategy="PB1", side="LONG", entry=10, exit_price=12, stop=9, target=12),
        _row("long-loss", symbol="LONGLOSS", strategy="PB1", side="LONG", entry=10, exit_price=9, stop=9, target=12),
        _row("short-win", symbol="SHORTWIN", strategy="PB2", side="SHORT", entry=10, exit_price=8, stop=11, target=8),
        _row("short-loss", symbol="SHORTLOSS", strategy="PB2", side="SHORT", entry=10, exit_price=11, stop=11, target=8),
        _row("unknown", symbol="UNKNOWN", strategy=None, side="LONG", entry=10, exit_price=9, stop=9, target=12, status="CLOSED_UNKNOWN"),
        _row("unfilled", symbol="UNFILLED", strategy="PB1", side="LONG", entry=None, exit_price=None, stop=9, target=12, status="UNFILLED"),
        _row("rejected", symbol="REJECTED", strategy="PB1", side="LONG", entry=None, exit_price=None, stop=9, target=12, status="REJECTED"),
        _row("et-date", symbol="ETDATE", strategy="PB1", side="LONG", entry=10, exit_price=11, stop=9, target=12, closed_at="2026-10-02T01:00:00+00:00"),
    ]
    bars = {
        ("LONGWIN", "intraday_5m"): [_candle("2026-10-01T09:35:00-04:00", 12, 9.5, 12)],
        ("LONGLOSS", "intraday_5m"): [_candle("2026-10-01T09:35:00-04:00", 10.3, 9, 9)],
        ("SHORTWIN", "intraday_5m"): [_candle("2026-10-01T09:35:00-04:00", 10.2, 8, 8)],
        ("SHORTLOSS", "intraday_5m"): [_candle("2026-10-01T09:35:00-04:00", 11, 9.9, 11)],
        ("ETDATE", "intraday_5m"): [_candle("2026-10-01T20:00:00-04:00", 11, 9.8, 11)],
    }
    return TradingResearchService(OrderJournalReader(rows=rows), CandleReader(bars=bars))


def test_direction_aware_metrics_summary_and_status_exclusions() -> None:
    result = _service().trade_review(ReviewRequest(from_date=date(2026, 10, 1), to_date=date(2026, 10, 1)))
    assert result["summary"]["closed_trades"] == 5
    assert result["summary"]["wins"] == 3
    assert result["summary"]["losses"] == 2
    assert result["summary"]["net_pnl"] == 3.0
    assert result["summary"]["profit_factor"] == 2.5
    assert result["journal_statuses"]["by_group"]["ambiguous"] == 1
    assert result["journal_statuses"]["by_group"]["non_executed"] == 2

    by_id = {trade["trade_id"]: trade for trade in result["trades"]}
    assert by_id["long-win"]["realized_r"] == 2.0
    assert by_id["short-win"]["realized_r"] == 2.0
    assert by_id["long-loss"]["mae_r"] == 1.0
    assert by_id["short-loss"]["mae_r"] == 1.0
    assert by_id["long-win"]["first_stop_or_target_touch"]["outcome"] == "TARGET"
    assert by_id["short-win"]["first_stop_or_target_touch"]["outcome"] == "TARGET"
    assert by_id["long-loss"]["first_stop_or_target_touch"]["outcome"] == "STOP"
    assert by_id["short-loss"]["first_stop_or_target_touch"]["outcome"] == "STOP"


def test_date_strategy_and_loser_filters_preserve_eastern_time_contract() -> None:
    service = _service()
    pb1 = service.trade_review(ReviewRequest(from_date=date(2026, 10, 1), to_date=date(2026, 10, 1), strategies=("pb1",)))
    assert pb1["summary"]["closed_trades"] == 3
    assert set(pb1["strategies"]) == {"PB1"}

    losses = service.trade_review(
        ReviewRequest(from_date=date(2026, 10, 1), to_date=date(2026, 10, 1), strategies=("PB2",), outcome="loss")
    )
    assert losses["summary"]["closed_trades"] == 1
    assert losses["summary"]["losses"] == 1
    assert losses["trades"][0]["trade_id"] == "short-loss"


def test_missing_data_and_strategy_registry_are_explicit() -> None:
    service = _service()
    result = service.trade_review(ReviewRequest(from_date=date(2026, 10, 1), to_date=date(2026, 10, 1)))
    assert "strategy" in result["data_quality"]["missing_fields"]
    assert result["data_quality"]["missing_field_counts"]["strategy"] == 1
    pb1 = service.list_strategies("PB1")["strategy"]
    assert pb1["definition_status"] == "source_definition_unavailable"
    assert "Do not infer" in pb1["documentation_markdown"]
    assert {entry["strategy_id"] for entry in service.list_strategies()["strategies"]} >= {"PB1", "PB2", "LB1", "LB2"}


def test_embedded_production_strategy_id_is_normalized_without_guessing() -> None:
    reader = OrderJournalReader(rows=[
        _row("prb2", symbol="PRB", strategy="MARKET_SCREENER_PRB2_MANUAL_SETUP", side="LONG", entry=10, exit_price=11, stop=9, target=12)
    ])
    assert reader.read()[0].strategy == "PRB2"


def test_request_matching_does_not_promote_same_symbol_closed_trade_to_open(tmp_path: Path) -> None:
    """Closed rows must match the request, not merely an active same-symbol position."""
    paths = ProductionDataPaths(repository_root=tmp_path)
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "runtime").mkdir()
    (tmp_path / "memory" / "order_requests.json").write_text(
        json.dumps(
            {
                "requests": [
                    {
                        "id": "closed-fitb",
                        "action": "place",
                        "status": "done",
                        "created_at": "2026-10-21T12:16:05",
                        "updated_at": "2026-10-21T12:16:12",
                        "symbol": "FITB",
                        "signal": "BUY",
                        "entry": 52.87,
                        "stop": 50.2,
                        "target": 59.54,
                        "quantity": 5,
                        "result": {"market_order_id": 1001, "status": "executed"},
                    },
                    {
                        "id": "open-fitb",
                        "action": "place",
                        "status": "done",
                        "created_at": "2026-10-22T12:13:15",
                        "updated_at": "2026-10-22T12:13:19",
                        "symbol": "FITB",
                        "signal": "BUY",
                        "entry": 52.67,
                        "stop": 52.17,
                        "target": 53.92,
                        "quantity": 18,
                        "result": {"market_order_id": 2001, "status": "executed"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "memory" / "runtime" / "closed_positions.json").write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "symbol": "FITB",
                        "request_id": "closed-fitb",
                        "closed_at": "2026-10-22T16:11:39+00:00",
                        "exit_price": 52.96,
                        "exit_quantity": 5,
                        "exit_execution_id": "fitb-close",
                        "broker_realized_pnl": -1.55547,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "memory" / "runtime" / "state.json").write_text(
        json.dumps(
            {
                "tracked_positions": [
                    {
                        "symbol": "FITB",
                        "market_order_id": 2001,
                        "opened_at": "2026-10-22T12:13:19-04:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    rows = {row.trade_id: row for row in OrderJournalReader(paths=paths).read()}

    assert rows["stock:req:closed-fitb"].status == "CLOSED_OBSERVED"
    assert rows["stock:req:closed-fitb"].realized_pnl == Decimal("-1.55547")
    assert rows["stock:req:open-fitb"].status == "OPEN"


def test_duplicate_request_closes_use_latest_execution_backed_close(tmp_path: Path) -> None:
    """A repeated request id must resolve to its latest persisted close."""
    paths = ProductionDataPaths(repository_root=tmp_path)
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "runtime").mkdir()
    (tmp_path / "memory" / "order_requests.json").write_text(
        json.dumps(
            {
                "requests": [
                    {
                        "id": "mrsh-request",
                        "action": "place",
                        "status": "done",
                        "created_at": "2026-07-29T10:41:14",
                        "updated_at": "2026-07-29T10:41:19",
                        "symbol": "MRSH",
                        "signal": "BUY",
                        "entry": 191.63,
                        "stop": 187.56,
                        "target": 199.77,
                        "quantity": 5,
                        "result": {"market_order_id": 627, "status": "executed"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "memory" / "runtime" / "closed_positions.json").write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "symbol": "MRSH",
                        "request_id": "mrsh-request",
                        "closed_at": "2026-08-13T15:29:09+00:00",
                        "exit_price": 187.3,
                        "exit_execution_id": "mrsh-old",
                    },
                    {
                        "symbol": "MRSH",
                        "request_id": "mrsh-request",
                        "closed_at": "2026-10-22T20:37:28+00:00",
                        "exit_price": 195.48,
                        "exit_quantity": 5,
                        "exit_execution_id": "mrsh-new",
                        "broker_realized_pnl": 40.429851,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "memory" / "runtime" / "state.json").write_text(
        json.dumps({"tracked_positions": []}),
        encoding="utf-8",
    )

    row = OrderJournalReader(paths=paths).read()[0]

    assert row.status == "CLOSED_OBSERVED"
    assert row.exit_time == parse_timestamp("2026-10-22T20:37:28+00:00")
    assert row.realized_pnl == Decimal("40.429851")


def test_research_package_has_no_execution_or_production_module_dependency() -> None:
    package = Path(__file__).resolve().parents[2] / "trading_research"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package.rglob("*.py"))
    assert "from src." not in source
    assert "import src." not in source
    for forbidden in ("place_order(", "submit_order(", "cancel_order(", "modify_order(", "approve_order(", "execute_trade("):
        assert forbidden not in source


def test_eastern_fallback_observes_modern_us_daylight_saving() -> None:
    fallback = USNewYorkFallback()
    assert parse_timestamp("2026-07-01T12:00:00+00:00").astimezone(fallback).utcoffset().total_seconds() == -4 * 60 * 60
    assert parse_timestamp("2026-01-01T12:00:00+00:00").astimezone(fallback).utcoffset().total_seconds() == -5 * 60 * 60
