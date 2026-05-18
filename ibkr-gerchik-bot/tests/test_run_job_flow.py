from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

from src.config import SETTINGS
from src.main import run_job
from src.workflow_log import append_workflow_snapshot


def _stock_position(symbol: str, quantity: int, avg_cost: float, *, direction: str = "long") -> Dict[str, object]:
    signed_quantity = quantity if direction == "long" else -quantity
    return {
        "symbol": symbol,
        "position": float(signed_quantity),
        "avg_cost": avg_cost,
        "sec_type": "STK",
    }


class _BrokerStub:
    positions_sequence: List[List[Dict[str, object]]] = []

    def __init__(self) -> None:
        self.is_connected = False
        if not self.positions_sequence:
            raise AssertionError("Broker stub positions were not configured for this test.")
        self._positions = self.positions_sequence.pop(0)

    def connect(self) -> None:
        self.is_connected = True

    def disconnect(self) -> None:
        self.is_connected = False

    def get_account_summary(self) -> List[Dict[str, object]]:
        return [
            {"tag": "NetLiquidation", "value": 100000.0},
            {"tag": "AvailableFunds", "value": 80000.0},
        ]

    def get_positions(self) -> List[Dict[str, object]]:
        return list(self._positions)

    def get_open_orders(self) -> List[Dict[str, object]]:
        return []


class _NewsRiskFilterStub:
    def __init__(self, _news_service: object) -> None:
        pass

    def is_macro_risk(self) -> bool:
        return False


