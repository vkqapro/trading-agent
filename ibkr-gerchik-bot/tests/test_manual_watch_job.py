from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

from src.config import SETTINGS
from src.main import run_manual_watch


class _BrokerStub:
    def __init__(self) -> None:
        self.is_connected = False
        self.cancelled_order_ids: List[int] = []

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
        return []

    def get_open_orders(self) -> List[Dict[str, object]]:
        return []

    def cancel_order(self, order_id: int) -> bool:
        self.cancelled_order_ids.append(order_id)
        return True


class _OrderManagerStub:
    last_dry_run: bool | None = None
    last_allow_after_hours: bool | None = None
    should_simulate_live: bool = False

    def __init__(self, broker: object, market_data: object, alerter: object, news_filter: object, dry_run: bool = False) -> None:
        del broker, market_data, alerter, news_filter
        type(self).last_dry_run = dry_run

    def execute_trade(
        self,
        signal: object,
        account_equity: float,
        cash_available: float,
        current_positions: List[Dict[str, object]],
        open_risk_amount: float,
        *,
        allow_after_hours: bool = False,
        allow_extended_hours_order: bool = False,
        time_in_force: str | None = None,
    ) -> tuple[bool, Dict[str, object]]:
        del account_equity, cash_available, current_positions, open_risk_amount
        del allow_extended_hours_order, time_in_force
        type(self).last_allow_after_hours = allow_after_hours
        dry_run = not self.should_simulate_live
        return True, {
            "status": "simulated" if dry_run else "executed",
            "dry_run": dry_run,
            "symbol": signal.symbol,
            "strategy": signal.strategy,
            "level_type": signal.level_type,
            "direction": signal.direction,
            "signal": signal.signal,
            "entry": signal.entry,
            "stop_loss": signal.stop,
            "target": signal.target,
            "reward_risk": signal.reward_risk,
            "partial_targets": signal.partial_targets,
            "quantity": 100,
            "market_order_id": 0 if dry_run else 101,
            "stop_order_id": 0 if dry_run else 202,
            "limit_order_id": 0 if dry_run else 303,
        }


