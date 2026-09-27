from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading
from types import SimpleNamespace

from src.brokers.ibkr import OrderResult
from src.config import DecisionAgentConfig
from src.decision.agent import AgentRuntimeContext, AutonomousGerchikAgent
from src.decision.audit import DecisionAudit
from src.decision.candidate_adapter import trade_signal_to_candidate
from src.decision.market import quote_age_seconds
from src.decision.models import AgentMode, DecisionAction, DecisionCandidate, DecisionResponse
from src.decision.portfolio import PaperPortfolio
from src.execution.order_manager import OrderManager
from src.strategy.signal_models import TradeSignal


def _candidate(candidate_id: str = "remediation-candidate") -> DecisionCandidate:
    return DecisionCandidate(
        candidate_id=candidate_id,
        created_at=datetime.now(timezone.utc),
        asset_class="stock",
        symbol="AAPL",
        strategy="rebound",
        direction="long",
        entry=100.0,
        stop=98.0,
        target=106.0,
        level_price=99.5,
        level_type="support",
        metadata={"identity_status": "stable"},
    )


def _config(tmp_path, mode: str = AgentMode.PAPER_AUTONOMOUS.value) -> SimpleNamespace:
    return SimpleNamespace(
        mode=mode,
        agent_id="GERCHIK_LLM_REMEDIATION",
        provider="fake",
        model="fake-model",
        timeout_seconds=1.0,
        max_open_positions=5,
        risk_per_trade_pct=1.0,
        max_daily_r=0.0,
        max_data_age_seconds=30.0,
        max_entry_chase_pct=0.01,
        decision_workers=1,
        decision_queue_depth=2,
        candidate_expiry_seconds=45.0,
        shadow_dedup_minutes=5.0,
        paper_protection_interval_seconds=1.0,
        position_review_interval_seconds=300.0,
        provider_health_max_age_seconds=900.0,
        use_news=False,
        multi_provider_shadow=False,
        shadow_providers=(),
        database_path=tmp_path / "decision.db",
        allow_live_trading=False,
        live_account_allowlist=(),
        minimum_confidence=None,
        slippage_pct=0.0,
        commission_per_share=0.0,
        position_review_enabled=False,
    )


def test_sqlite_reservation_allows_exactly_one_concurrent_claim(tmp_path) -> None:
    audit = DecisionAudit(tmp_path / "audit.db")
    candidate = _candidate()
    audit.record_candidate(candidate)

    def claim():
        return audit.reserve_execution(
            mode="paper_autonomous",
            agent_id="agent",
            candidate_id=candidate.candidate_id,
            account_id=None,
            symbol=candidate.symbol,
            side="BUY",
            entry=candidate.entry,
            stop=candidate.stop,
            target=candidate.target,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: claim(), range(2)))
    assert sum(result.acquired for result in results) == 1
    assert sum(not result.acquired for result in results) == 1


def test_reservation_namespace_does_not_cross_shadow_paper_live(tmp_path) -> None:
    audit = DecisionAudit(tmp_path / "audit.db")
    candidate = _candidate()
    audit.record_candidate(candidate)
    common = {
        "agent_id": "agent",
        "candidate_id": candidate.candidate_id,
        "account_id": "U1",
        "symbol": candidate.symbol,
        "side": "BUY",
        "entry": candidate.entry,
        "stop": candidate.stop,
        "target": candidate.target,
    }
    assert audit.reserve_execution(mode="paper_autonomous", **common).acquired
    assert audit.reserve_execution(mode="live_autonomous", **common).acquired
    assert not audit.reserve_execution(mode="paper_autonomous", **common).acquired


def test_candidate_identity_has_no_clock_fallback() -> None:
    signal = TradeSignal(
        symbol="AAPL",
        strategy="rebound",
        signal="BUY",
        direction="long",
        entry=100.0,
        stop=98.0,
        target=106.0,
        level_price=99.5,
        level_type="support",
    )
    first = trade_signal_to_candidate(signal)
    second = trade_signal_to_candidate(signal)
    assert first.candidate_id == second.candidate_id
    assert first.metadata["identity_status"] == "unstable"


