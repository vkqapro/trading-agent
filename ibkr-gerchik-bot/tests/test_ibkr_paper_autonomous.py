from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from src.config import DecisionAgentConfig, SETTINGS
from src.decision.agent import AgentRuntimeContext, AutonomousGerchikAgent
from src.decision.audit import DecisionAudit
from src.decision.models import AgentMode, DecisionAction, DecisionCandidate, DecisionResponse
from src.strategy.signal_models import TradeSignal


def candidate(candidate_id: str = "ibkr-paper-candidate") -> DecisionCandidate:
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
        reward_risk=3.0,
        metadata={"identity_status": "stable"},
    )


def signal() -> TradeSignal:
    return TradeSignal(
        symbol="AAPL", strategy="rebound", signal="BUY", direction="long",
        entry=100.0, stop=98.0, target=106.0, level_price=99.5,
        level_type="support", reward_risk=3.0,
    )


def context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        account_equity=50_000.0,
        cash_available=50_000.0,
        spread_pct=0.0005,
        broker_connected=True,
        account_synced=True,
        market_open=True,
        data_age_seconds=1.0,
        current_price=100.0,
        quote={"bid": 99.9, "ask": 100.1, "last": 100.0, "quote_status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()},
        market_context={"symbol": "AAPL"},
    )


class Provider:
    provider_name = "fake"
    model = "fake-model"

    def __init__(self, action: DecisionAction = DecisionAction.ENTER):
        self.action = action
        self.calls = 0

    def decide(self, request):
        self.calls += 1
        action = DecisionAction.WAIT if request.snapshot.candidate.metadata.get("non_trading") else self.action
        return DecisionResponse(
            action=action,
            confidence=0.9,
            ranked_actions=((action.value, 0.9),),
            reason_codes=("TEST",),
            summary="test",
        )


class Broker:
    is_connected = True

    def __init__(self, account_id="DU123456"):
        self.account_id = account_id
        self.identity_calls = 0

    def get_account_identity(self):
        self.identity_calls += 1
        return {
            "account_id": self.account_id,
            "accounts": [self.account_id],
            "environment": "paper" if self.account_id.startswith("DU") else "live",
            "paper_verified": self.account_id.startswith("DU"),
            "evidence": "test",
        }

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []

    def get_executions(self):
        return []


class OrderManager:
    def __init__(self, broker):
        self.broker = broker
        self.market_data = SimpleNamespace(get_quote=lambda symbol: context().quote)
        self.calls = 0

    def execute_trade(self, *args, **kwargs):
        self.calls += 1
        return True, {"status": "executed", "market_order_id": 101, "stop_order_id": 102, "limit_order_id": 103}


def config(tmp_path, **updates):
    values = dict(
        mode=AgentMode.IBKR_PAPER_AUTONOMOUS.value,
        agent_id="GERCHIK_LLM_TEST",
        provider="fake",
        model="fake-model",
        timeout_seconds=1.0,
        max_open_positions=5,
        risk_per_trade_pct=1.0,
        max_daily_r=0.0,
        max_data_age_seconds=30.0,
        max_entry_chase_pct=0.01,
        minimum_confidence=None,
        database_path=tmp_path / "decision_lab.db",
        slippage_pct=0.0,
        commission_per_share=0.0,
        position_review_enabled=False,
        multi_provider_shadow=False,
        use_news=False,
        allow_live_trading=False,
        live_account_allowlist=(),
        allow_ibkr_paper_trading=True,
        ibkr_paper_account_allowlist=("DU123456",),
        ibkr_paper_max_open_positions=1,
        ibkr_paper_max_trades_per_day=3,
        ibkr_paper_risk_per_trade_pct=0.10,
        local_model="",
        provider_health_max_age_seconds=900.0,
        candidate_expiry_seconds=45.0,
        decision_workers=1,
        decision_queue_depth=1,
    )
    values.update(updates)
    return SimpleNamespace(**values)


@pytest.fixture
def paper_runtime():
    old_paper = SETTINGS.paper_trading
    old_dry_run = SETTINGS.dry_run_mode
    object.__setattr__(SETTINGS, "paper_trading", True)
    object.__setattr__(SETTINGS, "dry_run_mode", False)
    yield
    object.__setattr__(SETTINGS, "paper_trading", old_paper)
    object.__setattr__(SETTINGS, "dry_run_mode", old_dry_run)


def test_mode_parses_and_config_requires_verified_allowlisted_paper_account(tmp_path):
    assert AgentMode.from_value("ibkr_paper_autonomous") is AgentMode.IBKR_PAPER_AUTONOMOUS
    base = DecisionAgentConfig(
        mode=AgentMode.IBKR_PAPER_AUTONOMOUS.value,
        model="fake-model",
        allow_ibkr_paper_trading=True,
        ibkr_paper_account_allowlist=("DU123456",),
    )
    with pytest.raises(ValueError, match="PAPER_TRADING"):
        base.validate(paper_trading=False, dry_run=False, account_id="DU123456", paper_account_verified=True)
    with pytest.raises(ValueError, match="DRY_RUN_MODE"):
        base.validate(paper_trading=True, dry_run=True, account_id="DU123456", paper_account_verified=True)
    with pytest.raises(ValueError, match="verified"):
        base.validate(paper_trading=True, dry_run=False, account_id="DU123456", paper_account_verified=False)
    with pytest.raises(ValueError, match="allowlist"):
        base.validate(paper_trading=True, dry_run=False, account_id="DU999999", paper_account_verified=True)
    base.validate(paper_trading=True, dry_run=False, account_id="DU123456", paper_account_verified=True)


