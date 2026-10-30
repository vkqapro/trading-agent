from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from src.config import SETTINGS
from src.decision.audit import DecisionAudit
from src.jobs.autonomous_stock_preflight import run_preflight


class ReadOnlyPaperBroker:
    is_connected = True

    def __init__(self, account_id: str = "DU123456"):
        self.account_id = account_id
        self.disconnected = False

    def connect(self):
        return None

    def disconnect(self):
        self.disconnected = True

    def get_account_identity(self):
        return {
            "account_id": self.account_id,
            "paper_verified": True,
            "environment": "paper",
        }

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []

    def get_executions(self):
        return []


def test_ibkr_paper_preflight_is_read_only_and_requires_verified_evidence(tmp_path: Path):
    database = tmp_path / "decision_lab.db"
    audit = DecisionAudit(database)
    config = replace(
        SETTINGS.decision_agent,
        mode="ibkr_paper_autonomous",
        provider="local_openai",
        model="test-model",
        local_base_url="http://127.0.0.1:9/v1",
        local_model="test-model",
        allow_ibkr_paper_trading=True,
        ibkr_paper_account_allowlist=("DU123456",),
        allow_live_trading=False,
        database_path=database,
    )
    audit.record_provider_health(
        provider="local_openai",
        model="test-model",
        status="ok",
    )
    settings = replace(
        SETTINGS,
        decision_agent=config,
        paper_trading=True,
        dry_run_mode=False,
    )
    broker = ReadOnlyPaperBroker()
    result = run_preflight(broker_factory=lambda: broker, config=config, settings=settings)

    assert result.ready is True
    assert result.autonomous_entry_enabled is True
    assert result.broker_status == "PAPER_VERIFIED"
    assert result.reasons == ()
    assert broker.disconnected is True


def test_ibkr_paper_preflight_rejects_allowlist_mismatch(tmp_path: Path):
    database = tmp_path / "decision_lab.db"
    audit = DecisionAudit(database)
    config = replace(
        SETTINGS.decision_agent,
        mode="ibkr_paper_autonomous",
        provider="local_openai",
        model="test-model",
        local_base_url="http://127.0.0.1:9/v1",
        local_model="test-model",
        allow_ibkr_paper_trading=True,
        ibkr_paper_account_allowlist=("DU999999",),
        allow_live_trading=False,
        database_path=database,
    )
    audit.record_provider_health(provider="local_openai", model="test-model", status="ok")
    settings = replace(SETTINGS, decision_agent=config, paper_trading=True, dry_run_mode=False)
    result = run_preflight(
        broker_factory=lambda: ReadOnlyPaperBroker("DU123456"),
        config=config,
        settings=settings,
    )

    assert result.ready is False
    assert result.autonomous_entry_enabled is False
    assert "paper_account_not_allowlisted" in result.reasons
