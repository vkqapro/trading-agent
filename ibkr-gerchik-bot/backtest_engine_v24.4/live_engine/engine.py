"""Canonical deterministic two-stage LONG-only setup and trigger evaluation."""
from __future__ import annotations
import hashlib
import json
from dataclasses import replace
from typing import Any, Mapping, Sequence
import pandas as pd
from .contracts import (CurrentSessionState, DailySetup, LivePreview, LiveStrategyResult,
    LiveTradePlan, SessionFreshness, SessionFreshnessPolicy, SessionFreshnessStatus, SWING_FRESHNESS_POLICY,
    SetupCandidate, SetupStatus, TriggerEvaluation, TriggerResult)

STRATEGIES=("lp1","lp2","prb1","prb2")

def _resolve_freshness_policy(policy: SessionFreshnessPolicy=SWING_FRESHNESS_POLICY, max_freshness_seconds: float|None=None) -> SessionFreshnessPolicy:
    if max_freshness_seconds is None: return policy
    return SessionFreshnessPolicy(float(max_freshness_seconds), policy.delayed_usable_limit_seconds)

def _session_freshness(session: CurrentSessionState, policy: SessionFreshnessPolicy) -> SessionFreshness:
    age=session.freshness_seconds
    if age is None or age < 0: status=SessionFreshnessStatus.MISSING
    elif age <= policy.fresh_limit_seconds: status=SessionFreshnessStatus.FRESH
    elif age <= policy.delayed_usable_limit_seconds: status=SessionFreshnessStatus.DELAYED_USABLE
    else: status=SessionFreshnessStatus.STALE
    return SessionFreshness(age,policy.fresh_limit_seconds,policy.delayed_usable_limit_seconds,status,session.as_of,session.reference_time)

def _stable_id(symbol: str, strategy: str, level: Any, dates: Sequence[str]) -> str:
    raw=f"{symbol.upper()}|{strategy.lower()}|{float(level):.10f}|{'|'.join(dates)}"
    return "setup_" + hashlib.sha256(raw.encode()).hexdigest()[:20]

def _date(value: Any) -> str: return pd.Timestamp(value).date().isoformat()
def _num(row: Any, name: str):
    try: return float(row[name])
    except (KeyError, TypeError, ValueError): return None

def _web_imports():
    from dashboard_react.market_screener import ScreenerParams, _add_metrics, _detect_signals, _pivot_levels
    return ScreenerParams, _add_metrics, _detect_signals, _pivot_levels

def _frame(data: pd.DataFrame) -> pd.DataFrame:
    frame=data.copy()
    rename={c: c.lower() for c in frame.columns}
    frame=frame.rename(columns=rename)
    if "date" not in frame and isinstance(frame.index, pd.DatetimeIndex): frame=frame.reset_index().rename(columns={frame.index.name or "index":"date"})
    for col in ("date","open","high","low","close","volume"):
        if col not in frame: raise ValueError(f"missing daily bar field: {col}")
    frame["date"]=pd.to_datetime(frame["date"],errors="coerce")
    frame=frame.dropna(subset=["date","open","high","low","close","volume"]).sort_values("date").drop_duplicates("date",keep="last")
    if "closed" in frame: frame=frame.loc[frame["closed"].astype(bool)]
    return frame.reset_index(drop=True)

def _bar_dict(row: pd.Series, role: str) -> dict[str, Any]:
    return {"date":_date(row["date"]),"role":role,"open":float(row["open"]),"high":float(row["high"]),"low":float(row["low"]),"close":float(row["close"]),"volume":float(row["volume"])}

def _developing_frame(historical_daily_bars: pd.DataFrame, session: CurrentSessionState) -> pd.DataFrame:
    if session.open is None or session.high is None or session.low is None or session.close is None or session.volume is None:
        raise ValueError("CurrentSessionState lacks complete developing OHLCV")
    historical = _frame(historical_daily_bars)
    session_date = pd.Timestamp(session.session_date).date()
    historical = historical.loc[historical["date"].dt.date < session_date].reset_index(drop=True)
    current = pd.DataFrame([{
        "date": session.session_date, "open": session.open, "high": session.high,
        "low": session.low, "close": session.close, "volume": session.volume,
    }])
    return pd.concat([historical, current], ignore_index=True)


