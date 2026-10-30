"""Immutable contracts for canonical daily setup and current-session preview."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Mapping

class SetupStatus(str, Enum):
    WATCH="WATCH"; READY="READY"; REJECTED="REJECTED"; STALE_DATA="STALE_DATA"; INVALID="INVALID"; NO_SETUP="NO_SETUP"

class SessionFreshnessStatus(str, Enum):
    FRESH="FRESH"; DELAYED_USABLE="DELAYED_USABLE"; STALE="STALE"; MISSING="MISSING"

@dataclass(frozen=True)
class SessionFreshnessPolicy:
    fresh_limit_seconds: float = 900.0
    delayed_usable_limit_seconds: float = 3600.0

SWING_FRESHNESS_POLICY = SessionFreshnessPolicy()

@dataclass(frozen=True)
class SessionFreshness:
    age_seconds: float|None
    fresh_limit_seconds: float
    delayed_usable_limit_seconds: float
    status: SessionFreshnessStatus
    as_of: str|None
    reference_time: str|None
    def to_dict(self):
        result=asdict(self); result["status"]=self.status.value; return result

@dataclass(frozen=True)
class Bar:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    closed: bool = True
    def to_dict(self): return asdict(self)

@dataclass(frozen=True)
class CurrentSessionState:
    session_date: str
    open: float|None
    high: float|None
    low: float|None
    close: float|None
    volume: float|None
    as_of: str
    completeness: str
    source: str
    timeframe: str
    persisted_intraday_timestamp: str|None
    freshness_seconds: float|None
    reference_time: str|None = None
    def to_dict(self):
        result=asdict(self); result["complete"] = self.complete; result["freshness"] = self.freshness_state; result["freshness_detail"] = self.freshness_detail.to_dict()
        return result
    @property
    def freshness_detail(self):
        policy=SWING_FRESHNESS_POLICY
        age=self.freshness_seconds
        if age is None or age < 0:
            status=SessionFreshnessStatus.MISSING
        elif age <= policy.fresh_limit_seconds:
            status=SessionFreshnessStatus.FRESH
        elif age <= policy.delayed_usable_limit_seconds:
            status=SessionFreshnessStatus.DELAYED_USABLE
        else:
            status=SessionFreshnessStatus.STALE
        return SessionFreshness(age,policy.fresh_limit_seconds,policy.delayed_usable_limit_seconds,status,self.as_of,self.reference_time)
    @property
    def complete(self): return str(self.completeness).lower() == "complete"
    @property
    def freshness(self): return self.freshness_seconds
    @property
    def freshness_state(self): return self.freshness_detail.status.value
    @property
    def ohlcv(self): return {"open":self.open,"high":self.high,"low":self.low,"close":self.close,"volume":self.volume}

@dataclass(frozen=True)
class DailySetup:
    schema_version: str
    setup_id: str
    symbol: str
    strategy_id: str
    direction: str
    setup_status: str
    level: Mapping[str, Any]
    pattern_bars: Mapping[str, Any]
    planned_trade: Mapping[str, Any]
    setup_evidence: Mapping[str, Any]
    provenance: Mapping[str, Any]
    def to_dict(self):
        result=asdict(self)
        for key in ("level","pattern_bars","planned_trade","setup_evidence","provenance"): result[key]=dict(result[key])
        return result

@dataclass(frozen=True)
class LiveTradePlan:
    setup_id: str
    plan_as_of: str
    atr: float
    entry: float
    stop: float
    target: float
    rr: float
    session_source: str
    session_complete: bool
    plan_hash: str
    plan_evidence: Mapping[str, Any]
    def to_dict(self): return asdict(self)

@dataclass(frozen=True)
class TriggerEvaluation:
    setup_id: str
    status: SetupStatus
    reason: str
    trigger_evidence: Mapping[str, Any]
    rejection_evidence: tuple[str, ...] = ()
    def to_dict(self):
        result=asdict(self); result["status"]=self.status.value; result["trigger_evidence"]=dict(self.trigger_evidence); result["rejection_evidence"]=list(self.rejection_evidence); return result

@dataclass(frozen=True)
class LiveStrategyResult:
    schema_version: str
    symbol: str
    strategy_id: str
    direction: str
    status: SetupStatus
    setup: DailySetup|None
    session: CurrentSessionState|None
    trigger: TriggerEvaluation|None
    provenance: Mapping[str, Any]
    live_plan: LiveTradePlan|None = None
    def to_dict(self):
        return {"schema_version":self.schema_version,"symbol":self.symbol,"strategy_id":self.strategy_id,"direction":self.direction,"status":self.status.value,"setup":self.setup.to_dict() if self.setup else None,"session":self.session.to_dict() if self.session else None,"trigger":self.trigger.to_dict() if self.trigger else None,"provenance":dict(self.provenance),"live_plan":self.live_plan.to_dict() if self.live_plan else None}

# Backward-compatible contracts used by the original preview tests/API.
@dataclass(frozen=True)
class SetupCandidate:
    setup_id: str; symbol: str; strategy: str; direction: str; setup_date: str; level: float; entry: float; stop: float|None; target: float|None; status: SetupStatus; provenance: Mapping[str, Any]; trigger_evidence: Mapping[str, Any]
    def to_dict(self):
        d=asdict(self); d['status']=self.status.value; d['provenance']=dict(self.provenance); d['trigger_evidence']=dict(self.trigger_evidence); return d

@dataclass(frozen=True)
class TriggerResult:
    setup_id: str; status: SetupStatus; reason: str; session: CurrentSessionState|None
    def to_dict(self):
        d=asdict(self); d['status']=self.status.value; d['session']=self.session.to_dict() if self.session else None; return d

@dataclass(frozen=True)
class LivePreview:
    symbol: str; as_of: str|None; setups: tuple[SetupCandidate,...]; triggers: tuple[TriggerResult,...]; status: SetupStatus
    def to_dict(self): return {'symbol':self.symbol,'as_of':self.as_of,'status':self.status.value,'setups':[x.to_dict() for x in self.setups],'triggers':[x.to_dict() for x in self.triggers]}