class RunJobFlowTests(unittest.TestCase):
    def test_run_job_chain_recovers_memory_between_steps(self) -> None:
        original_research_log = SETTINGS.paths.research_log
        original_trade_log = SETTINGS.paths.trade_log
        original_levels_log = SETTINGS.paths.levels_log
        original_weekly_log = SETTINGS.paths.weekly_log
        original_weekly_review_log = SETTINGS.paths.weekly_review_log
        original_strategy_doc = SETTINGS.paths.strategy_doc
        original_reports_dir = SETTINGS.paths.reports_dir
        original_runtime_dir = SETTINGS.paths.runtime_dir
        original_state_file = SETTINGS.paths.state_file

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_root = Path(temp_dir)
                research_log = temp_root / "RESEARCH_LOG.md"
                trade_log = temp_root / "TRADE_LOG.md"
                levels_log = temp_root / "LEVELS_LOG.md"
                weekly_log = temp_root / "WEEKLY_LOG.md"
                weekly_review_log = temp_root / "WEEKLY_REVIEW.md"
                strategy_doc = temp_root / "TRADING_STRATEGY.md"
                reports_dir = temp_root / "reports"
                runtime_dir = temp_root / "runtime"
                state_file = runtime_dir / "state.json"

                research_log.write_text("# Research Log\n", encoding="utf-8")
                trade_log.write_text("# Trade Log\n", encoding="utf-8")
                levels_log.write_text("# Levels Log\n", encoding="utf-8")
                weekly_log.write_text("# Weekly Log\n", encoding="utf-8")
                weekly_review_log.write_text("# Weekly Review Log\n", encoding="utf-8")
                strategy_doc.write_text("# Strategy\n", encoding="utf-8")
                reports_dir.mkdir(parents=True, exist_ok=True)
                runtime_dir.mkdir(parents=True, exist_ok=True)

                object.__setattr__(SETTINGS.paths, "research_log", research_log)
                object.__setattr__(SETTINGS.paths, "trade_log", trade_log)
                object.__setattr__(SETTINGS.paths, "levels_log", levels_log)
                object.__setattr__(SETTINGS.paths, "weekly_log", weekly_log)
                object.__setattr__(SETTINGS.paths, "weekly_review_log", weekly_review_log)
                object.__setattr__(SETTINGS.paths, "strategy_doc", strategy_doc)
                object.__setattr__(SETTINGS.paths, "reports_dir", reports_dir)
                object.__setattr__(SETTINGS.paths, "runtime_dir", runtime_dir)
                object.__setattr__(SETTINGS.paths, "state_file", state_file)

                call_trace: Dict[str, object] = {}
                watchlist = {"AAPL": {"levels": [{"price": 100.0}], "security_type": "STK"}}
                executed_position = {
                    "symbol": "AAPL",
                    "quantity": 100,
                    "entry": 123.45,
                    "direction": "long",
                    "sec_type": "STK",
                }

                def fake_run_premarket(
                    _market_data: object,
                    _news_service: object,
                    _news_filter: object,
                    _account_snapshot: Dict[str, object],
                    _symbols: List[str],
                ) -> Dict[str, object]:
                    append_workflow_snapshot(
                        SETTINGS.paths.research_log,
                        "Premarket",
                        {
                            "watchlist": watchlist,
                            "macro_risk": {"blocked": False},
                            "ideas": [],
                            "research_symbols": ["AAPL"],
                        },
                    )
                    call_trace["premarket_written"] = list(watchlist.keys())
                    return {"watchlist": watchlist, "macro_risk": {"blocked": False}, "ideas": [], "report_path": ""}

                def fake_run_open(
                    *,
                    market_data: object,
                    order_manager: object,
                    news_filter: object,
                    watchlist: Dict[str, object],
                    account_equity: float,
                    cash_available: float,
                    current_positions: List[Dict[str, object]],
                    open_risk_amount: float,
                ) -> List[Dict[str, object]]:
                    del market_data, order_manager, news_filter, account_equity, cash_available, current_positions, open_risk_amount
                    call_trace["open_watchlist"] = list(watchlist.keys())
                    append_workflow_snapshot(
                        SETTINGS.paths.research_log,
                        "Open",
                        {"executed": [executed_position], "skipped": [], "scans": []},
                    )
                    return [dict(executed_position)]

                def fake_run_intraday(
                    broker: object,
                    alerter: object,
                    news_filter: object,
                    tracked_positions: List[Dict[str, object]],
                    account_equity: float,
                    daily_realized_pnl: float = 0.0,
                    dry_run: bool = False,
                    *,
                    now_provider: object | None = None,
                    sleep_provider: object | None = None,
                ) -> List[Dict[str, object]]:
                    del broker, alerter, news_filter, account_equity, daily_realized_pnl, dry_run, now_provider, sleep_provider
                    call_trace["intraday_tracked_symbols"] = [position.get("symbol") for position in tracked_positions]
                    tracked_positions.append(
                        {
                            "symbol": "MSFT",
                            "quantity": 50,
                            "entry": 456.78,
                            "direction": "long",
                            "sec_type": "STK",
                        }
                    )
                    append_workflow_snapshot(
                        SETTINGS.paths.research_log,
                        "Intraday",
                        {"actions": [], "executed": [], "skipped": [], "tracked_positions": tracked_positions, "scans": []},
                    )
                    return []

                def fake_run_eod(
                    risk_manager: object,
                    account_snapshot: Dict[str, object],
                    open_positions: List[Dict[str, object]],
                    daily_pnl: float,
                    alerter: object,
                ) -> Dict[str, object]:
                    del risk_manager, account_snapshot, daily_pnl, alerter
                    call_trace["eod_open_symbols"] = [position.get("symbol") for position in open_positions]
                    return {"date": "2026-05-15", "positions": open_positions, "decision_summary": {}}

                _BrokerStub.positions_sequence = [
                    [],
                    [],
                    [_stock_position("AAPL", 100, 123.45)],
                    [_stock_position("AAPL", 100, 123.45), _stock_position("MSFT", 50, 456.78)],
                ]

                with (
                    patch("src.main.IBKRClient", _BrokerStub),
                    patch("src.main.NewsService", lambda broker=None: object()),
                    patch("src.main.NewsRiskFilter", _NewsRiskFilterStub),
                    patch("src.main.MarketDataService", lambda broker=None: object()),
                    patch("src.main.OrderManager", lambda *args, **kwargs: object()),
                    patch("src.main.maybe_commit_and_push", lambda *args, **kwargs: None),
                    patch("src.main.run_premarket", side_effect=fake_run_premarket),
                    patch("src.main.run_open", side_effect=fake_run_open),
                    patch("src.main.run_intraday", side_effect=fake_run_intraday),
                    patch("src.main.run_eod", side_effect=fake_run_eod),
                    patch("src.alerts.slack.SlackAlerter.send", return_value=True),
                    patch("src.alerts.slack.SlackAlerter.send_channel_message", return_value=True),
                    patch("src.alerts.slack.SlackAlerter.send_premarket_summary", return_value=True),
                    patch("src.alerts.slack.SlackAlerter.send_daily_summary", return_value=True),
                    patch("src.alerts.slack.SlackAlerter.upload_file", return_value=True),
                ):
                    run_job("premarket", dry_run_override=True)
                    self.assertTrue(state_file.exists())
                    state_file.unlink()

                    run_job("open", dry_run_override=True)
                    self.assertEqual(call_trace["open_watchlist"], ["AAPL"])
                    state_file.unlink()

                    run_job("intraday", dry_run_override=True)
                    self.assertEqual(call_trace["intraday_tracked_symbols"], ["AAPL"])
                    state_file.unlink()

                    run_job("eod", dry_run_override=True)
                    self.assertEqual(call_trace["eod_open_symbols"], ["AAPL", "MSFT"])
        finally:
            object.__setattr__(SETTINGS.paths, "research_log", original_research_log)
            object.__setattr__(SETTINGS.paths, "trade_log", original_trade_log)
            object.__setattr__(SETTINGS.paths, "levels_log", original_levels_log)
            object.__setattr__(SETTINGS.paths, "weekly_log", original_weekly_log)
            object.__setattr__(SETTINGS.paths, "weekly_review_log", original_weekly_review_log)
            object.__setattr__(SETTINGS.paths, "strategy_doc", original_strategy_doc)
            object.__setattr__(SETTINGS.paths, "reports_dir", original_reports_dir)
            object.__setattr__(SETTINGS.paths, "runtime_dir", original_runtime_dir)
            object.__setattr__(SETTINGS.paths, "state_file", original_state_file)


if __name__ == "__main__":
    unittest.main()
