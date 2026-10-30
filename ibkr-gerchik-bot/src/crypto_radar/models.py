"""Models for the read-only OKX Crypto Trade Radar."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CryptoRadarSettings:
    enabled: bool = True
    lookback_days: int = 300
    min_history_days: int = 200
    max_universe_size: int = 200
    trend_green_threshold: float = 60.0
    trend_red_threshold: float = 40.0
    compression_threshold: float = 45.0
    range_window: int = 20
    slope_window: int = 20
    min_quote_volume_usd: float = 0.0


@dataclass(frozen=True)
class IBKRCryptoContract:
    con_id: int
    symbol: str
    currency: str = "USD"
    sec_type: str = "CRYPTO"
    provider: str = ""
    valid_exchanges: tuple[str, ...] = ()
    min_tick: float | None = None
    trading_hours: str = ""
    account_qualified: bool = True
    qualification_status: str = "QUALIFIED_FOR_ACCOUNT"
    diagnostics: str | None = None

    @property
    def logical_pair(self) -> str:
        return f"{self.symbol.upper()}.{self.currency.upper()}"

    @property
    def identity_key(self) -> str:
        return f"CRYPTO:{self.con_id}:{self.logical_pair}:{self.provider.upper()}"

    def to_dict(self) -> dict[str, Any]:
        return {"conId": self.con_id, "symbol": self.symbol.upper(), "currency": self.currency.upper(), "secType": self.sec_type.upper(), "provider": self.provider, "validExchanges": list(self.valid_exchanges), "minTick": self.min_tick, "tradingHours": self.trading_hours, "accountQualified": self.account_qualified, "qualificationStatus": self.qualification_status, "diagnostics": self.diagnostics, "logicalPair": self.logical_pair}


@dataclass(frozen=True)
class OKXCryptoInstrument:
    inst_id: str
    base_ccy: str = ""
    quote_ccy: str = ""
    state: str = "live"
    lot_sz: str = ""
    min_sz: str = ""
    tick_sz: str = ""
    account_qualified: bool = True
    diagnostics: str | None = None

    @property
    def sec_type(self) -> str:
        return "SPOT"

    @property
    def symbol(self) -> str:
        return self.inst_id.upper()

    @property
    def currency(self) -> str:
        return self.quote_ccy.upper()

    @property
    def provider(self) -> str:
        return "OKX"

    @property
    def logical_pair(self) -> str:
        return self.inst_id.upper()

    @property
    def identity_key(self) -> str:
        return f"OKX:{self.inst_id.upper()}"

    @property
    def con_id(self) -> int:
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "instId": self.inst_id.upper(),
            "symbol": self.symbol,
            "currency": self.currency,
            "secType": self.sec_type,
            "provider": self.provider,
            "state": self.state,
            "baseCcy": self.base_ccy.upper(),
            "quoteCcy": self.quote_ccy.upper(),
            "lotSz": self.lot_sz,
            "minSz": self.min_sz,
            "tickSz": self.tick_sz,
            "accountQualified": self.account_qualified,
            "diagnostics": self.diagnostics,
            "logicalPair": self.logical_pair,
        }


@dataclass(frozen=True)
class CryptoRadarResult:
    contract: IBKRCryptoContract | OKXCryptoInstrument
    as_of: str
    latest_confirmed_candle: str | None
    status: str
    trend: str = "GREY"
    trend_score: float = 0.0
    confirmation_score: float = 0.0
    timeframe_alignment: str = "UNKNOWN"
    breakout: bool = False
    breakout_direction: str | None = None
    breakout_score: float = 0.0
    compression_score: float = 0.0
    momentum_score: float = 0.0
    liquidity_score: float = 0.0
    liquidity_available: bool = False
    price: float | None = None
    quote_volume: float | None = None
    volume_ratio: float | None = None
    atr: float | None = None
    atr_pct: float | None = None
    radar_score: float = 0.0
    previous_trend: str | None = None
    trend_flip: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    scanner_status: str = "NOT_SCANNED"
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        contract_payload = self.contract.to_dict()
        return {"asset_class": "crypto", "contract": contract_payload, "instId": contract_payload.get("instId"), "conId": self.contract.con_id, "symbol": self.contract.symbol.upper(), "provider": self.contract.provider, "as_of": self.as_of, "latest_confirmed_candle": self.latest_confirmed_candle, "status": self.status, "trend": self.trend, "trend_score": self.trend_score, "confirmation_score": self.confirmation_score, "timeframe_alignment": self.timeframe_alignment, "breakout": self.breakout, "breakout_direction": self.breakout_direction, "breakout_score": self.breakout_score, "compression_score": self.compression_score, "momentum_score": self.momentum_score, "liquidity_score": self.liquidity_score, "liquidity_available": self.liquidity_available, "price": self.price, "quote_volume": self.quote_volume, "volume_ratio": self.volume_ratio, "atr": self.atr, "atr_pct": self.atr_pct, "radar_score": self.radar_score, "previous_trend": self.previous_trend, "trend_flip": self.trend_flip, "reasons": list(self.reasons), "diagnostics": list(self.diagnostics), "scanner_status": self.scanner_status, "created_at": self.created_at}