def _live_plan(setup: DailySetup, session: CurrentSessionState, historical_daily_bars: pd.DataFrame) -> LiveTradePlan:
    _, add_metrics, _, _ = _web_imports()
    metrics = add_metrics(_developing_frame(historical_daily_bars, session))
    atr = float(metrics.iloc[-1]["atr_clean_14"])
    strategy = setup.strategy_id.upper()
    level = float(setup.level["price"])
    pattern = list(setup.pattern_bars.values())
    if not pattern:
        raise ValueError("setup has no historical pattern bars")
    if strategy == "LP1":
        stop_reference = float(pattern[0]["low"])
        stop_formula = "signal_bar.low - 0.25 * live_ATR"
    elif strategy == "LP2":
        stop_reference = min(float(row["low"]) for row in pattern)
        stop_formula = "min(penetration.low, reclaim.low) - 0.25 * live_ATR"
    elif strategy == "PRB1":
        stop_reference = level
        stop_formula = "resistance - 0.25 * live_ATR"
    elif strategy == "PRB2":
        hold = next((row for row in pattern if row.get("role") == "hold_bar"), pattern[-1])
        stop_reference = float(hold["low"])
        stop_formula = "historical_hold_day1.low - 0.25 * live_ATR"
    else:
        raise ValueError(f"unsupported live strategy: {strategy}")
    params = setup.provenance.get("strategy_params") or {}
    rr = float(params.get("rr", 2.0))
    entry = level + 0.50 * atr
    stop = stop_reference - 0.25 * atr
    target = entry + rr * (entry - stop)
    payload = {"setup_id": setup.setup_id, "plan_as_of": session.as_of, "atr": atr, "entry": entry, "stop": stop, "target": target, "rr": rr, "session_source": session.source, "session_complete": session.complete}
    plan_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    evidence = {"level": level, "atr_source": "developing_entry_day", "entry_formula": "level + 0.50 * live_ATR", "stop_reference": stop_reference, "stop_formula": stop_formula, "target_formula": "entry + rr * (entry - stop)"}
    return LiveTradePlan(setup.setup_id, session.as_of, atr, entry, stop, target, rr, session.source, session.complete, plan_hash, evidence)


def detect_daily_setups(symbol: str, closed_daily_bars: pd.DataFrame, *, strategies: Sequence[str]=STRATEGIES, levels: Sequence[Mapping[str, Any]]|None=None, current_session_date: str|None=None, params: Any=None) -> tuple[DailySetup,...]:
    """Detect Web-semantic setups using only completed historical daily bars.

    The candidate entry day is a completed bar used only for historical setup
    diagnostics. The live evaluator recomputes ATR/trade-plan values from the
    developing CurrentSessionState; it is never included in pattern_bars.
    """
    ScreenerParams, add_metrics, detect, pivots = _web_imports()
    frame=_frame(closed_daily_bars)
    if current_session_date: frame=frame.loc[frame["date"].dt.date < pd.Timestamp(current_session_date).date()].reset_index(drop=True)
    if len(frame)<4: return ()
    selected=tuple(str(x).lower() for x in strategies if str(x).lower() in STRATEGIES)
    if not selected: return ()
    base=params or ScreenerParams(strategies=tuple(x.upper() for x in selected), side_filter="LONG")
    out: dict[str,DailySetup]={}
    # The current live session is the entry day.  The Web detector expects an
    # entry-day row, so use a neutral copy of the last CLOSED row solely as a
    # deterministic evaluation placeholder.  It is never persisted or exposed
    # as a pattern bar.  Evaluating every historical prefix was both incorrect
    # for the live contract and needlessly quadratic for a 95-symbol universe.
    historical=add_metrics(frame.copy())
    supplied=list(levels) if levels is not None else pivots(historical,k=base.pivot_k,min_touches=base.min_touches,tolerance_atr=base.level_tolerance_atr)
    entry_date=pd.Timestamp(current_session_date) if current_session_date else pd.bdate_range(frame.iloc[-1]["date"],periods=2)[-1]
    frame_tz=getattr(frame["date"].dt, "tz", None)
    if frame_tz is not None and entry_date.tzinfo is None:
        entry_date=entry_date.tz_localize(frame_tz)
    elif frame_tz is None and entry_date.tzinfo is not None:
        entry_date=entry_date.tz_localize(None)
    placeholder=frame.iloc[[-1]].copy()
    placeholder.loc[placeholder.index[0],"date"]=entry_date
    entry_frame=pd.concat([frame,placeholder],ignore_index=True)
    prepared=add_metrics(entry_frame)
    signals=detect(symbol,prepared,supplied,base,entry_day_partial=True)
    for signal in signals:
        if str(signal.get("side")).upper()!="LONG" or str(signal.get("status")) not in {"READY","pending_entry","overextended","missed_due_to_gap","chased_gap_skip"}: continue
        pattern_dates=list(signal.get("pattern_bar_dates") or [])
        if not pattern_dates: continue
        rows={_date(row["date"]):row for _,row in frame.iterrows()}
        pattern={}
        for n,date in enumerate(pattern_dates,1):
            if date in rows: pattern[f"bar_{n}"]=_bar_dict(rows[date], "penetration_bar" if n==1 and len(pattern_dates)>1 else "pattern_bar")
        if len(pattern_dates)>1 and f"bar_{len(pattern_dates)}" in pattern:
            pattern[f"bar_{len(pattern_dates)}"]["role"]="reclaim_bar" if str(signal["strategy"]).upper()=="LP2" else "hold_bar"
        level=float(signal["level_price"]); sid=_stable_id(symbol,str(signal["strategy"]),level,pattern_dates)
        out[sid]=DailySetup("1.0",sid,symbol,str(signal["strategy"]).lower(),"LONG","VALID_SETUP",{"price":level,"type":str(signal.get("level_type") or "support").lower()},pattern,{"entry":float(signal["planned_entry_price"]),"stop":float(signal["stop_price"]),"target":float(signal["take_profit_price"])},{"web_reasoning":signal.get("reasoning"),"pattern_bar_dates":pattern_dates,"entry_day":_date(entry_date),"atr":signal.get("atr_clean_14"),"historical_trade_plan":{"entry":float(signal["planned_entry_price"]),"stop":float(signal["stop_price"]),"target":float(signal["take_profit_price"]),"source":"placeholder_entry_day_diagnostic"}},{"source":"dashboard_react.market_screener","historical_data_through":_date(frame.iloc[-1]["date"]),"entry_day_excluded_from_pattern":True,"strategy_params":base.__dict__})
    return tuple(sorted(out.values(),key=lambda x:(x.symbol,x.strategy_id,x.setup_id)))

