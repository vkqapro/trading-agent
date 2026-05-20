from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from src.alerts.slack import SlackAlerter
from src.config import SETTINGS
from src.jobs import eod as eod_module
from src.jobs.session_utils import load_runtime_state
from src.main import _hydrate_from_logs
from src.risk.risk_manager import RiskManager
from src.workflow_log import append_workflow_snapshot


class MemoryFlowTests(unittest.TestCase):
    def test_hydrate_from_logs_recovers_watchlist_and_prefers_intraday_positions(self) -> None:
        original_research_log = SETTINGS.paths.research_log
        with tempfile.TemporaryDirectory() as temp_dir:
            research_log = Path(temp_dir) / "RESEARCH_LOG.md"
            research_log.write_text("# Research Log\n", encoding="utf-8")
            object.__setattr__(SETTINGS.paths, "research_log", research_log)

            append_workflow_snapshot(
                research_log,
                "Premarket",
                {"watchlist": {"AAPL": {"levels": [{"price": 100.0}]}}},
            )
            append_workflow_snapshot(
                research_log,
                "Open",
                {"executed": [{"symbol": "AAPL", "quantity": 100}]},
            )
            append_workflow_snapshot(
                research_log,
                "Intraday",
                {"tracked_positions": [{"symbol": "MSFT", "quantity": 50}]},
            )

            hydrated = _hydrate_from_logs({"watchlist": {}, "tracked_positions": [], "weekly_results": []})

            self.assertEqual(list(hydrated["watchlist"].keys()), ["AAPL"])
            self.assertEqual(hydrated["tracked_positions"], [{"symbol": "MSFT", "quantity": 50}])

        object.__setattr__(SETTINGS.paths, "research_log", original_research_log)

    def test_hydrate_from_logs_falls_back_to_open_executions_when_intraday_missing(self) -> None:
        original_research_log = SETTINGS.paths.research_log
        with tempfile.TemporaryDirectory() as temp_dir:
            research_log = Path(temp_dir) / "RESEARCH_LOG.md"
            research_log.write_text("# Research Log\n", encoding="utf-8")
            object.__setattr__(SETTINGS.paths, "research_log", research_log)

            append_workflow_snapshot(
                research_log,
                "Premarket",
                {"watchlist": {"TSLA": {"levels": [{"price": 420.0}]}}},
            )
            append_workflow_snapshot(
                research_log,
                "Open",
                {"executed": [{"symbol": "TSLA", "quantity": 100, "entry": 425.69}]},
            )

            hydrated = _hydrate_from_logs({"watchlist": {}, "tracked_positions": [], "weekly_results": []})

            self.assertEqual(list(hydrated["watchlist"].keys()), ["TSLA"])
            self.assertEqual(hydrated["tracked_positions"][0]["symbol"], "TSLA")

        object.__setattr__(SETTINGS.paths, "research_log", original_research_log)

    def test_load_runtime_state_prefers_latest_premarket_watchlist_over_stale_state(self) -> None:
        original_research_log = SETTINGS.paths.research_log
        original_state_file = SETTINGS.paths.state_file
        with tempfile.TemporaryDirectory() as temp_dir:
            research_log = Path(temp_dir) / "RESEARCH_LOG.md"
            state_file = Path(temp_dir) / "state.json"
            research_log.write_text("# Research Log\n", encoding="utf-8")
            state_file.write_text(
                json.dumps({"watchlist": {"SEI": {"levels": [{"price": 79.19}]}}, "tracked_positions": []}),
                encoding="utf-8",
            )
            object.__setattr__(SETTINGS.paths, "research_log", research_log)
            object.__setattr__(SETTINGS.paths, "state_file", state_file)

            append_workflow_snapshot(
                research_log,
                "Premarket",
                {"watchlist": {"SEI": {"levels": [{"price": 71.05}]}}},
            )

            loaded = load_runtime_state()

            self.assertEqual(loaded["watchlist"]["SEI"]["levels"][0]["price"], 71.05)

        object.__setattr__(SETTINGS.paths, "research_log", original_research_log)
        object.__setattr__(SETTINGS.paths, "state_file", original_state_file)

    def test_eod_uses_workflow_fallback_when_daily_decisions_are_empty(self) -> None:
        original_research_log = SETTINGS.paths.research_log
        original_trade_log = SETTINGS.paths.trade_log
        with tempfile.TemporaryDirectory() as temp_dir:
            research_log = Path(temp_dir) / "RESEARCH_LOG.md"
            trade_log = Path(temp_dir) / "TRADE_LOG.md"
            runtime_log = Path(temp_dir) / "application.log"
            daily_decisions = Path(temp_dir) / "daily_decisions.json"
            research_log.write_text("# Research Log\n", encoding="utf-8")
            trade_log.write_text("# Trade Log\n", encoding="utf-8")
            runtime_log.write_text("", encoding="utf-8")
            daily_decisions.write_text(json.dumps({"date": "2000-01-01", "decisions": {}}), encoding="utf-8")

            object.__setattr__(SETTINGS.paths, "research_log", research_log)
            object.__setattr__(SETTINGS.paths, "trade_log", trade_log)

            today = datetime.now().date().isoformat()
            append_workflow_snapshot(
                research_log,
                "Open",
                {
                    "timestamp": f"{today}T09:36:00",
                    "executed": [{"symbol": "TSLA", "quantity": 100, "entry": 425.69}],
                    "skipped": [{"symbol": "AAPL", "reason": "no_signal"}],
                    "scans": [{"timestamp": f"{today}T09:36:00", "symbols_scanned": 18, "signals_detected": 2}],
                },
            )
            runtime_log.write_text(
                f"{today} 10:35:02 | INFO | ibkr-gerchik-bot | Job completed: "
                "{'job': 'intraday', 'blocked': True, 'reasons': ['session_already_running'], 'dry_run': False}\n",
                encoding="utf-8",
            )

            with (
                patch.object(eod_module, "DAILY_DECISIONS_PATH", daily_decisions),
                patch.object(eod_module, "RUNTIME_LOG_PATH", runtime_log),
            ):
                summary = eod_module.run_eod(
                    RiskManager(100_000.0),
                    account_snapshot={"account": [], "positions": [], "open_orders": []},
                    open_positions=[{"symbol": "TSLA", "quantity": 100}],
                    daily_pnl=0.0,
                    alerter=SlackAlerter(),
                )

            decision_summary = summary["decision_summary"]
            self.assertEqual(decision_summary["source"], "workflow_fallback")
            self.assertEqual(decision_summary["total_tickers_scanned"], 18)
            self.assertEqual(decision_summary["trades_taken"], 1)
            self.assertEqual(decision_summary["skipped"], 1)
            self.assertIn("workflow snapshots", summary["report_text"])
            self.assertTrue(any("intraday blocked" in note for note in decision_summary["status_notes"]))

        object.__setattr__(SETTINGS.paths, "research_log", original_research_log)
        object.__setattr__(SETTINGS.paths, "trade_log", original_trade_log)


if __name__ == "__main__":
    unittest.main()