def test_candidate_identity_uses_source_bar_timestamp() -> None:
    signal = TradeSignal(
        symbol="AAPL",
        strategy="rebound",
        signal="BUY",
        direction="long",
        entry=100.0,
        stop=98.0,
        target=106.0,
        level_price=99.5,
        level_type="support",
        metadata={"source_bar_timestamp": "2026-09-26T13:35:00+00:00"},
    )
    first = trade_signal_to_candidate(signal)
    second = trade_signal_to_candidate(signal)
    assert first.candidate_id == second.candidate_id
    assert first.metadata["identity_status"] == "stable"


def test_missing_quote_age_is_unknown() -> None:
    assert quote_age_seconds({"last": 100.0}) is None
    assert quote_age_seconds({"last": 100.0, "quote_timestamp": datetime.now(timezone.utc).isoformat()}) is not None


def test_paper_restart_recovers_portfolio_mutation_to_execution_link(tmp_path) -> None:
    audit = DecisionAudit(tmp_path / "decision.db")
    portfolio = PaperPortfolio(tmp_path / "portfolio.json", starting_equity=10_000.0)
    candidate = _candidate()
    audit.record_candidate(candidate)
    reservation = audit.reserve_execution(
        mode="paper_autonomous",
        agent_id="agent",
        candidate_id=candidate.candidate_id,
        account_id=None,
        symbol=candidate.symbol,
        side="BUY",
        entry=candidate.entry,
        stop=candidate.stop,
        target=candidate.target,
    )
    execution = portfolio.enter(candidate, quantity=10, quote={"ask": 100.0})
    audit.update_reservation(reservation.reservation_id, state="PAPER_MUTATING", quantity=10)
    AutonomousGerchikAgent(
        config=_config(tmp_path),
        audit=audit,
        portfolio=PaperPortfolio(tmp_path / "portfolio.json", starting_equity=10_000.0),
    )
    assert audit.has_active_execution(candidate.candidate_id, mode="paper_autonomous", agent_id="agent")
    assert execution["position_id"]


def test_paper_protection_runs_without_provider(tmp_path) -> None:
    config = _config(tmp_path)
    portfolio = PaperPortfolio(tmp_path / "portfolio.json", starting_equity=10_000.0)
    portfolio.enter(_candidate(), quantity=10, quote={"ask": 100.0})
    agent = AutonomousGerchikAgent(config=config, audit=DecisionAudit(tmp_path / "decision.db"), portfolio=portfolio)

    class MarketData:
        def get_quote(self, symbol):
            return {"last": 97.0, "quote_timestamp": datetime.now(timezone.utc).isoformat()}

    closed = agent.run_paper_safety_cycle(MarketData(), now=10.0)
    assert len(closed) == 1
    assert portfolio.positions() == []


def test_order_manager_requires_fresh_autonomous_quote_and_preserves_reference() -> None:
    class Broker:
        is_connected = True

        def __init__(self):
            self.order_ref = None

        def place_market_bracket_order(self, symbol, action, quantity, stop_price, **kwargs):
            self.order_ref = kwargs.get("order_ref")
            return (
                OrderResult(1, "AAPL", "BUY", quantity, "MKT", "Submitted"),
                OrderResult(2, "AAPL", "SELL", quantity, "STP", "Submitted"),
                OrderResult(3, "AAPL", "SELL", quantity, "LMT", "Submitted"),
            )

    class MarketData:
        def get_quote(self, symbol):
            return {
                "bid": 99.9,
                "ask": 100.1,
                "last": 100.0,
                "quote_status": "ok",
                "quote_timestamp": datetime.now(timezone.utc).isoformat(),
            }

        def market_is_open(self):
            return True

        def unstable_open_window(self):
            return False

    manager = OrderManager(Broker(), MarketData(), SimpleNamespace(send_trade_executed=lambda payload: None), SimpleNamespace(has_high_risk_news=lambda symbol: False, is_macro_risk=lambda: False), dry_run=False)
    signal = TradeSignal("AAPL", "rebound", "BUY", "long", 100.0, 98.0, 106.0, 99.5, "support")
    success, payload = manager.execute_trade(
        signal,
        account_equity=50_000.0,
        cash_available=50_000.0,
        current_positions=[],
        open_risk_amount=0.0,
        autonomous_guard={
            "agent_id": "agent",
            "candidate_id": "candidate",
            "decision_id": "decision",
            "order_ref": "LLM-reference",
            "entry": 100.0,
            "max_data_age_seconds": 30.0,
            "max_entry_chase_pct": 0.01,
            "max_spread_pct": 0.003,
            "account_synced": True,
        },
    )
    assert success is True
    assert payload["market_order_id"] == 1