class ManualWatchJobTests(unittest.TestCase):
    def test_manual_watch_defaults_to_simulation(self) -> None:
        original_trade_log = SETTINGS.paths.trade_log
        original_state_file = SETTINGS.paths.state_file
        original_runtime_dir = SETTINGS.paths.runtime_dir
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            trade_log = temp_root / "TRADE_LOG.md"
            runtime_dir = temp_root / "runtime"
            state_file = runtime_dir / "state.json"
            trade_log.write_text("# Trade Log\n", encoding="utf-8")
            runtime_dir.mkdir(parents=True, exist_ok=True)
            object.__setattr__(SETTINGS.paths, "trade_log", trade_log)
            object.__setattr__(SETTINGS.paths, "runtime_dir", runtime_dir)
            object.__setattr__(SETTINGS.paths, "state_file", state_file)

            _OrderManagerStub.should_simulate_live = False
            with (
                patch("src.main.IBKRClient", _BrokerStub),
                patch("src.main.NewsService", lambda broker=None: object()),
                patch("src.main.NewsRiskFilter", lambda news_service=None: object()),
                patch("src.main.MarketDataService", lambda broker=None: object()),
                patch("src.main.OrderManager", _OrderManagerStub),
            ):
                result = run_manual_watch(
                    symbol="SANM",
                    entry=241.97,
                    stop=237.09,
                    target=255.22,
                )

            self.assertTrue(result["success"])
            self.assertTrue(result["dry_run"])
            self.assertTrue(result["result"]["dry_run"])
            self.assertEqual(_OrderManagerStub.last_dry_run, True)

        object.__setattr__(SETTINGS.paths, "trade_log", original_trade_log)
        object.__setattr__(SETTINGS.paths, "state_file", original_state_file)
        object.__setattr__(SETTINGS.paths, "runtime_dir", original_runtime_dir)

    def test_manual_watch_execute_persists_tracked_position(self) -> None:
        original_trade_log = SETTINGS.paths.trade_log
        original_state_file = SETTINGS.paths.state_file
        original_runtime_dir = SETTINGS.paths.runtime_dir
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            trade_log = temp_root / "TRADE_LOG.md"
            runtime_dir = temp_root / "runtime"
            state_file = runtime_dir / "state.json"
            trade_log.write_text("# Trade Log\n", encoding="utf-8")
            runtime_dir.mkdir(parents=True, exist_ok=True)
            object.__setattr__(SETTINGS.paths, "trade_log", trade_log)
            object.__setattr__(SETTINGS.paths, "runtime_dir", runtime_dir)
            object.__setattr__(SETTINGS.paths, "state_file", state_file)

            _OrderManagerStub.should_simulate_live = True
            with (
                patch("src.main.IBKRClient", _BrokerStub),
                patch("src.main.NewsService", lambda broker=None: object()),
                patch("src.main.NewsRiskFilter", lambda news_service=None: object()),
                patch("src.main.MarketDataService", lambda broker=None: object()),
                patch("src.main.OrderManager", _OrderManagerStub),
            ):
                result = run_manual_watch(
                    symbol="AAPL",
                    entry=100.0,
                    stop=99.0,
                    target=103.0,
                    execute=True,
                )

            self.assertTrue(result["success"])
            self.assertFalse(result["dry_run"])
            self.assertFalse(result["result"]["dry_run"])
            self.assertEqual(_OrderManagerStub.last_dry_run, False)
            self.assertTrue(state_file.exists())
            saved_state = state_file.read_text(encoding="utf-8")
            self.assertIn("AAPL", saved_state)

        object.__setattr__(SETTINGS.paths, "trade_log", original_trade_log)
        object.__setattr__(SETTINGS.paths, "state_file", original_state_file)
        object.__setattr__(SETTINGS.paths, "runtime_dir", original_runtime_dir)

    def test_manual_watch_after_hours_auto_cancel_cleans_up_state(self) -> None:
        original_trade_log = SETTINGS.paths.trade_log
        original_state_file = SETTINGS.paths.state_file
        original_runtime_dir = SETTINGS.paths.runtime_dir
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            trade_log = temp_root / "TRADE_LOG.md"
            runtime_dir = temp_root / "runtime"
            state_file = runtime_dir / "state.json"
            trade_log.write_text("# Trade Log\n", encoding="utf-8")
            runtime_dir.mkdir(parents=True, exist_ok=True)
            object.__setattr__(SETTINGS.paths, "trade_log", trade_log)
            object.__setattr__(SETTINGS.paths, "runtime_dir", runtime_dir)
            object.__setattr__(SETTINGS.paths, "state_file", state_file)

            broker = _BrokerStub()
            _OrderManagerStub.should_simulate_live = True
            with (
                patch("src.main.IBKRClient", lambda: broker),
                patch("src.main.NewsService", lambda broker=None: object()),
                patch("src.main.NewsRiskFilter", lambda news_service=None: object()),
                patch("src.main.MarketDataService", lambda broker=None: object()),
                patch("src.main.OrderManager", _OrderManagerStub),
                patch("src.main.time.sleep", lambda seconds: None),
            ):
                result = run_manual_watch(
                    symbol="KO",
                    entry=80.0,
                    stop=76.0,
                    target=92.0,
                    execute=True,
                    allow_after_hours=True,
                    auto_cancel_seconds=1,
                )

            self.assertTrue(result["success"])
            self.assertEqual(_OrderManagerStub.last_allow_after_hours, True)
            self.assertEqual(broker.cancelled_order_ids, [303, 202, 101])
            self.assertIn("auto_cancel", result["result"])
            saved_state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertFalse(any(item.get("symbol") == "KO" for item in saved_state.get("tracked_positions", [])))

        object.__setattr__(SETTINGS.paths, "trade_log", original_trade_log)
        object.__setattr__(SETTINGS.paths, "state_file", original_state_file)
        object.__setattr__(SETTINGS.paths, "runtime_dir", original_runtime_dir)


if __name__ == "__main__":
    unittest.main()
