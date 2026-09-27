from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.decision.agent import AgentRuntimeContext, AutonomousGerchikAgent
from src.decision.models import AgentMode, DecisionAction, DecisionCandidate, DecisionResponse, PositionAction
from src.decision.audit import DecisionAudit
from src.decision.portfolio import PaperPortfolio


def _candidate() -> DecisionCandidate:
    return DecisionCandidate(
        candidate_id="agent-candidate-1",
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
        level_strength=8.0,
        atr=4.0,
        reward_risk=3.0,
    )


def _context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        account_equity=50_000.0,
        cash_available=50_000.0,
        current_positions=(),
        open_risk_amount=0.0,
        spread_pct=0.0005,
        broker_connected=True,
        account_synced=True,
        market_open=True,
        data_age_seconds=1.0,
        current_price=100.0,
        quote={"bid": 99.9, "ask": 100.1},
        isolated_paper=True,
    )


class FakeProvider:
    provider_name = "fake"
    model = "fake-model"

    def __init__(self, action: DecisionAction = DecisionAction.ENTER) -> None:
        self.action = action
        self.calls = 0

    def decide(self, request):
        self.calls += 1
        return DecisionResponse(
            action=self.action,
            confidence=0.9,
            ranked_actions=((self.action.value, 0.9),),
            reason_codes=("TEST",),
            summary="test decision",
        )


def _config(tmp_path, mode: str):
    return SimpleNamespace(
        mode=mode,
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
        allow_live_trading=False,
        live_account_allowlist=(),
        local_model="",
    )


def test_shadow_records_decision_without_paper_or_broker_execution(tmp_path) -> None:
    provider = FakeProvider()
    audit = DecisionAudit(tmp_path / "audit.db")
    agent = AutonomousGerchikAgent(
        config=_config(tmp_path, AgentMode.SHADOW.value),
        provider=provider,
        audit=audit,
        portfolio=PaperPortfolio(tmp_path / "portfolio.json", starting_equity=50_000.0),
    )

    result = agent.process_candidate(_candidate(), context=_context())

    assert result.status == "shadow"
    assert result.action == "ENTER"
    assert provider.calls == 1
    assert agent.portfolio.state()["positions"] == []
    assert audit.list_decisions()[0]["execution_status"] is None


def test_paper_autonomous_enters_without_order_manager(tmp_path) -> None:
    provider = FakeProvider()
    portfolio = PaperPortfolio(tmp_path / "portfolio.json", starting_equity=50_000.0)
    agent = AutonomousGerchikAgent(
        config=_config(tmp_path, AgentMode.PAPER_AUTONOMOUS.value),
        provider=provider,
        audit=DecisionAudit(tmp_path / "audit.db"),
        portfolio=portfolio,
    )

    result = agent.process_candidate(_candidate(), context=_context())

    assert result.status == "paper_simulated"
    assert result.action == "ENTER"
    assert len(portfolio.state()["positions"]) == 1
    assert result.execution["owner"] == "LLM_AGENT"


def test_provider_failure_is_no_action_and_does_not_open_paper_position(tmp_path) -> None:
    class FailedProvider(FakeProvider):
        def decide(self, request):
            self.calls += 1
            raise RuntimeError("provider unavailable")

    portfolio = PaperPortfolio(tmp_path / "portfolio.json", starting_equity=50_000.0)
    agent = AutonomousGerchikAgent(
        config=_config(tmp_path, AgentMode.PAPER_AUTONOMOUS.value),
        provider=FailedProvider(),
        audit=DecisionAudit(tmp_path / "audit.db"),
        portfolio=portfolio,
    )

    result = agent.process_candidate(_candidate(), context=_context())

    assert result.status == "provider_error"
    assert result.action == "NO_ACTION"
    assert portfolio.state()["positions"] == []
    assert agent.audit.list_decisions()[0]["status"] == "provider_error"


def test_same_candidate_cannot_open_twice(tmp_path) -> None:
    provider = FakeProvider()
    portfolio = PaperPortfolio(tmp_path / "portfolio.json", starting_equity=50_000.0)
    agent = AutonomousGerchikAgent(
        config=_config(tmp_path, AgentMode.PAPER_AUTONOMOUS.value),
        provider=provider,
        audit=DecisionAudit(tmp_path / "audit.db"),
        portfolio=portfolio,
    )
    first = agent.process_candidate(_candidate(), context=_context())
    second = agent.process_candidate(_candidate(), context=_context())

    assert first.status == "paper_simulated"
    assert second.status == "idempotent_skip"
    assert provider.calls == 1
    assert len(portfolio.state()["positions"]) == 1


def test_off_mode_never_calls_provider_or_creates_audit_state(tmp_path) -> None:
    provider = FakeProvider()
    audit_path = tmp_path / "audit.db"
    agent = AutonomousGerchikAgent(config=_config(tmp_path, AgentMode.OFF.value), provider=provider)

    result = agent.process_candidate(_candidate(), context=_context())

    assert result.status == "disabled"
    assert provider.calls == 0
    assert not audit_path.exists()


def test_multi_provider_shadow_records_each_provider_without_execution(tmp_path) -> None:
    first = FakeProvider()
    second = FakeProvider(DecisionAction.WAIT)
    config = _config(tmp_path, AgentMode.SHADOW.value)
    config.multi_provider_shadow = True
    audit = DecisionAudit(tmp_path / "shadow.db")
    agent = AutonomousGerchikAgent(
        config=config,
        audit=audit,
        shadow_providers={"first": first, "second": second},
    )

    result = agent.process_candidate(_candidate(), context=_context())

    assert result.status == "shadow"
    assert result.action == "SHADOW"
    assert first.calls == second.calls == 1
    decisions = audit.list_decisions()
    assert len(decisions) == 2
    assert {row["provider"] for row in decisions} == {"fake"}
    assert audit.status()["execution_links"] == 0


def test_paper_position_review_can_close_only_agent_owned_position(tmp_path) -> None:
    provider = FakeProvider()
    portfolio = PaperPortfolio(tmp_path / "portfolio.json", starting_equity=50_000.0)
    audit = DecisionAudit(tmp_path / "audit.db")
    config = _config(tmp_path, AgentMode.PAPER_AUTONOMOUS.value)
    config.position_review_enabled = True
    agent = AutonomousGerchikAgent(config=config, provider=provider, audit=audit, portfolio=portfolio)
    opened = agent.process_candidate(_candidate(), context=_context())
    position = portfolio.state()["positions"][0]
    provider.action = PositionAction.CLOSE

    reviewed = agent.review_position(
        position,
        context=AgentRuntimeContext(**{
            **_context().__dict__,
            "current_positions": tuple(portfolio.state()["positions"]),
            "current_price": 104.0,
        }),
    )

    assert opened.status == "paper_simulated"
    assert reviewed.status == "position_reviewed"
    assert reviewed.action == PositionAction.CLOSE.value
    assert portfolio.state()["positions"] == []


def test_live_mode_is_hard_vetoed_by_default_safety_guards(tmp_path) -> None:
    config = _config(tmp_path, AgentMode.LIVE_AUTONOMOUS.value)
    config.allow_live_trading = True
    config.live_account_allowlist = ("DU-ALLOWED",)
    agent = AutonomousGerchikAgent(config=config, provider=FakeProvider(), audit=DecisionAudit(tmp_path / "live.db"))

    allowed, reasons = agent.startup_guard(account_id="DU-OTHER")

    assert not allowed
    assert "paper_trading_mode_enabled" in reasons
    assert "order_manager_missing" in reasons
    assert "account_not_allowlisted" in reasons