def test_live_configuration_requires_verified_allowlisted_account() -> None:
    config = DecisionAgentConfig(
        mode=AgentMode.LIVE_AUTONOMOUS.value,
        model="test-model",
        allow_live_trading=True,
        live_account_allowlist=("DU123",),
    )
    import pytest

    with pytest.raises(ValueError, match="verified broker account ID"):
        config.validate(paper_trading=False, account_id=None)
    with pytest.raises(ValueError, match="allowlist"):
        config.validate(paper_trading=False, account_id="DU999")
    config.validate(paper_trading=False, account_id="DU123")


def test_live_reconciliation_marks_uncertain_submission_without_retry(tmp_path) -> None:
    class Broker:
        is_connected = True

        def __init__(self):
            self.place_calls = 0

        def get_open_orders(self):
            return []

        def get_executions(self):
            return []

        def place_market_bracket_order(self, *args, **kwargs):
            self.place_calls += 1
            raise AssertionError("reconciliation must never resubmit")

        def get_account_summary(self):
            return [{"account": "DU123"}]

    audit = DecisionAudit(tmp_path / "live.db")
    candidate = _candidate("uncertain-live")
    audit.record_candidate(candidate)
    reservation = audit.reserve_execution(
        mode=AgentMode.LIVE_AUTONOMOUS.value,
        agent_id="agent",
        candidate_id=candidate.candidate_id,
        account_id="DU123",
        symbol=candidate.symbol,
        side="BUY",
        entry=candidate.entry,
        stop=candidate.stop,
        target=candidate.target,
        order_ref="LLM-uncertain",
    )
    audit.update_reservation(reservation.reservation_id, state="SUBMITTING")
    broker = Broker()
    order_manager = SimpleNamespace(broker=broker)
    config = _config(tmp_path, AgentMode.LIVE_AUTONOMOUS.value)
    config.allow_live_trading = True
    config.live_account_allowlist = ("DU123",)
    agent = AutonomousGerchikAgent(config=config, audit=audit, order_manager=order_manager)

    resolved = agent.reconcile_live_reservations()

    assert resolved[0]["state"] == "RECONCILIATION_REQUIRED"
    assert audit.reservation(reservation.reservation_id)["state"] == "RECONCILIATION_REQUIRED"
    assert broker.place_calls == 0


def test_news_is_omitted_from_autonomous_snapshot_when_disabled(tmp_path) -> None:
    config = _config(tmp_path, AgentMode.SHADOW.value)
    config.use_news = False
    agent = AutonomousGerchikAgent(config=config, provider=SimpleNamespace(provider_name="fake", model="m"), audit=DecisionAudit(tmp_path / "news.db"))
    request = agent._request(
        _candidate(),
        AgentRuntimeContext(
            current_price=100.0,
            data_age_seconds=1.0,
            market_context={"news": ["secret headline"], "news_risk": "HIGH", "technical": "ok"},
        ),
    )
    assert "news" not in request.snapshot.market_context
    assert "news_risk" not in request.snapshot.market_context
    assert request.snapshot.market_context["technical"] == "ok"


