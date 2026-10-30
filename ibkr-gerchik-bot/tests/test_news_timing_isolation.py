from __future__ import annotations

import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from src.brokers.ibkr import OrderResult
from src.decision.agent import AgentResult
from src.decision.models import AgentMode
from src.jobs.session_utils import run_entry_scan
from src.strategy.signal_models import TradeSignal


def _signal(symbol: str = "AAPL") -> TradeSignal:
    return TradeSignal(
        symbol=symbol,
        strategy="false_breakout_one_bar",
        signal="BUY",
        direction="long",
        entry=100.0,
        stop=98.0,
        target=106.0,
        level_price=99.0,
        level_type="support",
        nearest_upper_level=106.0,
        nearest_lower_level=98.0,
        reward_risk=3.0,
    )


class _Broker:
    is_connected = True
    account_id = None


class _MarketData:
    def get_quote(self, symbol):
        del symbol
        return {
            "bid": 99.9,
            "ask": 100.1,
            "last": 100.0,
            "quote_timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def market_is_open(self, current_time=None):
        del current_time
        return True

    def unstable_open_window(self, current_time=None):
        del current_time
        return False


class _OrderManager:
    def __init__(self):
        self.broker = _Broker()
        self.market_data = _MarketData()
        self.execute_calls = 0

    def execute_trade(self, *args, **kwargs):
        del args, kwargs
        self.execute_calls += 1
        return False, {"status": "rejected", "reasons": ["test_legacy_path"]}


class _DecisionAgent:
    def __init__(self, mode: AgentMode):
        self.mode = mode
        self.config = SimpleNamespace(use_news=False)
        self.submit_calls = 0
        self.contexts = []
        self.submitted = threading.Event()

    def submit_signal(self, signal, *, context):
        del signal
        self.contexts.append(context)
        self.submit_calls += 1
        self.submitted.set()
        return AgentResult(status="scheduled", action="PENDING")


class _News:
    def __init__(self, *, macro="LOW", symbol="LOW", macro_gate=None):
        self.macro = macro
        self.symbol = symbol
        self.macro_gate = macro_gate
        self.calls = []

    def get_macro_risk_context(self):
        self.calls.append("macro")
        if self.macro_gate is not None:
            self.macro_gate.wait(timeout=3.0)
        if isinstance(self.macro, BaseException):
            raise self.macro
        return {"risk_level": self.macro, "provider_hits": ["test"]}

    def get_symbol_risk_context(self, symbol):
        self.calls.append(f"symbol:{symbol}")
        if isinstance(self.symbol, BaseException):
            raise self.symbol
        return {"risk_level": self.symbol, "provider_hits": ["test"]}


def _run(agent, order_manager, news, symbols=("AAPL",)):
    watchlist = {
        symbol: {
            "chart_history": {"ready": True, "missing": []},
            "technical_atr": 4.0,
            "daily_atr": 20.0,
            "levels": [],
        }
        for symbol in symbols
    }
    bars = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-09-27T14:00:00Z"),
                "low": 99.0,
                "high": 101.0,
                "close": 100.0,
            }
        ]
    )
    result = run_entry_scan(
        stage_name="Test",
        market_data=_MarketData(),
        order_manager=order_manager,
        news_filter=news,
        watchlist=watchlist,
        account_equity=50_000.0,
        cash_available=50_000.0,
        current_positions=[],
        open_risk_amount=0.0,
        scan_time=datetime(2026, 9, 27, 14, 5, tzinfo=timezone.utc),
        intraday_bars_by_symbol={symbol: bars for symbol in symbols},
        decision_agent=agent,
    )
    return result


def test_paper_ai_path_does_not_call_news_when_disabled() -> None:
    agent = _DecisionAgent(AgentMode.PAPER_AUTONOMOUS)
    order_manager = _OrderManager()
    news = _News(macro=RuntimeError("news service must not be called"), symbol=RuntimeError("news service must not be called"))

    with patch("src.jobs.session_utils.route_strategies", return_value=[_signal()]), patch(
        "src.jobs.session_utils.technical_atr_has_room", return_value=True
    ), patch("src.jobs.session_utils.atr_travel_filter", return_value=True):
        result = _run(agent, order_manager, news)

    assert agent.submit_calls == 1
    assert news.calls == []
    assert order_manager.execute_calls == 0
    assert result["skipped"][0]["source"] == "LLM_AGENT"


