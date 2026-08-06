from __future__ import annotations

from datetime import datetime
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.jobs.intraday import run_intraday
from src.jobs.session_utils import sync_tracked_positions_with_broker


TZ = ZoneInfo("America/New_York")


class IntradaySyncTests(TestCase):
    def test_sync_tracked_positions_rehydrates_live_positions_and_stop_orders(self) -> None:
        tracked_positions = []
        broker_positions = [
            {"symbol": "SEI", "position": -100.0, "avg_cost": 78.18, "sec_type": "STK"},
            {"symbol": "EUR", "position": 10000.0, "avg_cost": 1.16, "sec_type": "CASH"},
        ]
        open_orders = [
            {"symbol": "SEI", "order_id": 456, "type": "STP"},
            {"symbol": "SEI", "order_id": 789, "type": "LMT"},
        ]

        synced = sync_tracked_positions_with_broker(tracked_positions, broker_positions, open_orders)

        self.assertEqual(
            synced,
            [
                {
                    "symbol": "SEI",
                    "quantity": 100,
                    "entry": 78.18,
                    "avg_cost": 78.18,
                    "direction": "short",
                    "sec_type": "STK",
                    "protection_policy": "manual_unmanaged",
                    "stop_order_id": 456,
                    "current_stop_order_status": None,
                    "limit_order_id": 789,
                    "current_target_order_status": None,
                }
            ],
        )

    def test_intraday_refreshes_tracked_positions_from_broker_before_management(self) -> None:
        class BrokerStub:
            def get_positions(self):
                return [{"symbol": "SEI", "position": -100.0, "avg_cost": 78.18, "sec_type": "STK"}]

            def get_open_orders(self):
                return [{"symbol": "SEI", "order_id": 456, "type": "STP", "status": "Submitted"}]

        class MarketDataStub:
            def __init__(self) -> None:
                self.calls = 0

            def market_is_open(self, _moment: datetime) -> bool:
                self.calls += 1
                return self.calls <= 2

        class AlerterStub:
            def send_intraday_heartbeat(self, _payload):
                return True

        class NewsFilterStub:
            def is_macro_risk(self) -> bool:
                return False

        captured_symbols: list[str] = []
        captured_stop_ids: list[int] = []
        market_data_stub = MarketDataStub()

        def fake_manage_positions(*, tracked_positions, **kwargs):
            del kwargs
            captured_symbols.extend(str(position.get("symbol")) for position in tracked_positions)
            captured_stop_ids.extend(int(position.get("stop_order_id", 0) or 0) for position in tracked_positions)
            return {"actions": [], "macro_risk": False, "kill_switch": False, "reasons": []}

        with (
            patch("src.jobs.intraday.build_job_dependencies", return_value=(market_data_stub, object())),
            patch("src.jobs.intraday.load_runtime_state", return_value={"watchlist": {}, "tracked_positions": []}),
            patch("src.jobs.intraday.manage_positions", side_effect=fake_manage_positions),
            patch("src.jobs.intraday.persist_tracked_positions", return_value=None),
            patch("src.jobs.intraday.append_workflow_snapshot", return_value=None),
            patch("src.jobs.intraday.append_markdown_log", return_value=None),
        ):
            actions = run_intraday(
                BrokerStub(),
                AlerterStub(),
                NewsFilterStub(),
                [],
                account_equity=100_000.0,
                dry_run=True,
                now_provider=lambda: datetime(2026, 5, 18, 12, 0, tzinfo=TZ),
                sleep_provider=lambda _seconds: None,
            )

        self.assertEqual(actions, [])
        self.assertEqual(captured_symbols, ["SEI"])
        self.assertEqual(captured_stop_ids, [456])

    def test_intraday_kill_switch_mode_continues_collecting_without_trading(self) -> None:
        class BrokerStub:
            def get_positions(self):
                raise AssertionError("Data-only mode must not synchronize positions.")

            def get_open_orders(self):
                raise AssertionError("Data-only mode must not inspect orders.")

        class MarketDataStub:
            def __init__(self) -> None:
                self.calls = 0

            def market_is_open(self, _moment):
                self.calls += 1
                return self.calls <= 2

        class AlerterStub:
            def send_intraday_heartbeat(self, _payload):
                return True

        class NewsFilterStub:
            def is_macro_risk(self):
                return False

        collection_calls = []
        market_data = MarketDataStub()
        with (
            patch("src.jobs.intraday.build_job_dependencies", return_value=(market_data, object())),
            patch("src.jobs.intraday.load_runtime_state", return_value={"watchlist": {"AAPL": {}}, "tracked_positions": []}),
            patch(
                "src.jobs.intraday.collect_watchlist_intraday_bars",
                side_effect=lambda _market_data, watchlist: collection_calls.append(list(watchlist)) or {
                    "bars_by_symbol": {},
                    "symbols_requested": 1,
                    "symbols_persisted": 1,
                    "failed": [],
                },
            ),
            patch("src.jobs.intraday.manage_positions", side_effect=AssertionError("Trading management must remain disabled.")),
            patch("src.jobs.intraday.append_workflow_snapshot", return_value=None),
            patch("src.jobs.intraday.append_markdown_log", return_value=None),
            patch("src.jobs.intraday.sleep_until", return_value=None),
        ):
            actions = run_intraday(
                BrokerStub(),
                AlerterStub(),
                NewsFilterStub(),
                [],
                account_equity=100_000.0,
                dry_run=True,
                now_provider=lambda: datetime(2026, 6, 22, 11, 0, tzinfo=TZ),
                sleep_provider=lambda _seconds: None,
                initial_trading_halt_reasons=["account_not_synced"],
            )

        self.assertEqual(actions, [])
        self.assertEqual(collection_calls, [["AAPL"]])