def test_audit_failure_fails_closed_before_paper_mutation(tmp_path) -> None:
    class FailingAudit(DecisionAudit):
        def record_snapshot(self, request):
            raise RuntimeError("disk full")

    portfolio = PaperPortfolio(tmp_path / "paper.json", starting_equity=10_000.0)
    audit = FailingAudit(tmp_path / "audit.db")
    agent = AutonomousGerchikAgent(
        config=_config(tmp_path),
        provider=SimpleNamespace(
            provider_name="fake",
            model="fake-model",
            decide=lambda request: DecisionResponse(
                action=DecisionAction.ENTER,
                confidence=0.9,
                reason_codes=("TEST",),
            ),
        ),
        audit=audit,
        portfolio=portfolio,
    )

    result = agent.process_candidate(_candidate("audit-failure"), context=AgentRuntimeContext(
        account_equity=10_000.0,
        cash_available=10_000.0,
        broker_connected=True,
        account_synced=True,
        market_open=True,
        data_age_seconds=1.0,
        current_price=100.0,
        quote={"bid": 99.9, "ask": 100.1},
        isolated_paper=True,
    ))

    assert result.status == "audit_error"
    assert portfolio.positions() == []
    reservation = audit.incomplete_reservations(mode=AgentMode.PAPER_AUTONOMOUS.value)
    assert reservation == []


def test_autonomous_guard_rejects_unknown_stale_and_wide_quotes() -> None:
    class Broker:
        is_connected = True

        def __init__(self):
            self.calls = 0

        def place_market_bracket_order(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("unsafe quote must be rejected before broker")

    class MarketData:
        def __init__(self, quote):
            self.quote = quote

        def get_quote(self, symbol):
            return self.quote

        def market_is_open(self):
            return True

        def unstable_open_window(self):
            return False

    import pytest

    for quote, reason in (
        ({"last": 100.0}, "quote_age_unknown"),
        ({"last": 100.0, "quote_timestamp": "2020-01-01T00:00:00+00:00"}, "quote_stale"),
        ({"bid": 90.0, "ask": 110.0, "last": 100.0, "quote_timestamp": datetime.now(timezone.utc).isoformat()}, "spread_too_wide"),
    ):
        broker = Broker()
        manager = OrderManager(broker, MarketData(quote), SimpleNamespace(send_trade_executed=lambda payload: None), SimpleNamespace(has_high_risk_news=lambda symbol: False, is_macro_risk=lambda: False), dry_run=False)
        signal = TradeSignal("AAPL", "rebound", "BUY", "long", 100.0, 98.0, 106.0, 99.5, "support")
        success, payload = manager.execute_trade(
            signal,
            account_equity=50_000.0,
            cash_available=50_000.0,
            current_positions=[],
            open_risk_amount=0.0,
            autonomous_guard={"entry": 100.0, "max_data_age_seconds": 30.0, "max_entry_chase_pct": 0.01, "max_spread_pct": 0.003, "account_synced": True},
        )
        assert success is False
        assert reason in payload["reasons"]
        assert broker.calls == 0


def test_decision_queue_is_bounded_and_shadow_deduplicates_pending_work(tmp_path) -> None:
    class SlowProvider:
        provider_name = "fake"
        model = "fake-model"

        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def decide(self, request):
            self.started.set()
            self.release.wait(timeout=3.0)
            return DecisionResponse(
                action=DecisionAction.WAIT,
                confidence=0.9,
                reason_codes=("TEST",),
            )

    config = _config(tmp_path, AgentMode.SHADOW.value)
    config.decision_workers = 1
    config.decision_queue_depth = 1
    provider = SlowProvider()
    agent = AutonomousGerchikAgent(config=config, provider=provider, audit=DecisionAudit(tmp_path / "queue.db"))
    context = AgentRuntimeContext(current_price=100.0, data_age_seconds=1.0, quote={"last": 100.0})
    signal = TradeSignal("AAPL", "rebound", "BUY", "long", 100.0, 98.0, 106.0, 99.5, "support")

    first = agent.submit_signal(signal, context=context, source_signal_id="source-a")
    assert first.status == "scheduled"
    assert provider.started.wait(timeout=1.0)
    second = agent.submit_signal(signal, context=context, source_signal_id="source-b")
    duplicate = agent.submit_signal(signal, context=context, source_signal_id="source-b")
    third = agent.submit_signal(signal, context=context, source_signal_id="source-c")

    assert second.status == "scheduled"
    assert duplicate.status == "deduplicated"
    assert third.status == "expired"
    assert "decision_queue_full" in third.reasons
    provider.release.set()
    results = agent.wait_for_shadow(timeout=3.0)
    assert len(results) == 2
    assert all(result.status == "shadow" for result in results)
