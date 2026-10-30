"""Orchestration for read-only Crypto Trade Radar."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Callable, Iterable
import pandas as pd
from .engine import analyze_crypto_radar
from .models import CryptoRadarResult, CryptoRadarSettings, IBKRCryptoContract, OKXCryptoInstrument
from .store import CryptoRadarStore

ContractLoader = Callable[[], Iterable[IBKRCryptoContract | OKXCryptoInstrument]]
BarsLoader = Callable[[IBKRCryptoContract | OKXCryptoInstrument, str], pd.DataFrame]

class CryptoRadarService:
    def __init__(self, *, store: CryptoRadarStore, contract_loader: ContractLoader, bars_loader: BarsLoader, settings: CryptoRadarSettings | None = None):
        self.store=store; self.contract_loader=contract_loader; self.bars_loader=bars_loader; self.settings=settings or CryptoRadarSettings()
    def contracts(self) -> list[IBKRCryptoContract | OKXCryptoInstrument]:
        unique={c.identity_key:c for c in self.contract_loader() if c.sec_type.upper() in {"CRYPTO", "SPOT"}}
        return sorted(unique.values(), key=lambda c:(c.symbol,c.provider,c.con_id))[:self.settings.max_universe_size]
    def refresh(self) -> dict:
        started=datetime.now(timezone.utc).isoformat(); results=[]; errors=[]
        if not self.settings.enabled:
            return {"enabled":False,"results":[],"errors":[],"updated_at":started,"orders_submitted":0,"orders_created":0,"execution_requested":False}
        contracts = self.contracts()
        for contract in contracts:
            try:
                # Daily history gates the rest of the analysis.  Do not spend
                # two more HMDS requests on a contract that already has no
                # usable daily data (common for an account-visible provider
                # without historical-market-data permission).
                daily = self.bars_loader(contract, "1D")
                bars = {"1D": daily}
                daily_rows = 0 if daily is None else len(daily)
                if daily_rows >= self.settings.min_history_days:
                    bars["4H"] = self.bars_loader(contract, "4H")
                    bars["1H"] = self.bars_loader(contract, "1H")
                else:
                    bars["4H"] = pd.DataFrame()
                    bars["1H"] = pd.DataFrame()
                previous=self.store.previous_trend(contract.identity_key, "9999-12-31T00:00:00Z")
                result=analyze_crypto_radar(contract,bars,settings=self.settings,previous_trend=previous)
                self.store.upsert(result); results.append(result.to_dict())
            except Exception as exc:
                errors.append({"symbol":contract.logical_pair,"conId":contract.con_id,"status":"ERROR","diagnostic":f"{type(exc).__name__}: {exc}"})
        return {"enabled":True,"results":sorted(results,key=lambda r:(-float(r.get("radar_score") or 0),r.get("symbol",""))),"errors":errors,"universe_size":len(contracts),"updated_at":datetime.now(timezone.utc).isoformat(),"orders_submitted":0,"orders_created":0,"execution_requested":False}
    def list_results(self, **filters) -> list[dict]:
        rows=self.store.latest(symbol=filters.get("symbol"),con_id=filters.get("conId"),provider=filters.get("provider"),limit=filters.get("limit",100))
        trend=str(filters.get("trend") or "").upper(); min_score=float(filters.get("min_radar_score") or 0); min_trend=float(filters.get("min_trend_score") or 0)
        if trend: rows=[r for r in rows if r.get("trend")==trend]
        return [r for r in rows if float(r.get("radar_score") or 0)>=min_score and float(r.get("trend_score") or 0)>=min_trend and (filters.get("breakout") is None or bool(r.get("breakout"))==bool(filters["breakout"]))]
    def scan_symbol(self, symbol: str, scanner: Callable[[str], dict]) -> dict:
        rows=self.list_results(symbol=symbol,limit=1)
        if not rows: raise ValueError(f"No crypto Radar snapshot found for {symbol.upper()}")
        payload=dict(scanner(symbol.upper())); payload["radar_snapshot_id"]=f"{rows[0].get('conId')}:{rows[0].get('as_of')}"; payload["conId"]=rows[0].get("conId"); payload["direction_conflict"]=bool((rows[0].get("trend")=="GREEN" and str(payload.get("side")).upper()=="SHORT") or (rows[0].get("trend")=="RED" and str(payload.get("side")).upper()=="LONG")); payload.update({"orders_submitted":0,"orders_created":0,"execution_requested":False}); return payload
    @staticmethod
    def readonly_payload(payload: dict) -> dict:
        result=dict(payload); result.update({"orders_submitted":0,"orders_created":0,"execution_requested":False}); return result
