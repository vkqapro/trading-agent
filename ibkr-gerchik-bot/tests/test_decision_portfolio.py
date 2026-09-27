from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.decision.models import DecisionCandidate
from src.decision.portfolio import PaperPortfolio


def _candidate() -> DecisionCandidate:
    return DecisionCandidate(
        candidate_id="portfolio-candidate-1",
        created_at=datetime.now(timezone.utc),
        asset_class="stock",
        symbol="AAPL",
        strategy="rebound",
        direction="long",
        entry=100.0,
        stop=98.0,
        target=106.0,
        reward_risk=3.0,
    )


def test_paper_portfolio_uses_conservative_quote_fill_and_isolated_state(tmp_path) -> None:
    portfolio = PaperPortfolio(tmp_path / "portfolio.json", starting_equity=10_000.0)
    position = portfolio.enter(
        _candidate(),
        quantity=10,
        quote={"bid": 99.9, "ask": 100.1},
        slippage_pct=0.001,
        commission_per_share=0.02,
    )

    assert position["owner"] == "LLM_AGENT"
    assert position["entry"] > 100.1
    assert portfolio.state()["cash"] < 10_000.0
    assert portfolio.state()["positions"][0]["candidate_id"] == "portfolio-candidate-1"


def test_paper_portfolio_closes_and_recovers_after_restart(tmp_path) -> None:
    path = tmp_path / "portfolio.json"
    portfolio = PaperPortfolio(path, starting_equity=10_000.0)
    position = portfolio.enter(
        _candidate(),
        quantity=10,
        quote={"bid": 100.0, "ask": 100.1},
        slippage_pct=0.0,
        commission_per_share=0.0,
    )
    closed = portfolio.close(position["position_id"], price=104.0, reason="TARGET")
    restored = PaperPortfolio(path, starting_equity=999.0)

    assert closed["final_status"] == "CLOSED"
    assert closed["pnl"] == pytest.approx(39.0)
    assert restored.state()["positions"] == []
    assert restored.state()["closed_positions"][0]["final_r"] == pytest.approx(1.95)
    assert restored.state()["realized_pnl"] == pytest.approx(39.0)


def test_paper_portfolio_rejects_duplicate_symbol_ownership(tmp_path) -> None:
    portfolio = PaperPortfolio(tmp_path / "portfolio.json", starting_equity=10_000.0)
    portfolio.enter(_candidate(), quantity=1, quote={"bid": 100.0, "ask": 100.0})

    try:
        portfolio.enter(_candidate(), quantity=1, quote={"bid": 100.0, "ask": 100.0})
    except ValueError as exc:
        assert "already owned" in str(exc)
    else:  # pragma: no cover - safety assertion
        raise AssertionError("duplicate paper ownership must be rejected")
