from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

import pandas as pd

from src.config import SETTINGS
from src.main import run_job
from src.strategy.signal_models import TradeSignal


class _BrokerStub:
    def __init__(self) -> None:
        self.is_connected = False

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


class _NewsRiskFilterStub:
    def __init__(self, _news_service: object) -> None:
        pass

    def is_macro_risk(self) -> bool:
        return False

    def get_macro_risk_context(self) -> Dict[str, object]:
        return {"risk_level": "LOW", "provider_hits": [], "matched_headlines": []}

    def get_symbol_risk_context(self, symbol: str) -> Dict[str, object]:
        if symbol == "RISKY":
            return {"risk_level": "HIGH", "provider_hits": ["stub"], "matched_headlines": ["earnings"]}
        return {"risk_level": "LOW", "provider_hits": [], "matched_headlines": []}

    def has_high_risk_news(self, symbol: str) -> bool:
        return symbol == "RISKY"


class _MarketDataServiceStub:
    requests: List[Dict[str, str]] = []

    def __init__(self, _broker: object) -> None:
        self._bars = pd.DataFrame(
            [
                {"date": "2026-05-19 09:35:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000},
                {"date": "2026-05-19 09:40:00", "open": 100.5, "high": 101.5, "low": 100.0, "close": 101.0, "volume": 1200},
            ]
        )

    def get_intraday_bars(self, symbol: str, duration: str = "2 D", bar_size: str = "5 mins") -> pd.DataFrame:
        self.requests.append({"symbol": symbol, "duration": duration, "bar_size": bar_size})
        return self._bars.copy()

    def get_quote(self, symbol: str) -> Dict[str, float]:
        del symbol
        return {"bid": 100.0, "ask": 100.1, "last": 100.05, "close": 100.0}

    def market_is_open(self, current_time=None) -> bool:
        del current_time
        return True

    def unstable_open_window(self, current_time=None) -> bool:
        del current_time
        return False


class _OrderManagerStub:
    def __init__(
        self,
        broker: object,
        market_data: object,
        alerter: object,
        news_filter: object,
        dry_run: bool = False,
        alert_on_manual_candidates: bool = True,
    ) -> None:
        del broker, market_data, alerter, news_filter, alert_on_manual_candidates
        self.dry_run = dry_run

    def execute_trade(
        self,
        signal: TradeSignal,
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
        del allow_after_hours, allow_extended_hours_order, time_in_force
        if signal.symbol == "AAPL":
            return True, {
                "status": "simulated",
                "dry_run": True,
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
            }
        return False, {
            "status": "rejected",
            "reasons": ["reward_risk_too_low"],
            "signal": {"reward_risk": signal.reward_risk, "quantity": 50},
        }


class ValidateWatchlistJobTests(unittest.TestCase):
    def test_validate_watchlist_reports_placeable_and_blocked_symbols(self) -> None:
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

            state_file.write_text(
                """{
  "watchlist": {
    "AAPL": {"levels": [], "technical_atr": 1.0, "daily_atr": 2.0, "security_type": "STK"},
    "MSFT": {"levels": [], "technical_atr": 1.0, "daily_atr": 2.0, "security_type": "STK"},
    "RISKY": {"levels": [], "technical_atr": 1.0, "daily_atr": 2.0, "security_type": "STK"}
  },
  "tracked_positions": [],
  "weekly_results": []
}""",
                encoding="utf-8",
            )
            _MarketDataServiceStub.requests = []

            def fake_route_strategies(symbol: str, intraday_bars: object, levels: object, news_context: object = None):
                del intraday_bars, levels, news_context
                if symbol == "AAPL":
                    return [
                        TradeSignal(
                            symbol="AAPL",
                            strategy="false_breakout_one_bar",
                            signal="BUY",
                            direction="long",
                            entry=100.0,
                            stop=99.0,
                            target=103.0,
                            level_price=99.0,
                            level_type="historical",
                            nearest_upper_level=103.0,
                            reward_risk=3.0,
                            partial_targets=[{"qty_pct": 1.0, "price": 103.0}],
                        )
                    ]
                if symbol == "MSFT":
                    return [
                        TradeSignal(
                            symbol="MSFT",
                            strategy="false_breakout_one_bar",
                            signal="BUY",
                            direction="long",
                            entry=100.0,
                            stop=99.0,
                            target=101.0,
                            level_price=99.0,
                            level_type="historical",
                            nearest_upper_level=101.0,
                            reward_risk=1.0,
                            partial_targets=[{"qty_pct": 1.0, "price": 101.0}],
                        )
                    ]
                return []

            with (
                patch("src.main.IBKRClient", _BrokerStub),
                patch("src.main.NewsService", lambda broker=None: object()),
                patch("src.main.NewsRiskFilter", _NewsRiskFilterStub),
                patch("src.main.MarketDataService", _MarketDataServiceStub),
                patch("src.main.OrderManager", _OrderManagerStub),
                patch("src.main.route_strategies", side_effect=fake_route_strategies),
                patch("src.main.technical_atr_has_room", return_value=True),
                patch("src.main.atr_travel_filter", return_value=True),
            ):
                result = run_job("validate_watchlist", dry_run_override=True)

            self.assertEqual(result["job"], "validate_watchlist")
            self.assertTrue(result["dry_run"])
            self.assertEqual(result["watchlist_count"], 3)
            self.assertEqual(result["summary"]["placeable"], 1)
            self.assertEqual(result["summary"]["candidate_rejected"], 1)
            self.assertEqual(result["summary"]["symbol_news_blocked"], 1)
            self.assertEqual(result["placeable"][0]["symbol"], "AAPL")
            self.assertEqual(result["sample_rejections"][0]["symbol"], "MSFT")
            self.assertIn("reward_risk_too_low", result["reason_counts"])
            self.assertTrue(_MarketDataServiceStub.requests)
            self.assertEqual(_MarketDataServiceStub.requests[0]["duration"], SETTINGS.strategy.intraday_bar_duration)
            self.assertEqual(_MarketDataServiceStub.requests[0]["bar_size"], SETTINGS.strategy.intraday_bar_size)

        object.__setattr__(SETTINGS.paths, "trade_log", original_trade_log)
        object.__setattr__(SETTINGS.paths, "state_file", original_state_file)
        object.__setattr__(SETTINGS.paths, "runtime_dir", original_runtime_dir)
