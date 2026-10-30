from __future__ import annotations

import sqlite3

import pandas as pd

from src.crypto_radar.engine import analyze_crypto_radar
from src.crypto_radar.models import CryptoRadarResult, CryptoRadarSettings, IBKRCryptoContract, OKXCryptoInstrument
from src.crypto_radar.service import CryptoRadarService
from src.crypto_radar.store import CryptoRadarStore


def _bars(days: int = 240, *, incomplete: bool = False) -> pd.DataFrame:
    rows = []
    price = 100.0
    for i in range(days):
        price *= 1.002 if i < days - 30 else 1.0005
        rows.append({"date": pd.Timestamp("2025-01-01", tz="UTC") + pd.Timedelta(days=i), "open": price - 1, "high": price + 2, "low": price - 2, "close": price, "volume": 1000.0, "quote_volume": price * 1000, "confirmed": True})
    if incomplete:
        rows[-1]["confirmed"] = False
    return pd.DataFrame(rows)


def _contract(symbol: str = "BTC", con_id: int = 1, provider: str = "PAXOS") -> IBKRCryptoContract:
    return IBKRCryptoContract(con_id=con_id, symbol=symbol, currency="USD", sec_type="CRYPTO", provider=provider, valid_exchanges=(provider,), account_qualified=True)


def test_engine_excludes_incomplete_candle_and_uses_utc() -> None:
    result = analyze_crypto_radar(_contract(), {"1D": _bars(incomplete=True), "4H": _bars(), "1H": _bars()}, settings=CryptoRadarSettings(min_history_days=100))
    assert result.status == "UNCONFIRMED_BAR"
    assert result.latest_confirmed_candle is not None
    assert result.latest_confirmed_candle.endswith("Z")


def test_engine_scores_alignment_and_never_uses_contract_count_as_liquidity() -> None:
    bars = {"1D": _bars(), "4H": _bars(), "1H": _bars()}
    result = analyze_crypto_radar(_contract(), bars, settings=CryptoRadarSettings(min_history_days=100))
    assert result.status == "CANDIDATE"
    assert result.timeframe_alignment == "ALIGNED"
    assert 0 <= result.radar_score <= 100
    assert result.liquidity_available is True
    assert result.quote_volume is not None


def test_okx_instrument_identity_is_inst_id_based_and_service_scans_spot(tmp_path) -> None:
    instruments = [
        OKXCryptoInstrument(inst_id="BTC-USDT", base_ccy="BTC", quote_ccy="USDT", state="live"),
        OKXCryptoInstrument(inst_id="ETH-USDT", base_ccy="ETH", quote_ccy="USDT", state="live"),
    ]
    service = CryptoRadarService(
        store=CryptoRadarStore(tmp_path / "okx.db"),
        contract_loader=lambda: instruments,
        bars_loader=lambda instrument, timeframe: _bars(),
        settings=CryptoRadarSettings(min_history_days=100),
    )

    payload = service.refresh()

    assert payload["universe_size"] == 2
    assert {row["symbol"] for row in payload["results"]} == {"BTC-USDT", "ETH-USDT"}
    assert {row["contract"]["instId"] for row in payload["results"]} == {"BTC-USDT", "ETH-USDT"}


def test_contract_identity_preserves_provider_and_conid() -> None:
    first = _contract(con_id=1, provider="PAXOS")
    second = _contract(con_id=2, provider="ZEROHASH")
    assert first.identity_key != second.identity_key
    assert first.logical_pair == second.logical_pair


def test_engine_ignores_future_confirmed_candle() -> None:
    bars = _bars()
    future = bars.iloc[-1].copy()
    future["date"] = pd.Timestamp("2999-01-01", tz="UTC")
    bars = pd.concat([bars, pd.DataFrame([future])], ignore_index=True)
    result = analyze_crypto_radar(_contract(), {"1D": bars, "4H": _bars(), "1H": _bars()}, settings=CryptoRadarSettings(min_history_days=100))
    assert result.status == "CANDIDATE"
    assert not result.latest_confirmed_candle.startswith("2999-")


def test_store_latest_deduplicates_identity_and_excludes_future(tmp_path) -> None:
    store = CryptoRadarStore(tmp_path / "latest.db")
    contract = _contract("BTC", 1)
    store.upsert(CryptoRadarResult(contract, "2026-10-26T00:00:00Z", "2026-10-26T00:00:00Z", "CANDIDATE", radar_score=10))
    store.upsert(CryptoRadarResult(contract, "2026-10-27T00:00:00Z", "2026-10-27T00:00:00Z", "CANDIDATE", radar_score=20))
    store.upsert(CryptoRadarResult(contract, "2999-01-01T00:00:00Z", "2999-01-01T00:00:00Z", "CANDIDATE", radar_score=99))
    rows = store.latest(limit=10)
    assert len(rows) == 1
    assert rows[0]["as_of"] == "2026-10-27T00:00:00Z"


def test_refresh_is_idempotent_and_failed_symbol_isolated(tmp_path) -> None:
    store = CryptoRadarStore(tmp_path / "radar.db")
    contracts = [_contract("BTC", 1), _contract("ETH", 2)]
    service = CryptoRadarService(store=store, contract_loader=lambda: contracts, bars_loader=lambda c, tf: _bars(), settings=CryptoRadarSettings(min_history_days=100))
    first = service.refresh()
    second = service.refresh()
    assert first["orders_submitted"] == second["orders_submitted"] == 0
    with sqlite3.connect(tmp_path / "radar.db") as db:
        assert db.execute("select count(*) from crypto_radar_snapshots").fetchone()[0] == 2


def test_unqualified_contract_is_not_candidate(tmp_path) -> None:
    contract = IBKRCryptoContract(con_id=9, symbol="X", currency="USD", sec_type="CRYPTO", provider="PAXOS", account_qualified=False)
    service = CryptoRadarService(store=CryptoRadarStore(tmp_path / "r.db"), contract_loader=lambda: [contract], bars_loader=lambda c, tf: _bars(), settings=CryptoRadarSettings(min_history_days=100))
    payload = service.refresh()
    assert payload["results"][0]["status"] == "UNAVAILABLE_FOR_ACCOUNT"


def test_refresh_skips_lower_timeframes_when_daily_history_is_unusable(tmp_path) -> None:
    calls = []

    def loader(contract, timeframe):
        calls.append((contract.symbol, timeframe))
        return pd.DataFrame()

    service = CryptoRadarService(
        store=CryptoRadarStore(tmp_path / "short.db"),
        contract_loader=lambda: [_contract("BTC", 1)],
        bars_loader=loader,
        settings=CryptoRadarSettings(min_history_days=200),
    )
    payload = service.refresh()
    assert calls == [("BTC", "1D")]
    assert payload["results"][0]["status"] == "INSUFFICIENT_DATA"