def test_reservation_namespace_is_atomic_and_mode_scoped(tmp_path):
    audit = DecisionAudit(tmp_path / "audit.db")
    item = candidate("reservation")
    audit.record_candidate(item)

    def reserve():
        return audit.reserve_execution(
            mode=AgentMode.IBKR_PAPER_AUTONOMOUS.value,
            agent_id="GERCHIK_LLM_TEST", candidate_id=item.candidate_id,
            account_id="DU123456", symbol="AAPL", side="BUY",
            entry=100.0, stop=98.0, target=106.0,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sorted(result.acquired for result in results) == [False, True]

    live = audit.reserve_execution(
        mode=AgentMode.LIVE_AUTONOMOUS.value,
        agent_id="GERCHIK_LLM_TEST", candidate_id=item.candidate_id,
        account_id="DU123456", symbol="AAPL", side="BUY",
        entry=100.0, stop=98.0, target=106.0,
    )
    assert live.acquired is True


def test_daily_paper_broker_cap_vetoes_before_order(tmp_path, paper_runtime):
    audit = DecisionAudit(tmp_path / "audit.db")
    for index in range(3):
        audit.record_execution(
            decision_id=f"old-decision-{index}", candidate_id=f"old-candidate-{index}",
            status="ibkr_paper_executed", mode=AgentMode.IBKR_PAPER_AUTONOMOUS.value,
            agent_id="GERCHIK_LLM_TEST", order_ids={"market_order_id": index + 1},
        )
    provider = Provider()
    manager = OrderManager(Broker())
    agent = AutonomousGerchikAgent(config=config(tmp_path), provider=provider, audit=audit, order_manager=manager)

    result = agent.process_candidate(candidate("daily-cap"), context=context(), signal=signal())

    assert result.status == "startup_veto"
    assert "ibkr_paper_daily_trade_cap" in result.reasons
    assert provider.calls == 1  # provider-only health probe; no decision is submitted
    assert manager.calls == 0


def test_ibkr_paper_mode_submits_exactly_once_after_all_gates(paper_runtime, tmp_path):
    broker = Broker()
    manager = OrderManager(broker)
    audit = DecisionAudit(tmp_path / "audit.db")
    agent = AutonomousGerchikAgent(
        config=config(tmp_path), provider=Provider(), audit=audit, order_manager=manager
    )

    result = agent.process_candidate(candidate(), context=context(), signal=signal())

    assert result.status == "ibkr_paper_executed", result.reasons
    assert result.action == "ENTER"
    assert manager.calls == 1
    assert audit.status()["ibkr_paper_executions"] == 1
    assert audit.list_decisions()[0]["execution_mode"] == "ibkr_paper_autonomous"


@pytest.mark.parametrize(
    "account_id, expected_reason",
    [("U123456", "paper_account_not_verified"), ("DU999999", "account_not_allowlisted")],
)
def test_live_or_unknown_account_never_crosses_into_paper_execution(paper_runtime, tmp_path, account_id, expected_reason):
    manager = OrderManager(Broker(account_id))
    agent = AutonomousGerchikAgent(
        config=config(tmp_path), provider=Provider(), audit=DecisionAudit(tmp_path / "audit.db"), order_manager=manager
    )

    result = agent.process_candidate(candidate(), context=context(), signal=signal())

    assert result.status == "startup_veto"
    assert expected_reason in result.reasons
    assert manager.calls == 0


def test_wait_and_stale_quote_do_not_call_order_manager(paper_runtime, tmp_path):
    manager = OrderManager(Broker())
    provider = Provider(DecisionAction.WAIT)
    agent = AutonomousGerchikAgent(
        config=config(tmp_path), provider=provider, audit=DecisionAudit(tmp_path / "audit.db"), order_manager=manager
    )
    waited = agent.process_candidate(candidate("wait"), context=context(), signal=signal())
    assert waited.status == "no_action"
    assert manager.calls == 0

    stale_context = replace(context(), data_age_seconds=999.0)
    provider.action = DecisionAction.ENTER
    manager.market_data.get_quote = lambda symbol: {
        "bid": 99.9, "ask": 100.1, "last": 100.0, "quote_status": "ok",
        "timestamp": "2000-01-01T00:00:00+00:00",
    }
    stale = agent.process_candidate(candidate("stale"), context=stale_context, signal=signal())
    assert stale.status in {"risk_veto", "reconciliation_veto"}
    assert manager.calls == 0


def test_uncertain_restart_reconciles_without_resubmission(tmp_path):
    audit = DecisionAudit(tmp_path / "audit.db")
    item = candidate("restart")
    audit.record_candidate(item)
    reservation = audit.reserve_execution(
        mode=AgentMode.IBKR_PAPER_AUTONOMOUS.value,
        agent_id="GERCHIK_LLM_TEST",
        candidate_id=item.candidate_id,
        account_id="DU123456",
        symbol="AAPL", side="BUY", entry=100.0, stop=98.0, target=106.0,
        order_ref="GERCHIK_LLM_TEST-restart-order",
    )
    audit.update_reservation(reservation.reservation_id, state="SUBMITTING")
    manager = OrderManager(Broker())
    agent = AutonomousGerchikAgent(config=config(tmp_path), provider=Provider(), audit=audit, order_manager=manager)

    resolved = agent.reconcile_live_reservations()

    assert resolved[0]["state"] == "RECONCILIATION_REQUIRED"
    assert manager.calls == 0