def evaluate_entry_day(setup: DailySetup, session: CurrentSessionState|None, *, previous_close: float|None=None, max_freshness_seconds: float|None=None, freshness_policy: SessionFreshnessPolicy=SWING_FRESHNESS_POLICY) -> TriggerEvaluation:
    if session is None: return TriggerEvaluation(setup.setup_id,SetupStatus.INVALID,"MISSING_SESSION",{},("MISSING_SESSION",))
    if session.complete: return TriggerEvaluation(setup.setup_id,SetupStatus.INVALID,"SESSION_MUST_BE_CURRENT",{},("SESSION_MUST_BE_CURRENT",))
    if not session.source or not session.timeframe: return TriggerEvaluation(setup.setup_id,SetupStatus.INVALID,"INVALID_SESSION",{},("INVALID_SESSION",))
    policy=_resolve_freshness_policy(freshness_policy,max_freshness_seconds)
    freshness=_session_freshness(session,policy)
    freshness_evidence=freshness.to_dict()
    if freshness.status in {SessionFreshnessStatus.MISSING,SessionFreshnessStatus.STALE}:
        return TriggerEvaluation(setup.setup_id,SetupStatus.STALE_DATA,"STALE_SESSION",{"freshness":freshness_evidence,"freshness_seconds":session.freshness_seconds},("STALE_SESSION",))
    entry=float(setup.planned_trade["entry"]); high=session.high; open_price=session.open
    gap_atr=None; chase_atr=None
    atr=float(setup.setup_evidence.get("live_atr") or setup.setup_evidence.get("atr") or 0.0)
    strategy = setup.strategy_id.upper()
    level = float(setup.level.get("price"))
    strategy_params = setup.provenance.get("strategy_params", {})
    hold_tolerance = float(strategy_params.get("level_tolerance_atr", 0.10)) * max(atr, 0.0001)
    if previous_close is not None and atr>0: gap_atr=abs(float(open_price)-previous_close)/atr
    if atr>0: chase_atr=(float(open_price)-entry)/atr
    evidence={"planned_entry":entry,"session_high":high,"entry_reached":bool(high is not None and high>=entry),"gap_atr":gap_atr,"chase_atr":chase_atr,"gap_ok":gap_atr is None or gap_atr<=float(setup.provenance.get("strategy_params",{}).get("gap_max_atr",1.2)),"chase_ok":chase_atr is None or chase_atr<=float(setup.provenance.get("strategy_params",{}).get("chase_max_atr",0.3)),"session_as_of":session.as_of,"source":session.source,"freshness_seconds":session.freshness_seconds,"freshness":freshness_evidence}
    reasons=[]
    if strategy == "PRB1" and (open_price is None or float(open_price) < level - hold_tolerance):
        reasons.append("ENTRY_DAY_HOLD_FAILED")
    if strategy == "PRB2" and (open_price is None or float(open_price) < level - hold_tolerance or session.low is None or float(session.low) < level):
        reasons.append("ENTRY_DAY_HOLD_FAILED")
    if not evidence["gap_ok"]: reasons.append("GAP_EXCEEDED")
    if not evidence["chase_ok"] and evidence["entry_reached"]: reasons.append("CHASE_EXCEEDED")
    stop=setup.planned_trade.get("stop")
    if stop is None or float(stop)>=entry: reasons.append("INVALID_STOP")
    if reasons: return TriggerEvaluation(setup.setup_id,SetupStatus.REJECTED,reasons[0],evidence,tuple(reasons))
    if high is None or open_price is None: return TriggerEvaluation(setup.setup_id,SetupStatus.WATCH,"AWAITING_TRIGGER",evidence)
    if high>=entry: return TriggerEvaluation(setup.setup_id,SetupStatus.READY,"ENTRY_REACHED",evidence)
    return TriggerEvaluation(setup.setup_id,SetupStatus.WATCH,"ENTRY_NOT_REACHED",evidence)

