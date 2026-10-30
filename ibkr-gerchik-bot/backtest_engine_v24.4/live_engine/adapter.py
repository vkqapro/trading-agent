"""Read-only adapter for persisted current-session state."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
import pandas as pd
from .contracts import CurrentSessionState
from src.data.bar_store import load_bars
from src.data.chart_history import build_partial_daily_frame

def load_closed_daily_frame(symbol: str, *, as_of: str|None=None, limit: int=500):
    frame=load_bars(symbol,"daily")
    if frame.empty: raise ValueError(f"No closed daily data for {symbol}")
    dates=pd.to_datetime(frame["date"],errors="coerce")
    if as_of:
        cutoff=pd.Timestamp(as_of)
        if cutoff.tzinfo is not None and getattr(dates.dt,"tz",None) is None: cutoff=cutoff.tz_localize(None)
        frame=frame.loc[dates < cutoff.normalize()]
    else:
        frame=frame.loc[dates.dt.date < datetime.now().date()]
    frame=frame.dropna(subset=["date","open","high","low","close"]).set_index("date")[["open","high","low","close","volume"]]
    frame.columns=["Open","High","Low","Close","Volume"]
    if frame.empty: raise ValueError(f"No closed daily data for {symbol}")
    return frame

class PersistedSessionAdapter:
    def __init__(self, loader=None):
        if loader is None:
            from src.mcp.trading_data_service import get_symbol_history
            loader=get_symbol_history
        self.loader=loader
    def _fetch(self,symbol,timeframe,as_of,limit,include_incomplete=True):
        try: return self.loader(symbol,timeframe,limit=limit,end=as_of,include_incomplete=include_incomplete)
        except TypeError: return self.loader(symbol,timeframe,limit=limit,end=as_of)
    @staticmethod
    def _freshness(last_ts: str, as_of: str|None):
        try:
            observed=datetime.fromisoformat(str(last_ts).replace("Z","+00:00")); ref=datetime.fromisoformat(str(as_of).replace("Z","+00:00")) if as_of else datetime.now(timezone.utc)
            if observed.tzinfo is None: observed=observed.replace(tzinfo=timezone.utc)
            if ref.tzinfo is None: ref=ref.replace(tzinfo=timezone.utc)
            return max(0.0,(ref-observed).total_seconds())
        except (TypeError,ValueError): return None
    def _aggregate(self,candles:list[dict[str,Any]],*,session_date:str|None,timeframe:str,as_of:str|None,source:str,freshness_reference:str|None=None):
        if not candles: return None
        rows=[]
        for row in candles:
            ts=str(row.get("timestamp") or "")
            if not ts: continue
            date=ts[:10]
            if session_date and date != session_date: continue
            rows.append((ts,row))
        if not rows: return None
        rows.sort(key=lambda item:item[0]); values=[row for _,row in rows]
        def number(key, fn):
            nums=[float(x[key]) for x in values if x.get(key) is not None]
            return fn(nums) if nums else None
        last_ts=rows[-1][0]; closed=all(bool(x.get("closed")) for x in values) and timeframe=="1D"
        reference=freshness_reference or as_of or datetime.now(timezone.utc).isoformat()
        return CurrentSessionState(session_date=session_date or last_ts[:10],open=number("open",lambda x:x[0]),high=number("high",max),low=number("low",min),close=number("close",lambda x:x[-1]),volume=number("volume",sum),as_of=as_of or last_ts,completeness="complete" if closed else "in_progress",source=source,timeframe=timeframe,persisted_intraday_timestamp=last_ts,freshness_seconds=self._freshness(last_ts,reference),reference_time=reference)
    @staticmethod
    def _session_date_from_as_of(as_of:str|None):
        return str(as_of)[:10] if as_of else None
    def load(self,symbol,*,timeframe="5m",session_date=None,as_of=None,limit=500):
        daily=self._fetch(symbol,"1D",as_of,limit,True)
        daily_candles=daily.get("candles") or []
        incomplete_daily=[row for row in daily_candles if not bool(row.get("closed")) and row.get("timestamp")]
        target_date=session_date or self._session_date_from_as_of(as_of)
        if target_date is None and incomplete_daily:
            target_date=max(incomplete_daily,key=lambda row:str(row.get("timestamp")))['timestamp'][:10]
        intraday=self._fetch(symbol,timeframe,as_of,limit,True)
        # Use the same partial-1D frame as the Web Screener.  Completed
        # intraday bars replace a stale stored current-day row; daily history
        # remains the fallback when no intraday session bars exist.
        intraday_candles=[row for row in (intraday.get("candles") or []) if bool(row.get("closed"))]
        daily_frame=pd.DataFrame(daily_candles)
        intraday_frame=pd.DataFrame(intraday_candles)
        partial=build_partial_daily_frame(daily_frame,intraday_frame,as_of=as_of)
        if target_date is None and not partial.empty:
            target_date=str(partial.iloc[-1]["date"])[:10]
        matching=[item for item in intraday_candles if str(item.get("timestamp", ""))[:10] == str(target_date)]
        if not matching and incomplete_daily:
            current_rows=[row for row in incomplete_daily if str(row.get("timestamp"))[:10] == str(target_date)]
            if current_rows:
                current=self._aggregate([max(current_rows,key=lambda row:str(row.get("timestamp")))],session_date=target_date,timeframe="1D",as_of=None,source="stored_daily_current_session",freshness_reference=as_of)
                if current is not None: return current
        current=partial.loc[partial["date"].astype(str).str[:10] == str(target_date)] if target_date else pd.DataFrame()
        if not current.empty:
            row=current.iloc[-1].to_dict()
            row["timestamp"] = max((str(item.get("timestamp")) for item in matching), default=str(row["date"]))
            source = "intraday_5m_aggregate" if matching else "stored_daily_current_session"
            state=self._aggregate([row],session_date=target_date,timeframe=timeframe,as_of=None,source=source,freshness_reference=as_of)
            if state is not None: return state
        raise ValueError(f"No persisted session data for {symbol}")