def test_shadow_ai_candidate_is_submitted_before_blocking_legacy_news() -> None:
    release_news = threading.Event()
    agent = _DecisionAgent(AgentMode.SHADOW)
    order_manager = _OrderManager()
    news = _News(macro="LOW", symbol="LOW", macro_gate=release_news)
    captured = {}

    def run():
        captured["result"] = _run(agent, order_manager, news)

    worker = threading.Thread(target=run)
    with patch("src.jobs.session_utils.route_strategies", return_value=[_signal()]), patch(
        "src.jobs.session_utils.technical_atr_has_room", return_value=True
    ), patch("src.jobs.session_utils.atr_travel_filter", return_value=True):
        worker.start()
        assert agent.submitted.wait(timeout=1.0)
        assert news.calls == ["macro"]
        release_news.set()
        worker.join(timeout=3.0)

    assert not worker.is_alive()
    assert agent.submit_calls == 1
    assert news.calls == ["macro", "symbol:AAPL"]
    assert order_manager.execute_calls == 1


def test_shadow_high_news_does_not_reject_ai_candidate() -> None:
    agent = _DecisionAgent(AgentMode.SHADOW)
    order_manager = _OrderManager()
    news = _News(macro="HIGH")

    with patch("src.jobs.session_utils.route_strategies", return_value=[_signal()]), patch(
        "src.jobs.session_utils.technical_atr_has_room", return_value=True
    ), patch("src.jobs.session_utils.atr_travel_filter", return_value=True):
        _run(agent, order_manager, news)

    assert agent.submit_calls == 1
    assert order_manager.execute_calls == 0
    assert news.calls == ["macro"]


def test_shadow_news_exception_does_not_reject_ai_candidate() -> None:
    agent = _DecisionAgent(AgentMode.SHADOW)
    order_manager = _OrderManager()
    news = _News(macro=RuntimeError("provider timeout"), symbol=RuntimeError("provider timeout"))

    with patch("src.jobs.session_utils.route_strategies", return_value=[_signal()]), patch(
        "src.jobs.session_utils.technical_atr_has_room", return_value=True
    ), patch("src.jobs.session_utils.atr_travel_filter", return_value=True):
        _run(agent, order_manager, news)

    assert agent.submit_calls == 1
    assert news.calls == ["macro", "symbol:AAPL"]


def test_shadow_legacy_news_does_not_leak_into_later_ai_context() -> None:
    agent = _DecisionAgent(AgentMode.SHADOW)
    order_manager = _OrderManager()
    news = _News(macro="HIGH", symbol="HIGH")

    with patch(
        "src.jobs.session_utils.route_strategies",
        side_effect=lambda symbol, *args, **kwargs: [_signal(symbol)],
    ), patch("src.jobs.session_utils.technical_atr_has_room", return_value=True), patch(
        "src.jobs.session_utils.atr_travel_filter", return_value=True
    ):
        _run(agent, order_manager, news, symbols=("AAPL", "MSFT"))

    assert agent.submit_calls == 2
    assert [context.market_context["news_risk"] for context in agent.contexts] == ["DISABLED", "DISABLED"]
    assert [context.market_context["macro_risk"] for context in agent.contexts] == ["DISABLED", "DISABLED"]
    assert news.calls == ["macro"]


def test_off_mode_keeps_legacy_macro_news_gate() -> None:
    agent = _DecisionAgent(AgentMode.OFF)
    order_manager = _OrderManager()
    news = _News(macro="HIGH")

    with patch("src.jobs.session_utils.route_strategies") as route:
        result = _run(agent, order_manager, news)

    route.assert_not_called()
    assert news.calls == ["macro"]
    assert order_manager.execute_calls == 0
    assert result["skipped"][0]["reason"] == "macro_risk"