def detect_historical_setups(symbol: str, daily_bars: pd.DataFrame, *, strategies: Sequence[str]=STRATEGIES, as_of: str|None=None) -> tuple[SetupCandidate,...]:
    setups=detect_daily_setups(symbol,daily_bars,strategies=strategies,current_session_date=as_of)
    return tuple(SetupCandidate(s.setup_id,s.symbol,s.strategy_id.upper(),s.direction,s.provenance["historical_data_through"],float(s.level["price"]),float(s.planned_trade["entry"]),s.planned_trade.get("stop"),s.planned_trade.get("target"),SetupStatus.WATCH,s.provenance,{"planned_entry":s.planned_trade["entry"]}) for s in setups)

def preview_current_session(setups: Sequence[SetupCandidate], session: CurrentSessionState|None, *, max_freshness_seconds: float|None=None, freshness_policy: SessionFreshnessPolicy=SWING_FRESHNESS_POLICY):
    out=[]; policy=_resolve_freshness_policy(freshness_policy,max_freshness_seconds)
    for s in setups:
        if session is None: out.append(TriggerResult(s.setup_id,SetupStatus.INVALID,"missing_current_session",None)); continue
        freshness=_session_freshness(session,policy)
        blocked=freshness.status in {SessionFreshnessStatus.MISSING,SessionFreshnessStatus.STALE}
        status=SetupStatus.STALE_DATA if blocked else SetupStatus.READY if session.high is not None and session.high>=s.entry else SetupStatus.WATCH
        out.append(TriggerResult(s.setup_id,status,"session_data_stale" if blocked else "entry_level_touched" if status is SetupStatus.READY else "entry_level_not_touched",session))
    return tuple(out)

class CanonicalLiveEngine:
    def __init__(self,*,max_freshness_seconds:float|None=None, freshness_policy:SessionFreshnessPolicy=SWING_FRESHNESS_POLICY): self.freshness_policy=_resolve_freshness_policy(freshness_policy,max_freshness_seconds)
    def preview(self,symbol,daily_bars,session,*,strategies=STRATEGIES,as_of=None):
        setups=detect_historical_setups(symbol,daily_bars,strategies=strategies,as_of=as_of)
        triggers=preview_current_session(setups,session,freshness_policy=self.freshness_policy)
        current=tuple(replace(s,status=t.status) for s,t in zip(setups,triggers))
        status=SetupStatus.NO_SETUP if not setups else SetupStatus.READY if any(t.status is SetupStatus.READY for t in triggers) else triggers[0].status
        return LivePreview(symbol,session.as_of if session else as_of,current,triggers,status)

def evaluate_live_setup(
    setup: DailySetup,
    session: CurrentSessionState|None,
    *,
    previous_close: float|None=None,
    max_freshness_seconds: float|None=None,
    freshness_policy: SessionFreshnessPolicy=SWING_FRESHNESS_POLICY,
    historical_daily_bars: pd.DataFrame|None=None,
) -> LiveStrategyResult:
    live_plan = None
    live_setup = setup
    if session is not None and historical_daily_bars is not None:
        live_plan = _live_plan(setup, session, historical_daily_bars)
        live_setup = replace(
            setup,
            planned_trade={"entry": live_plan.entry, "stop": live_plan.stop, "target": live_plan.target, "plan_hash": live_plan.plan_hash},
            setup_evidence={**dict(setup.setup_evidence), "live_atr": live_plan.atr, "atr_source": "developing_entry_day", "plan_hash": live_plan.plan_hash},
            provenance={**dict(setup.provenance), "plan_hash": live_plan.plan_hash, "plan_as_of": live_plan.plan_as_of},
        )
    trigger=evaluate_entry_day(live_setup,session,previous_close=previous_close,max_freshness_seconds=max_freshness_seconds,freshness_policy=freshness_policy)
    provenance={"historical_data_through":setup.provenance.get("historical_data_through"),"current_session_date":session.session_date if session else None,"current_session_as_of":session.as_of if session else None,"session_complete":session.complete if session else None}
    if live_plan is not None:
        provenance.update({"plan_hash":live_plan.plan_hash,"plan_as_of":live_plan.plan_as_of,"atr_source":"developing_entry_day"})
    return LiveStrategyResult("1.0",live_setup.symbol,live_setup.strategy_id,live_setup.direction,trigger.status,live_setup,session,trigger,provenance,live_plan)
