from __future__ import annotations

from src.decision.candidate_adapter import mapping_to_candidate
from src.decision.portfolio import PaperPortfolio


def test_crypto_mapping_adapter_preserves_calculated_plan(tmp_path) -> None:
    candidate = mapping_to_candidate({
        "symbol": "BTC-USDT-SWAP",
        "strategy": "retest",
        "direction": "short",
        "entry_price": 100.0,
        "stop_loss": 105.0,
        "take_profit": 90.0,
        "atr": 4.0,
        "rr": 2.0,
        "timestamp": "2026-09-26T13:00:00+00:00",
    })

    assert candidate.asset_class == "crypto"
    assert candidate.symbol == "BTC-USDT-SWAP"
    assert candidate.entry == 100.0
    assert candidate.stop == 105.0
    assert candidate.target == 90.0
    assert mapping_to_candidate(candidate.to_dict()).candidate_id == candidate.candidate_id


def test_paper_protection_is_deterministic_and_does_not_need_provider(tmp_path) -> None:
    portfolio = PaperPortfolio(tmp_path / "portfolio.json", starting_equity=10_000.0)
    candidate = mapping_to_candidate({
        "symbol": "AAPL", "strategy": "rebound", "direction": "long",
        "entry": 100.0, "stop": 98.0, "target": 104.0,
        "timestamp": "2026-09-26T13:00:00+00:00",
    }, asset_class="stock")
    position = portfolio.enter(candidate, quantity=10, quote={"ask": 100.0})

    closed = portfolio.enforce_protection({"AAPL": {"last": 97.5}})

    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "deterministic_stop"
    assert portfolio.state()["positions"] == []
