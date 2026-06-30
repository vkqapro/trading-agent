"""Lightweight FastAPI backend for the React dashboard.

Serves memory/ data as JSON. Read-only; never connects to IBKR.
Run:  python dashboard_react/server.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from zoneinfo import ZoneInfo

from dashboard import data_access as da, forecast as fc
from src.config import SETTINGS
from src.data.chart_history import daily_bars_from_intraday
from src.crypto.analysis import run_crypto_analysis
from src.crypto.symbols import normalize_okx_instrument

app = FastAPI(title="Gerchik Bot Dashboard API", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def disable_dashboard_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

HERE = Path(__file__).parent
app.mount("/static", StaticFiles(directory=HERE), name="static")

ET = ZoneInfo(SETTINGS.trading_hours.timezone)
BMSB_NEAR_CROSS_THRESHOLD_PCT = 0.75
BMSB_RECENT_CROSS_DAYS = 2

def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default

def _market_session(now: datetime) -> tuple[str, str]:
    if now.weekday() >= 5:
        return "CLOSED", "Weekend"
    m = now.hour * 60 + now.minute
    if m < 240:   return "CLOSED", "Overnight"
    if m < 570:   return "PRE-MARKET", "Before 09:30"
    if m < 960:   return "OPEN", "Regular session"
    if m < 1200:  return "AFTER-HOURS", "After 16:00"
    return "CLOSED", "Overnight"


def _daily_live_frame(symbol: str) -> pd.DataFrame:
    daily = da.get_bars(symbol, "daily")
    intraday = da.get_bars(symbol, "intraday_5m")
    if intraday.empty:
        return daily

    derived_daily = daily_bars_from_intraday(intraday)
    if derived_daily.empty:
        return daily
    if daily.empty:
        return derived_daily

    derived_dates = pd.to_datetime(derived_daily["date"], errors="coerce").dt.date
    historical_dates = set(pd.to_datetime(daily["date"], errors="coerce").dropna().dt.date)
    missing_or_live = derived_daily.loc[~derived_dates.isin(historical_dates)]
    if missing_or_live.empty:
        return daily
    return pd.concat([daily, missing_or_live], ignore_index=True).sort_values("date")


def _weekly_live_frame(symbol: str) -> pd.DataFrame:
    """Return weekly OHLCV with current-week daily/live data stitched in."""
    daily = _daily_live_frame(symbol)
    if daily.empty:
        return da.get_bars(symbol, "weekly")
    frame = daily.set_index("date").sort_index()
    return (
        frame.resample("W-FRI")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )


def _bmsb_scan_symbol(symbol: str, today) -> dict | None:
    weekly = _weekly_live_frame(symbol)
    if weekly.empty or len(weekly) < 21:
        return None

    weekly = weekly.sort_values("date").tail(260).reset_index(drop=True)
    close = pd.to_numeric(weekly["close"], errors="coerce")
    weekly["sma20"] = close.rolling(20).mean()
    weekly["ema21"] = close.ewm(span=21, adjust=False).mean()
    ready = weekly.dropna(subset=["sma20", "ema21", "close"])
    if len(ready) < 2:
        return None

    prev = ready.iloc[-2]
    curr = ready.iloc[-1]
    prev_diff = float(prev["ema21"] - prev["sma20"])
    curr_diff = float(curr["ema21"] - curr["sma20"])
    close_price = float(curr["close"])
    if close_price <= 0:
        return None

    current_gap_pct = abs(curr_diff) / close_price * 100.0
    prev_gap_pct = abs(prev_diff) / float(prev["close"]) * 100.0 if float(prev["close"]) > 0 else None
    latest_daily = _daily_live_frame(symbol)
    latest_session = None
    if not latest_daily.empty:
        latest_session = pd.to_datetime(latest_daily["date"], errors="coerce").max()
    latest_session_date = latest_session.date() if pd.notna(latest_session) else today
    recent_cutoff = today - timedelta(days=BMSB_RECENT_CROSS_DAYS)

    signal = None
    side = None
    urgency = None
    if prev_diff <= 0 < curr_diff and latest_session_date >= recent_cutoff:
        signal = "CROSSED_LONG"
        side = "LONG"
        urgency = "crossed"
    elif prev_diff >= 0 > curr_diff and latest_session_date >= recent_cutoff:
        signal = "CROSSED_EXIT"
        side = "EXIT"
        urgency = "crossed"
    elif current_gap_pct <= BMSB_NEAR_CROSS_THRESHOLD_PCT:
        signal = "NEAR_LONG" if curr_diff <= 0 else "NEAR_EXIT"
        side = "LONG" if curr_diff <= 0 else "EXIT"
        urgency = "near"

    if not signal:
        return None

    return {
        "symbol": symbol,
        "signal": signal,
        "side": side,
        "urgency": urgency,
        "last_price": round(close_price, 4),
        "ema21": round(float(curr["ema21"]), 4),
        "sma20": round(float(curr["sma20"]), 4),
        "gap": round(curr_diff, 4),
        "gap_pct": round(current_gap_pct, 4),
        "previous_gap_pct": round(prev_gap_pct, 4) if prev_gap_pct is not None else None,
        "cross_date": str(latest_session_date),
        "weekly_bar": str(pd.to_datetime(curr["date"]).date()),
        "threshold_pct": BMSB_NEAR_CROSS_THRESHOLD_PCT,
    }


@app.get("/")
def index():
    return FileResponse(HERE / "index.html")


@app.get("/api/meta")
def api_meta():
    now = datetime.now(ET)
    session, session_detail = _market_session(now)
    health = _safe(da.source_health, [])
    return {
        "clock": now.strftime("%H:%M:%S"),
        "session": session,
        "session_detail": session_detail,
        "paper": SETTINGS.paper_trading,
        "dry_run": SETTINGS.dry_run_mode,
        "health": [asdict(h) | {"path": str(h.path), "updated_at": h.updated_at.isoformat() if h.updated_at else None} for h in health],
    }


@app.get("/api/dashboard")
def api_dashboard():
    watchlist = _safe(da.load_watchlist, {})
    decisions = _safe(da.load_daily_decisions, {})
    attempts = _safe(lambda: da.all_decision_attempts(decisions), [])
    positions = _safe(da.load_tracked_positions, [])
    snapshot = _safe(da.load_intraday_snapshot, {})
    news_items = _safe(lambda: da.blocked_news_summary(watchlist), [])

    from collections import Counter
    signals = Counter(str(a.get("signal", "NONE")).upper() for a in attempts)
    buys, sells = signals["BUY"], signals["SELL"]
    tradable = sum(1 for p in watchlist.values() if isinstance(p, dict) and p.get("levels") and not p.get("news_blocked"))
    blocked = sum(1 for p in watchlist.values() if isinstance(p, dict) and p.get("news_blocked"))
    executed = snapshot.get("executed", []) if isinstance(snapshot, dict) else []
    skipped  = snapshot.get("skipped", []) if isinstance(snapshot, dict) else []
    fill_total = len(executed) + len(skipped)
    fill_rate = len(executed) / fill_total if fill_total else 0.0

    recent_attempts = sorted(attempts, key=lambda a: str(a.get("timestamp", "")), reverse=True)[:14]
    opp_rows = []
    for sym, plan in sorted((watchlist or {}).items()):
        if not isinstance(plan, dict): continue
        levels = plan.get("levels", [])
        spacing = plan.get("level_spacing", {}) or {}
        blocked_sym = bool(plan.get("news_blocked"))
        status = "BLOCKED" if blocked_sym else ("READY" if levels else "NO LEVELS")
        opp_rows.append({
            "sym": sym, "status": status, "trade_levels": len(levels),
            "room": spacing.get("reason", "-"),
            "daily_atr": plan.get("daily_atr"),
            "price": spacing.get("current_price"),
            "strategy": (levels[0].get("strategy") if levels else "-"),
        })

    return {
        "metrics": {
            "watchlist": len(watchlist),
            "trade_ready": tradable,
            "news_blocked": blocked,
            "attempts": len(attempts),
            "action_signals": buys + sells,
            "buys": buys, "sells": sells,
            "fill_rate": fill_rate,
            "fill_executed": len(executed),
            "fill_total": fill_total,
        },
        "news_items": news_items,
        "symbols": sorted((watchlist or {}).keys()),
        "opp_rows": opp_rows[:25],
        "attempt_log": recent_attempts,
        "risk": {
            "risk_per_trade": SETTINGS.risk.risk_per_trade,
            "max_daily_loss_pct": SETTINGS.risk.max_daily_loss_pct,
            "min_reward_risk_ratio": SETTINGS.risk.min_reward_risk_ratio,
            "max_positions": SETTINGS.risk.max_positions,
            "max_spread_pct": SETTINGS.risk.max_spread_pct,
            "max_open_risk_pct": SETTINGS.risk.max_open_risk_pct,
        },
    }


@app.get("/api/strategy")
def api_strategy():
    index = _safe(da.bars_index, {})
    symbols = sorted(
        symbol for symbol, frames in (index or {}).items()
        if isinstance(frames, dict) and ("daily" in frames or "weekly" in frames)
    )
    today = datetime.now(ET).date()
    rows = [
        row for symbol in symbols
        if (row := _safe(lambda s=symbol: _bmsb_scan_symbol(s, today), None)) is not None
    ]
    urgency_rank = {"crossed": 0, "near": 1}
    side_rank = {"LONG": 0, "EXIT": 1}
    rows.sort(key=lambda r: (
        urgency_rank.get(str(r.get("urgency")), 9),
        side_rank.get(str(r.get("side")), 9),
        float(r.get("gap_pct", 999.0)),
        str(r.get("symbol", "")),
    ))
    return {
        "strategy": "BMSB Strategy 1",
        "description": "Weekly 21 EMA / 20 SMA cross monitor. Near-cross means EMA/SMA gap is within the configured threshold.",
        "threshold_pct": BMSB_NEAR_CROSS_THRESHOLD_PCT,
        "recent_days": BMSB_RECENT_CROSS_DAYS,
        "symbols_scanned": len(symbols),
        "symbols": symbols,
        "matches": len(rows),
        "crossed": sum(1 for row in rows if row.get("urgency") == "crossed"),
        "near": sum(1 for row in rows if row.get("urgency") == "near"),
        "rows": rows,
    }


@app.get("/api/preopen")
def api_preopen():
    watchlist = _safe(da.load_watchlist, {})
    rows = []
    for sym, plan in sorted((watchlist or {}).items()):
        if not isinstance(plan, dict): continue
        spacing = plan.get("level_spacing", {}) or {}
        levels = plan.get("levels", [])
        blocked = bool(plan.get("news_blocked"))
        rows.append({
            "symbol": sym,
            "status": "BLOCKED" if blocked else ("READY" if levels else "NO LEVELS"),
            "price": spacing.get("current_price"),
            "daily_atr": plan.get("daily_atr"),
            "technical_atr": plan.get("technical_atr"),
            "trade_levels": len(levels),
            "room": spacing.get("reason", "-"),
            "news": "BLOCKED" if blocked else "CLEAR",
        })
    return {"rows": rows, "watchlist": watchlist}


@app.get("/api/preopen/{symbol}")
def api_preopen_symbol(symbol: str):
    symbol = symbol.upper()
    watchlist = _safe(da.load_watchlist, {})
    plan = (watchlist or {}).get(symbol)
    if plan is None:
        raise HTTPException(404, f"{symbol} not in watchlist")
    spacing = plan.get("level_spacing", {}) or {}
    raw_levels = plan.get("raw_levels", [])
    trade_levels = plan.get("levels", [])
    return {
        "symbol": symbol,
        "price": spacing.get("current_price"),
        "daily_atr": plan.get("daily_atr"),
        "technical_atr": plan.get("technical_atr"),
        "trade_levels": trade_levels,
        "raw_levels": raw_levels,
        "room": spacing.get("reason", "-"),
        "news": "BLOCKED" if plan.get("news_blocked") else "CLEAR",
        "headlines": plan.get("matched_headlines", []),
        "security_type": plan.get("security_type", "-"),
    }


@app.get("/api/bars/{symbol}/{timeframe}")
def api_bars(symbol: str, timeframe: str):
    symbol = symbol.upper()

    def _hourly_bars() -> pd.DataFrame:
        intraday = da.get_bars(symbol, "intraday_5m")
        if intraday.empty:
            return intraday
        frame = intraday.set_index("date").sort_index()
        return (
            frame.resample("60min", origin="start_day", offset="30min")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna(subset=["open", "high", "low", "close"])
            .reset_index()
        )

    def _four_hour_bars() -> pd.DataFrame:
        stored = da.get_bars(symbol, "intraday_4h")
        if not stored.empty:
            return stored
        intraday = da.get_bars(symbol, "intraday_5m")
        if intraday.empty:
            return intraday
        frame = intraday.set_index("date").sort_index()
        return (
            frame.resample("240min", origin="start_day", offset="30min")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna(subset=["open", "high", "low", "close"])
            .reset_index()
        )

    def _weekly_bars() -> pd.DataFrame:
        stored = da.get_bars(symbol, "weekly")
        if not stored.empty:
            return stored
        daily = da.get_bars(symbol, "daily")
        if daily.empty:
            return daily
        frame = daily.set_index("date").sort_index()
        return (
            frame.resample("W-FRI")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna(subset=["open", "high", "low", "close"])
            .reset_index()
        )

    def _live_daily_bars() -> pd.DataFrame:
        daily = da.get_bars(symbol, "daily")
        intraday = da.get_bars(symbol, "intraday_5m")
        if intraday.empty:
            return daily

        derived_daily = daily_bars_from_intraday(intraday)
        if derived_daily.empty:
            return daily
        if daily.empty:
            return derived_daily

        derived_dates = pd.to_datetime(derived_daily["date"], errors="coerce").dt.date
        historical_dates = set(pd.to_datetime(daily["date"], errors="coerce").dropna().dt.date)
        missing_or_live = derived_daily.loc[~derived_dates.isin(historical_dates)]
        if missing_or_live.empty:
            return daily
        return pd.concat([daily, missing_or_live], ignore_index=True).sort_values("date")

    if timeframe == "intraday_1h":
        df = _safe(_hourly_bars, None)
    elif timeframe == "intraday_4h":
        df = _safe(_four_hour_bars, None)
    elif timeframe == "daily_live":
        df = _safe(_live_daily_bars, None)
    elif timeframe == "weekly":
        df = _safe(_weekly_bars, None)
    else:
        df = _safe(lambda: da.get_bars(symbol, timeframe), None)
    if df is None or df.empty:
        return {"bars": [], "stale": True, "last_date": None}
    chart_limits = {
        "intraday_5m": 500,
        "intraday_1h": 500,
        "intraday_4h": 500,
        "daily_live": 600,
        "daily": 600,
        "weekly": 260,
    }
    df = df.tail(chart_limits.get(timeframe, 300))
    last_date = str(df["date"].iloc[-1]) if "date" in df.columns else None
    # stale = last bar is genuinely missing sessions.
    # Count how many weekdays (Mon-Fri) lie between last_bar and today — if more
    # than 1 then at least one trading session was skipped (we can't know about
    # holidays without a full calendar, so we allow 1 extra day as holiday buffer).
    stale = False
    if last_date:
        from datetime import date, timedelta
        try:
            last_dt = date.fromisoformat(str(last_date)[:10])
            today = datetime.now(ET).date()
            # count weekdays strictly between last_dt and today (exclusive of both)
            weekdays_gap = 0
            d = last_dt + timedelta(days=1)
            while d < today:
                if d.weekday() < 5:
                    weekdays_gap += 1
                d += timedelta(days=1)
            # Weekly bars naturally lag within the current week.
            stale = weekdays_gap > (7 if timeframe == "weekly" else 1)
        except Exception:
            pass
    return {
        "bars": df.rename(columns={"date": "t"}).to_dict(orient="records"),
        "stale": stale,
        "last_date": last_date,
    }


@app.get("/api/crypto")
def api_crypto():
    state = _safe(da.load_crypto_dashboard_state, {})
    configured = _safe(da.load_crypto_symbols, [])
    watchlist = state.get("watchlist", {}) if isinstance(state, dict) else {}
    index = _safe(da.crypto_bars_index, {})
    rows = []
    configured_by_inst = {row.get("inst_id"): row for row in configured if isinstance(row, dict)}
    symbols = sorted(set(configured_by_inst) | set(watchlist or {}) | set(index or {}))
    for symbol in symbols:
        plan = watchlist.get(symbol, {}) if isinstance(watchlist, dict) else {}
        frames = index.get(symbol, {}) if isinstance(index, dict) else {}
        trade_levels = plan.get("trade_levels", []) if isinstance(plan, dict) else []
        raw_levels = plan.get("raw_levels", []) if isinstance(plan, dict) else []
        latest = None
        for tf in ("intraday_5m", "intraday_1h", "intraday_4h", "daily"):
            latest = (frames.get(tf, {}) or {}).get("last_bar") if isinstance(frames.get(tf, {}), dict) else latest
            if latest:
                break
        rows.append({
            "symbol": symbol,
            "raw": configured_by_inst.get(symbol, {}).get("raw"),
            "inst_type": configured_by_inst.get(symbol, {}).get("inst_type"),
            "ready": bool(plan.get("ready")) if isinstance(plan, dict) else False,
            "price": plan.get("price") if isinstance(plan, dict) else None,
            "daily_atr": plan.get("daily_atr") if isinstance(plan, dict) else None,
            "trade_levels": len(trade_levels),
            "raw_levels": len(raw_levels),
            "last_bar": latest,
            "bars": plan.get("bars", {}) if isinstance(plan, dict) else {},
            "reason": plan.get("reason") if isinstance(plan, dict) else None,
        })
    ready = sum(1 for row in rows if row.get("ready"))
    return {
        "updated_at": state.get("updated_at") if isinstance(state, dict) else None,
        "symbols": symbols,
        "rows": rows,
        "errors": state.get("errors", []) if isinstance(state, dict) else [],
        "metrics": {
            "configured": len(configured),
            "tracked": len(rows),
            "ready": ready,
            "with_trade_levels": sum(1 for row in rows if int(row.get("trade_levels") or 0) > 0),
        },
    }


@app.get("/api/crypto/{symbol}")
def api_crypto_symbol(symbol: str):
    inst_id = normalize_okx_instrument(symbol)
    state = _safe(da.load_crypto_dashboard_state, {})
    watchlist = state.get("watchlist", {}) if isinstance(state, dict) else {}
    plan = watchlist.get(inst_id)
    if not isinstance(plan, dict):
        raise HTTPException(404, f"{inst_id} is not analyzed yet")
    return plan


@app.get("/api/crypto/bars/{symbol}/{timeframe}")
def api_crypto_bars(symbol: str, timeframe: str):
    inst_id = normalize_okx_instrument(symbol)
    tf = {
        "daily_live": "daily",
        "intraday_4h": "intraday_4h",
        "intraday_1h": "intraday_1h",
        "intraday_5m": "intraday_5m",
        "daily": "daily",
    }.get(timeframe, timeframe)
    df = _safe(lambda: da.get_crypto_bars(inst_id, tf), None)
    if df is None or df.empty:
        return {"bars": [], "stale": True, "last_date": None}
    limits = {"intraday_5m": 500, "intraday_1h": 500, "intraday_4h": 500, "daily": 600, "daily_live": 600}
    df = df.tail(limits.get(tf, 300))
    last_date = str(df["date"].iloc[-1]) if "date" in df.columns else None
    return {
        "bars": df.rename(columns={"date": "t"}).to_dict(orient="records"),
        "stale": False,
        "last_date": last_date,
    }


@app.post("/api/crypto/analyze")
async def api_crypto_analyze(body: dict | None = None):
    try:
        symbols = None
        if body and body.get("symbols"):
            raw = body.get("symbols")
            symbols = raw if isinstance(raw, list) else [raw]
        result = run_crypto_analysis(symbols)
        return {
            "ok": True,
            "updated_at": result.get("updated_at"),
            "symbols": result.get("symbols", []),
            "errors": result.get("errors", []),
        }
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/api/intraday")
def api_intraday():
    snapshot = _safe(da.load_intraday_snapshot, {})
    decisions = _safe(da.load_daily_decisions, {})
    attempts = _safe(lambda: da.all_decision_attempts(decisions), [])
    from collections import Counter
    signals = Counter(str(a.get("signal", "NONE")).upper() for a in attempts)
    decision_map = decisions.get("decisions", {}) if isinstance(decisions, dict) else {}
    symbols = sorted(decision_map)
    scans = snapshot.get("scans", []) if isinstance(snapshot, dict) else []
    latest_scan = scans[-1] if scans else {}
    return {
        "metrics": {
            "date": str(decisions.get("date", "-")) if isinstance(decisions, dict) else "-",
            "symbols_evaluated": len(symbols),
            "attempts": len(attempts),
            "buys": signals["BUY"], "sells": signals["SELL"],
            "latest_signals": latest_scan.get("signals_detected", 0),
        },
        "attempts": attempts,
        "symbols": symbols,
    }


@app.get("/api/trades")
def api_trades():
    positions = _safe(da.load_tracked_positions, [])
    snapshot = _safe(da.load_intraday_snapshot, {})
    requests = _safe(da.load_order_requests, [])
    from src.execution import order_requests as oq
    executed = snapshot.get("executed", []) if isinstance(snapshot, dict) else []
    skipped  = snapshot.get("skipped", []) if isinstance(snapshot, dict) else []
    sections = _safe(lambda: da.latest_trade_log_sections(12), [])
    worker_alive = _safe(oq.worker_is_alive, False)
    worker_age = _safe(oq.worker_age_seconds, None)
    open_req = sum(1 for r in requests if str(r.get("status")) in {"pending", "processing"})
    return {
        "positions": positions,
        "executed": executed,
        "skipped": skipped,
        "order_requests": requests,
        "open_requests": open_req,
        "worker_alive": worker_alive,
        "worker_age": worker_age,
        "trade_log": sections,
    }


@app.post("/api/order/place")
async def api_order_place(body: dict):
    from src.execution import order_requests as oq
    from src.config import SETTINGS
    if not body.get("symbol"):
        raise HTTPException(400, "symbol is required")
    try:
        request_id = oq.submit_place(body, live=False)
        return {"ok": True, "id": request_id, "dry_run": SETTINGS.dry_run_mode, "message": f"Queued place order for {body['symbol']} — paper only, respects DRY_RUN."}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.post("/api/order/close")
async def api_order_close(body: dict):
    from src.execution import order_requests as oq
    from src.config import SETTINGS
    symbol = str(body.get("symbol", "")).upper()
    if not symbol:
        raise HTTPException(400, "symbol is required")
    try:
        request_id = oq.submit_close(symbol, live=False)
        return {"ok": True, "id": request_id, "dry_run": SETTINGS.dry_run_mode, "message": f"Queued close for {symbol} — paper only, respects DRY_RUN."}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/api/reports")
def api_reports():
    infos = _safe(da.list_report_info, [])
    return {"reports": [
        {"file": r.path.name, "kind": r.kind, "date": r.report_date,
         "updated_at": r.updated_at.isoformat(), "size": r.size_bytes}
        for r in infos
    ]}


@app.post("/api/forecast/run")
async def api_forecast_run(body: dict):
    try:
        scenario = fc.ForecastScenario(
            account_equity=float(body.get("equity", 100000)),
            cash_available=float(body.get("cash", 100000)),
            daily_realized_pnl=float(body.get("dailyPnl", 0)),
            assumed_spread_pct=float(body.get("spread", 0.05)) / 100,
            risk_per_trade=float(body.get("riskTrade", 1.0)) / 100,
            max_daily_loss_pct=float(body.get("maxDailyLoss", 2.0)) / 100,
            min_reward_risk_ratio=float(body.get("minRR", 3.0)),
            max_positions=int(body.get("maxPositions", 5)),
            max_spread_pct=float(body.get("maxSpread", 0.20)) / 100,
            max_open_risk_pct=float(body.get("maxOpenRisk", 6.0)) / 100,
            max_position_value=float(body.get("maxPosValue", 50000)),
            min_projected_entry_distance_atr=float(body.get("minEntryAtr", 1.0)),
            max_projected_entry_distance_atr=float(body.get("maxEntryAtr", 2.0)),
        )
        watchlist = _safe(da.load_watchlist, {})
        positions = _safe(da.load_tracked_positions, [])
        projected = _safe(lambda: fc.projected_level_candidates(watchlist), [])
        active, warns = _safe(lambda: fc.replay_active_candidates(watchlist, da.get_bars), ([], []))
        candidates = active + projected
        result = fc.run_forecast(candidates, scenario, positions)
        # make serialisable
        import math
        def _clean(v):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                return None
            return v
        def _fix(obj):
            if isinstance(obj, dict):  return {k: _fix(v) for k, v in obj.items()}
            if isinstance(obj, list):  return [_fix(i) for i in obj]
            return _clean(obj)
        return _fix(result)
    except Exception as exc:
        raise HTTPException(500, str(exc))


if __name__ == "__main__":
    uvicorn.run("dashboard_react.server:app", host="127.0.0.1", port=8550, reload=False)
