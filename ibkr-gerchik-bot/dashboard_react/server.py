"""Lightweight FastAPI backend for the React dashboard.

Serves memory/ data as JSON. The dashboard never connects to IBKR directly;
mutating workflows are handed off to bot jobs.
Run:  python dashboard_react/server.py
"""
from __future__ import annotations

import os
import json
import subprocess
import sys
import time
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from zoneinfo import ZoneInfo

from dashboard import data_access as da, forecast as fc
from src.config import SETTINGS
from src.data.chart_history import daily_bars_from_intraday
from src.crypto.analysis import run_crypto_analysis
from src.crypto.config import CRYPTO_SETTINGS
from src.crypto.okx_client import OKXClient
from src.crypto.symbols import add_crypto_symbol, default_instrument_type, normalize_okx_instrument
from src.crypto.tradingview_webhook import (
    enqueue_tradingview_webhook,
    load_tradingview_state,
    process_queued_tradingview_execution,
)
from src.config import fx_pair_components
from src.forex.tradingview_webhook import handle_forex_tradingview_webhook, load_forex_tradingview_state
from src.journal.broker_reconcile import reconcile_ibkr_open_orders
from src.journal.order_journal import load_order_journal, save_review
from src.stocks.tradingview_webhook import handle_stock_tradingview_webhook, load_stock_tradingview_state
from dashboard_react.market_screener import ScreenerParams, run_market_screener

app = FastAPI(title="Vitaly's Trading Bot Dashboard API", docs_url=None, redoc_url=None)
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


def _onboard_client_id(symbol: str) -> str:
    configured = os.environ.get("ONBOARD_SYMBOL_CLIENT_ID", "").strip()
    if configured:
        return configured
    seed = sum((index + 1) * ord(char) for index, char in enumerate(symbol.upper()))
    timestamp = int(datetime.now(ET).timestamp())
    return str(1000 + ((timestamp + seed) % 8000))
BMSB_NEAR_CROSS_THRESHOLD_PCT = 0.75
BMSB_RECENT_CROSS_DAYS = 2
BMSB2_RECLAIM_TOLERANCE_PCT = 0.5
BMSB2_NEAR_TRIGGER_THRESHOLD_PCT = 0.75

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


def _bmsb_scan_symbol(symbol: str, today, include_neutral: bool = False) -> dict | None:
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
        if not include_neutral:
            return None
        signal = "NO_SIGNAL"
        side = "NONE"
        urgency = "none"

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
        "entry_level": None,
        "exit_level": None,
        "trigger_level": None,
        "trigger_gap_pct": round(current_gap_pct, 4),
        "cross_date": str(latest_session_date),
        "weekly_bar": str(pd.to_datetime(curr["date"]).date()),
        "threshold_pct": BMSB_NEAR_CROSS_THRESHOLD_PCT,
    }


def _bmsb_strategy2_scan_symbol(symbol: str, today, include_neutral: bool = False) -> dict | None:
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

    tolerance_mult = BMSB2_RECLAIM_TOLERANCE_PCT / 100.0
    ready = ready.copy()
    ready["band_upper"] = ready[["sma20", "ema21"]].max(axis=1)
    ready["band_lower"] = ready[["sma20", "ema21"]].min(axis=1)
    ready["entry_level"] = ready["band_upper"] * (1.0 - tolerance_mult)
    ready["exit_level"] = ready["band_lower"] * (1.0 + tolerance_mult)

    prev = ready.iloc[-2]
    curr = ready.iloc[-1]
    close_price = float(curr["close"])
    if close_price <= 0:
        return None

    latest_daily = _daily_live_frame(symbol)
    latest_session = None
    if not latest_daily.empty:
        latest_session = pd.to_datetime(latest_daily["date"], errors="coerce").max()
    latest_session_date = latest_session.date() if pd.notna(latest_session) else today
    recent_cutoff = today - timedelta(days=BMSB_RECENT_CROSS_DAYS)

    entry_level = float(curr["entry_level"])
    exit_level = float(curr["exit_level"])
    prev_entry_level = float(prev["entry_level"])
    prev_exit_level = float(prev["exit_level"])
    prev_close = float(prev["close"])

    long_entry = (
        prev_close <= prev_entry_level
        and close_price > entry_level
        and latest_session_date >= recent_cutoff
    )
    long_exit = (
        prev_close >= prev_exit_level
        and close_price < exit_level
        and latest_session_date >= recent_cutoff
    )

    signal = None
    side = None
    urgency = None
    trigger_level = None
    trigger_gap_pct = None

    if long_entry:
        signal = "BMSB2_LONG_ENTRY"
        side = "LONG"
        urgency = "crossed"
        trigger_level = entry_level
        trigger_gap_pct = 0.0
    elif long_exit:
        signal = "BMSB2_EXIT"
        side = "EXIT"
        urgency = "crossed"
        trigger_level = exit_level
        trigger_gap_pct = 0.0
    else:
        neutral_candidates = [
            ("BMSB2_NEAR_ENTRY", "LONG", entry_level, abs(entry_level - close_price) / close_price * 100.0),
            ("BMSB2_NEAR_EXIT", "EXIT", exit_level, abs(close_price - exit_level) / close_price * 100.0),
        ]
        candidates = []
        if close_price <= entry_level:
            distance = (entry_level - close_price) / close_price * 100.0
            candidates.append(("BMSB2_NEAR_ENTRY", "LONG", entry_level, distance))
        if close_price >= exit_level:
            distance = (close_price - exit_level) / close_price * 100.0
            candidates.append(("BMSB2_NEAR_EXIT", "EXIT", exit_level, distance))
        candidates = [item for item in candidates if item[3] <= BMSB2_NEAR_TRIGGER_THRESHOLD_PCT]
        if candidates:
            signal, side, trigger_level, trigger_gap_pct = min(candidates, key=lambda item: item[3])
            urgency = "near"

    ema21 = float(curr["ema21"])
    sma20 = float(curr["sma20"])
    band_gap_pct = abs(ema21 - sma20) / close_price * 100.0

    if not signal:
        if not include_neutral:
            return None
        _, nearest_side, nearest_level, nearest_gap_pct = min(neutral_candidates, key=lambda item: item[3])
        signal = "NO_SIGNAL"
        side = "NONE"
        urgency = "none"
        trigger_level = nearest_level
        trigger_gap_pct = nearest_gap_pct

    return {
        "symbol": symbol,
        "signal": signal,
        "side": side,
        "urgency": urgency,
        "last_price": round(close_price, 4),
        "ema21": round(ema21, 4),
        "sma20": round(sma20, 4),
        "gap": round(ema21 - sma20, 4),
        "gap_pct": round(band_gap_pct, 4),
        "entry_level": round(entry_level, 4),
        "exit_level": round(exit_level, 4),
        "trigger_level": round(float(trigger_level), 4) if trigger_level is not None else None,
        "trigger_gap_pct": round(float(trigger_gap_pct), 4) if trigger_gap_pct is not None else None,
        "nearest_side": nearest_side if signal == "NO_SIGNAL" else side,
        "cross_date": str(latest_session_date),
        "weekly_bar": str(pd.to_datetime(curr["date"]).date()),
        "threshold_pct": BMSB2_NEAR_TRIGGER_THRESHOLD_PCT,
        "tolerance_pct": BMSB2_RECLAIM_TOLERANCE_PCT,
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


@app.post("/api/system/hard-reset")
def api_system_hard_reset():
    reset_script = ROOT / "run_dashboard_hard_reset.cmd"
    if not reset_script.exists():
        raise HTTPException(500, f"Reset script not found: {reset_script}")
    try:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            ["cmd", "/c", str(reset_script)],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
        )
        return {
            "ok": True,
            "message": "Hard reset started. Dashboard services will close and reopen in a few seconds.",
        }
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.post("/api/system/fetch-ibkr-candles")
def api_system_fetch_ibkr_candles():
    """Queue a one-shot IBKR candle refresh, ignoring normal market-hours gating."""
    try:
        SETTINGS.paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        log_path = SETTINGS.paths.runtime_dir / "manual_market_data_fetch.log"
        client_id = os.environ.get("MANUAL_MARKET_DATA_CLIENT_ID", "117").strip() or "117"
        cmd = [
            sys.executable,
            "-m",
            "src.main",
            "--job",
            "market_data",
            "--once",
            "--force",
            "--interval-seconds",
            "300",
            "--client-id",
            str(client_id),
        ]
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n--- {datetime.now(ET).isoformat()} queued {' '.join(cmd)} ---\n")
            process = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        return {
            "ok": True,
            "pid": process.pid,
            "client_id": client_id,
            "log": str(log_path),
            "message": "Queued manual IBKR candle refresh. It will fetch latest watchlist bars even outside the normal market-data window.",
        }
    except Exception as exc:
        raise HTTPException(500, str(exc))


def _pid_alive(pid: object) -> bool:
    try:
        pid_int = int(pid)
        if pid_int <= 0:
            return False
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid_int)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        os.kill(pid_int, 0)
        return True
    except PermissionError:
        return True
    except Exception:
        return False


def _read_json_file(path: Path) -> dict | None:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _iso_age_seconds(value: object) -> float | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _service_status(label: str, ok: bool, *, age: float | None = None, detail: str = "", status: str | None = None, icon: str = "settings_ethernet") -> dict:
    if status is None:
        status = "online" if ok else "offline"
    return {
        "label": label,
        "status": status,
        "ok": bool(ok),
        "age_seconds": None if age is None else round(float(age), 1),
        "detail": detail,
        "icon": icon,
    }


def _lock_status(path: Path, *, stale_after_seconds: float) -> tuple[bool, float | None, str]:
    if not path.exists():
        return False, None, "lock not present"
    try:
        age = max(0.0, time.time() - path.stat().st_mtime)
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        pid = text.split("|", 1)[0].strip() if text else ""
        alive = _pid_alive(pid) if pid else age <= stale_after_seconds
        ok = alive and age <= stale_after_seconds
        detail = f"pid {pid} · {int(age)}s ago" if pid else f"{int(age)}s ago"
        if not ok and age > stale_after_seconds:
            detail = f"stale · {detail}"
        detail = detail.replace("\ufffd", "-")
        return ok, age, detail
    except Exception as exc:
        return False, None, f"lock read error: {exc}"


def _market_collector_window_status(now: datetime | None = None) -> str:
    current = now or datetime.now(ET)
    if current.weekday() >= 5:
        return "waiting"
    start = current.replace(
        hour=SETTINGS.trading_hours.market_open_hour,
        minute=SETTINGS.trading_hours.market_open_minute,
        second=0,
        microsecond=0,
    )
    end = current.replace(
        hour=SETTINGS.trading_hours.market_data_collector_end_hour,
        minute=SETTINGS.trading_hours.market_data_collector_end_minute,
        second=0,
        microsecond=0,
    )
    return "online" if start <= current <= end else "waiting"


def _latest_job_log_status(job_name: str) -> dict:
    runtime_dir = SETTINGS.paths.runtime_dir
    newest: dict = {"status": "unknown", "ok": False, "age": None, "detail": "no job log found"}
    try:
        logs = sorted(runtime_dir.glob("application.*.log"), key=lambda path: path.stat().st_mtime, reverse=True)
    except Exception:
        return newest
    marker = f"job={job_name}"
    for path in logs:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if marker not in text:
            continue
        stat = path.stat()
        age = max(0.0, time.time() - stat.st_mtime)
        pid_match = re.search(rf"job={re.escape(job_name)}\b[^\n]*\bpid=(\d+)", text)
        pid = pid_match.group(1) if pid_match else ""
        completed = (
            "Job completed:" in text
            and (
                f"'job': '{job_name}'" in text
                or f'"job": "{job_name}"' in text
                or f'"job":"{job_name}"' in text
            )
        )
        if completed:
            return {
                "status": "complete",
                "ok": age < 24 * 60 * 60,
                "age": age,
                "detail": f"completed {int(age)}s ago" + (f" - pid {pid}" if pid else ""),
                "pid": pid,
                "path": str(path),
            }
        alive = _pid_alive(pid) if pid else False
        if alive:
            return {
                "status": "running",
                "ok": True,
                "age": age,
                "detail": f"pid {pid} - log {int(age)}s ago",
                "pid": pid,
                "path": str(path),
            }
        newest = {
            "status": "complete" if age < 24 * 60 * 60 else "stale",
            "ok": age < 24 * 60 * 60,
            "age": age,
            "detail": f"last log {int(age)}s ago" + (f" - pid {pid} ended" if pid else ""),
            "pid": pid,
            "path": str(path),
        }
        break
    return newest


def _premarket_status() -> dict:
    status = _latest_job_log_status("premarket")
    if status.get("status") == "running":
        return status

    today = datetime.now(ET).strftime("%Y%m%d")
    report = SETTINGS.paths.reports_dir / f"premarket_levels_{today}.xlsx"
    if report.exists():
        age = max(0.0, time.time() - report.stat().st_mtime)
        return {
            "status": "complete",
            "ok": True,
            "age": age,
            "detail": f"today's report ready - {int(age)}s ago",
        }

    current = datetime.now(ET)
    if current.weekday() < 5 and current.hour < SETTINGS.trading_hours.market_open_hour:
        return {
            "status": "pending",
            "ok": False,
            "age": status.get("age"),
            "detail": "no completed pre-open report yet",
        }
    return status


@app.get("/api/services")
def api_services():
    from src.execution import order_requests as oq

    execute_age = _safe(oq.worker_age_seconds, None)
    execute_ok = bool(_safe(oq.worker_is_alive, False))
    execute_detail = (
        f"heartbeat {int(execute_age)}s ago"
        if execute_age is not None
        else "no heartbeat"
    )
    premarket = _premarket_status()

    market_lock = SETTINGS.paths.runtime_dir / "market_data_collector.lock"
    _, market_age, market_lock_detail = _lock_status(market_lock, stale_after_seconds=900)
    market_pid = None
    if market_lock.exists():
        try:
            market_pid = market_lock.read_text(encoding="utf-8", errors="ignore").strip().split("|", 1)[0].strip()
        except Exception:
            market_pid = None
    market_process_alive = _pid_alive(market_pid) if market_pid else False
    market_window_status = _market_collector_window_status()
    market_status = market_window_status if market_process_alive else "offline"
    market_ok = market_process_alive
    market_alive_detail = (
        f"pid {market_pid} - startup lock {int(market_age)}s old"
        if market_pid and market_age is not None
        else "collector process alive"
    )
    market_detail = (
        f"waiting for 09:30 ET - {market_alive_detail}"
        if market_process_alive and market_window_status == "waiting"
        else f"collector alive - {market_alive_detail}"
        if market_process_alive
        else market_lock_detail
    )

    crypto_heartbeat_path = CRYPTO_SETTINGS.memory_dir / "runtime" / "worker.json"
    crypto_hb = _read_json_file(crypto_heartbeat_path)
    crypto_age = _iso_age_seconds((crypto_hb or {}).get("updated_at"))
    crypto_interval = float((crypto_hb or {}).get("interval_seconds") or CRYPTO_SETTINGS.collect_interval_seconds or 300)
    crypto_stale_after = max(crypto_interval * 3, 900.0)
    crypto_pid = (crypto_hb or {}).get("pid")
    crypto_process_alive = _pid_alive(crypto_pid) if crypto_pid else False
    crypto_fresh = crypto_age is not None and crypto_age <= crypto_stale_after
    crypto_raw_status = str((crypto_hb or {}).get("status") or "unknown").lower()
    crypto_status = (
        crypto_raw_status
        if bool(crypto_hb) and crypto_process_alive and crypto_fresh
        else "stale"
        if bool(crypto_hb) and crypto_process_alive
        else "offline"
    )
    crypto_ok = crypto_status in {"ok", "sleeping", "running"}
    crypto_detail = (
        f"{(crypto_hb or {}).get('status', 'unknown')} · pid {crypto_pid} · {int(crypto_age)}s ago"
        if crypto_age is not None
        else "no heartbeat"
    )

    crypto_detail = crypto_detail.replace("\ufffd", "-")

    ibkr_ok = execute_ok or market_ok
    ibkr_detail = (
        "via execute worker / market-data collector"
        if execute_ok and market_ok
        else "via execute worker"
        if execute_ok
        else "via market-data collector"
        if market_ok
        else "no live IBKR-connected service detected"
    )

    services = [
        _service_status("Dashboard API", True, age=0, detail="FastAPI responding", icon="dashboard"),
        _service_status("Stock Worker", execute_ok, age=execute_age, detail=execute_detail, icon="bolt"),
        _service_status(
            "Pre-Open",
            bool(premarket.get("ok")),
            age=premarket.get("age"),
            detail=str(premarket.get("detail") or ""),
            status=str(premarket.get("status") or "unknown"),
            icon="wb_twilight",
        ),
        _service_status("Market Data", market_ok, age=market_age, detail=market_detail, status=market_status, icon="database"),
        _service_status("Crypto Worker", crypto_ok, age=crypto_age, detail=crypto_detail, status=crypto_status, icon="currency_bitcoin"),
        _service_status("IBKR Bridge", ibkr_ok, age=min([a for a in [execute_age, market_age] if a is not None], default=None), detail=ibkr_detail, icon="account_balance"),
    ]
    return {
        "updated_at": datetime.now(ET).isoformat(),
        "services": services,
        "all_ok": all(service["ok"] for service in services),
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


@app.get("/api/watchlist")
def api_watchlist():
    from src.symbol_universe import load_stock_symbols

    watchlist = _safe(da.load_watchlist, {})
    configured_symbols = _safe(load_stock_symbols, [])
    rows = []

    def _num(value):
        try:
            result = float(value)
            return result if pd.notna(result) else None
        except (TypeError, ValueError):
            return None

    symbols = sorted(set(configured_symbols or []) | set((watchlist or {}).keys()))
    for symbol in symbols:
        plan = (watchlist or {}).get(symbol, {})
        has_plan = isinstance(plan, dict) and bool(plan)
        if not isinstance(plan, dict):
            plan = {}

        spacing = plan.get("level_spacing", {}) or {}
        levels = plan.get("levels", []) or []
        blocked = bool(plan.get("news_blocked"))
        status = "PENDING" if not has_plan else ("BLOCKED" if blocked else ("READY" if levels else "NO LEVELS"))

        daily = _safe(lambda s=symbol: _daily_live_frame(s), pd.DataFrame())
        last_price = _num(spacing.get("current_price"))
        prev_close = None
        change = None
        change_pct = None
        day_open = day_high = day_low = volume = None
        last_bar = None

        if daily is not None and not daily.empty:
            daily = daily.sort_values("date").dropna(subset=["close"])
            if not daily.empty:
                last = daily.iloc[-1]
                last_price = _num(last.get("close")) or last_price
                day_open = _num(last.get("open"))
                day_high = _num(last.get("high"))
                day_low = _num(last.get("low"))
                volume = _num(last.get("volume"))
                last_bar = str(last.get("date")) if last.get("date") is not None else None
                if len(daily) >= 2:
                    prev_close = _num(daily.iloc[-2].get("close"))
                    if last_price is not None and prev_close not in (None, 0):
                        change = last_price - prev_close
                        change_pct = change / prev_close * 100.0

        rows.append({
            "symbol": symbol,
            "status": status,
            "last_price": round(last_price, 4) if last_price is not None else None,
            "prev_close": round(prev_close, 4) if prev_close is not None else None,
            "change": round(change, 4) if change is not None else None,
            "change_pct": round(change_pct, 4) if change_pct is not None else None,
            "day_open": round(day_open, 4) if day_open is not None else None,
            "day_high": round(day_high, 4) if day_high is not None else None,
            "day_low": round(day_low, 4) if day_low is not None else None,
            "volume": int(volume) if volume is not None else None,
            "daily_atr": round(_num(plan.get("daily_atr")), 4) if _num(plan.get("daily_atr")) is not None else None,
            "technical_atr": round(_num(plan.get("technical_atr")), 4) if _num(plan.get("technical_atr")) is not None else None,
            "trade_levels": len(levels),
            "raw_levels": len(plan.get("raw_levels", []) or []),
            "news": "BLOCKED" if blocked else "CLEAR",
            "last_bar": last_bar,
            "security_type": plan.get("security_type", "-"),
            "room": spacing.get("reason", "-"),
        })

    gainers = sum(1 for row in rows if (row.get("change") or 0) > 0)
    losers = sum(1 for row in rows if (row.get("change") or 0) < 0)
    unchanged = len(rows) - gainers - losers
    ready = sum(1 for row in rows if row.get("status") == "READY")
    pending = sum(1 for row in rows if row.get("status") == "PENDING")

    return {
        "rows": rows,
        "metrics": {
            "symbols": len(rows),
            "ready": ready,
            "pending": pending,
            "gainers": gainers,
            "losers": losers,
            "unchanged": unchanged,
        },
        "updated_at": datetime.now(ET).isoformat(),
    }


def _screener_frame(symbol: str, timeframe: str) -> pd.DataFrame:
    timeframe = str(timeframe or "1D").upper()
    if timeframe == "1D":
        return _daily_live_frame(symbol)
    if timeframe == "4H":
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
    if timeframe == "1H":
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
    return _daily_live_frame(symbol)


@app.get("/api/market-screener")
def api_market_screener(
    timeframe: str = "1D",
    strategies: str = "LP1,LP2,PRB1,PRB2",
    side: str = "ALL",
    min_score: float = 0.0,
    rr: float = 2.0,
    risk_pct: float = 0.5,
    equity: float = 100000.0,
    anchor_date: str = "",
):
    from src.symbol_universe import load_stock_symbols

    watchlist = _safe(da.load_watchlist, {})
    configured_symbols = _safe(load_stock_symbols, [])
    symbols = sorted(set(configured_symbols or []) | set((watchlist or {}).keys()))
    selected_strategies = tuple(
        strategy
        for strategy in (item.strip().upper() for item in str(strategies or "").split(","))
        if strategy in {"LP1", "LP2", "PRB1", "PRB2"}
    ) or ("LP1", "LP2", "PRB1", "PRB2")
    params = ScreenerParams(
        timeframe=str(timeframe or "1D").upper(),
        anchor_date=str(anchor_date or "").strip() or None,
        strategies=selected_strategies,
        side_filter=str(side or "ALL").upper(),
        score_min=max(0.0, min(1.0, float(min_score or 0.0))),
        rr=max(1.0, min(3.0, float(rr or 2.0))),
        risk_pct=max(0.0001, min(0.05, float(risk_pct or 0.5) / 100.0)),
        equity=max(1.0, float(equity or 100000.0)),
    )
    return run_market_screener(
        symbols=symbols,
        bars_loader=_screener_frame,
        watchlist=watchlist if isinstance(watchlist, dict) else {},
        params=params,
    )


@app.post("/api/watchlist/add")
async def api_watchlist_add(body: dict):
    from src.symbol_universe import add_stock_symbol, normalize_stock_symbol

    try:
        symbol = normalize_stock_symbol(str(body.get("symbol", "")))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    try:
        add_result = add_stock_symbol(symbol)
        if not add_result.get("added"):
            return {
                "ok": True,
                "symbol": symbol,
                "added": False,
                "duplicate": True,
                "pid": None,
                "log": None,
                "message": f"{symbol} is already in the stock universe. Onboarding was not queued again.",
            }
        SETTINGS.paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        log_path = SETTINGS.paths.runtime_dir / f"onboard_symbol_{symbol}.log"
        client_id = _onboard_client_id(symbol)
        cmd = [
            sys.executable,
            "-m",
            "src.main",
            "--job",
            "onboard_symbol",
            "--symbol",
            symbol,
            "--client-id",
            client_id,
        ]
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n--- {datetime.now(ET).isoformat()} queued {' '.join(cmd)} ---\n")
            process = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        return {
            "ok": True,
            "symbol": symbol,
            "added": bool(add_result.get("added")),
            "pid": process.pid,
            "log": str(log_path),
            "message": f"Queued onboarding for {symbol}. The bot will fetch bars, calculate levels/zones, and refresh the working watchlist.",
        }
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.post("/api/watchlist/remove")
async def api_watchlist_remove(body: dict):
    from src.symbol_universe import normalize_stock_symbol, remove_stock_symbol

    try:
        symbol = normalize_stock_symbol(str(body.get("symbol", "")))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    try:
        remove_result = remove_stock_symbol(symbol)
        state_path = SETTINGS.paths.state_file
        removed_from_state = False
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                state = {}
            if isinstance(state, dict):
                watchlist = state.get("watchlist")
                if isinstance(watchlist, dict) and symbol in watchlist:
                    watchlist.pop(symbol, None)
                    removed_from_state = True
                temp_path = state_path.with_suffix(state_path.suffix + ".tmp")
                temp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
                os.replace(temp_path, state_path)
        return {
            "ok": True,
            "symbol": symbol,
            "removed": bool(remove_result.get("removed")) or removed_from_state,
            "removed_from_config": bool(remove_result.get("removed")),
            "removed_from_state": removed_from_state,
            "message": f"{symbol} removed from stock universe and active watchlist.",
        }
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/api/strategy")
def api_strategy():
    from src.symbol_universe import load_stock_symbols, stock_symbols_file

    index = _safe(da.bars_index, {})
    configured_symbols = _safe(load_stock_symbols, [])
    if stock_symbols_file().exists():
        symbols = sorted(configured_symbols or [])
    else:
        symbols = sorted(
            symbol for symbol, frames in (index or {}).items()
            if isinstance(frames, dict) and ("daily" in frames or "weekly" in frames)
        )
    today = datetime.now(ET).date()
    rows = []
    for symbol in symbols:
        row = _safe(lambda s=symbol: _bmsb_scan_symbol(s, today, include_neutral=True), None)
        if row is None:
            row = {
                "symbol": symbol,
                "signal": "NO_DATA",
                "side": "NONE",
                "urgency": "none",
                "last_price": None,
                "ema21": None,
                "sma20": None,
                "gap": None,
                "gap_pct": None,
                "entry_level": None,
                "exit_level": None,
                "trigger_level": None,
                "trigger_gap_pct": None,
                "cross_date": None,
                "weekly_bar": None,
                "threshold_pct": BMSB_NEAR_CROSS_THRESHOLD_PCT,
            }
        rows.append(row)
    matched_rows = [row for row in rows if row.get("urgency") in {"crossed", "near"}]
    urgency_rank = {"crossed": 0, "near": 1}
    side_rank = {"LONG": 0, "EXIT": 1, "NONE": 2}
    def _sort_gap(row: dict) -> float:
        value = row.get("trigger_gap_pct")
        if value is None:
            value = row.get("gap_pct")
        try:
            return float(value)
        except (TypeError, ValueError):
            return 999.0
    rows.sort(key=lambda r: (
        urgency_rank.get(str(r.get("urgency")), 9),
        side_rank.get(str(r.get("side")), 9),
        _sort_gap(r),
        str(r.get("symbol", "")),
    ))
    return {
        "strategy": "BMSB Strategy",
        "description": "Weekly BMSB monitor. Signals are based on the 21W EMA crossing the 20W SMA, or symbols near that cross.",
        "threshold_pct": BMSB_NEAR_CROSS_THRESHOLD_PCT,
        "recent_days": BMSB_RECENT_CROSS_DAYS,
        "symbols_scanned": len(symbols),
        "symbols": symbols,
        "matches": len(matched_rows),
        "crossed": sum(1 for row in matched_rows if row.get("urgency") == "crossed"),
        "near": sum(1 for row in matched_rows if row.get("urgency") == "near"),
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


def _crypto_tv_payload(tv_state: dict, *, limit: int = 50) -> dict:
    if not isinstance(tv_state, dict):
        tv_state = {}
    positions = tv_state.get("positions", {})
    executions = tv_state.get("executions", [])
    if not isinstance(positions, dict):
        positions = {}
    if not isinstance(executions, list):
        executions = []
    return {
        "updated_at": tv_state.get("updated_at"),
        "positions": list(positions.values()),
        "executions": [
            {key: value for key, value in row.items() if key not in {"okx_response", "raw"}}
            for row in executions[:limit]
            if isinstance(row, dict)
        ],
    }


def _crypto_event_date(value: Any):
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
        return parsed.astimezone(ET).date()
    except Exception:
        return None


def _resolve_crypto_add_instrument(raw_symbol: str) -> tuple[str, list[str]]:
    """Resolve user-friendly crypto input to an OKX instrument id.

    People often type TradingView-style pairs such as DOGE-USD while OKX spot
    commonly lists DOGE-USDT. Prefer an exact OKX match, then try the USDT spot
    proxy for plain USD pairs.
    """

    normalized = normalize_okx_instrument(raw_symbol)
    if not normalized:
        raise ValueError("Crypto symbol is required.")

    candidates = [normalized]
    if normalized.endswith("-USD") and not normalized.endswith("-USDT"):
        candidates.append(f"{normalized[:-4]}-USDT")

    by_type: dict[str, set[str]] = {}
    client = OKXClient()
    for candidate in candidates:
        by_type.setdefault(default_instrument_type(candidate), set()).add(candidate)

    available: set[str] = set()
    for inst_type in by_type:
        available.update(item.inst_id for item in client.instruments(inst_type))

    for candidate in candidates:
        if candidate in available:
            return candidate, candidates

    raise ValueError(
        f"{normalized} is not available on OKX. Tried: {', '.join(candidates)}."
    )


@app.get("/api/crypto")
def api_crypto():
    state = _safe(da.load_crypto_dashboard_state, {})
    tv_state = _safe(load_tradingview_state, {})
    configured = _safe(da.load_crypto_symbols, [])
    watchlist = state.get("watchlist", {}) if isinstance(state, dict) else {}
    index = _safe(da.crypto_bars_index, {})
    tv_positions = tv_state.get("positions", {}) if isinstance(tv_state, dict) else {}
    tv_executions = tv_state.get("executions", []) if isinstance(tv_state, dict) else []
    if not isinstance(tv_positions, dict):
        tv_positions = {}
    if not isinstance(tv_executions, list):
        tv_executions = []
    today = datetime.now(ET).date()
    position_by_symbol = {}
    for inst_id, position in tv_positions.items():
        if not isinstance(position, dict):
            continue
        try:
            qty = float(position.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty > 0:
            position_by_symbol[str(inst_id).upper()] = {**position, "qty": qty}
    realized_pnl_by_symbol = {}
    for execution in tv_executions:
        if not isinstance(execution, dict):
            continue
        if str(execution.get("status", "")).lower() != "submitted":
            continue
        if str(execution.get("action", "")).upper() != "SELL":
            continue
        if _crypto_event_date(execution.get("created_at")) != today:
            continue
        inst_id = str(execution.get("inst_id") or "").upper()
        if not inst_id:
            continue
        try:
            pnl = float(execution.get("daily_pnl") if execution.get("daily_pnl") is not None else execution.get("estimated_pnl") or 0)
        except (TypeError, ValueError):
            pnl = 0.0
        realized_pnl_by_symbol[inst_id] = realized_pnl_by_symbol.get(inst_id, 0.0) + pnl
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
        price = plan.get("price") if isinstance(plan, dict) else None
        daily_open = None
        daily_change = None
        daily_change_pct = None
        daily_pnl = None
        daily_pnl_pct = None
        daily_pnl_status = None
        position = position_by_symbol.get(symbol.upper())
        position_qty = float((position or {}).get("qty") or 0.0)
        daily_bars = _safe(lambda s=symbol: da.get_crypto_bars(s, "daily"), None)
        try:
            current_price = float(price) if price is not None else None
        except (TypeError, ValueError):
            current_price = None
        if daily_bars is not None and not daily_bars.empty:
            try:
                last_daily = daily_bars.iloc[-1]
                daily_open = float(last_daily.get("open"))
                latest_daily_close = float(last_daily.get("close"))
                current_price = float(price) if price is not None else latest_daily_close
                if daily_open > 0:
                    daily_change = current_price - daily_open
                    daily_change_pct = (daily_change / daily_open) * 100.0
            except (TypeError, ValueError):
                pass
        realized_today = realized_pnl_by_symbol.get(symbol.upper())
        has_pnl_today = realized_today is not None
        if has_pnl_today:
            daily_pnl = float(realized_today or 0.0)
            daily_pnl_status = "CLOSED"
        if position_qty > 0 and current_price is not None:
            try:
                opened_today = _crypto_event_date(position.get("opened_at")) == today if isinstance(position, dict) else False
                entry = float(position.get("entry") or 0.0) if isinstance(position, dict) else 0.0
                baseline = entry if opened_today and entry > 0 else daily_open
                if baseline and baseline > 0:
                    open_pnl = (current_price - baseline) * position_qty
                    daily_pnl = (daily_pnl or 0.0) + open_pnl
                    daily_pnl_pct = ((current_price - baseline) / baseline) * 100.0
                    daily_pnl_status = "OPEN"
            except (TypeError, ValueError):
                pass
        rows.append({
            "symbol": symbol,
            "raw": configured_by_inst.get(symbol, {}).get("raw"),
            "inst_type": configured_by_inst.get(symbol, {}).get("inst_type"),
            "ready": bool(plan.get("ready")) if isinstance(plan, dict) else False,
            "price": price,
            "daily_atr": plan.get("daily_atr") if isinstance(plan, dict) else None,
            "daily_open": daily_open,
            "daily_change": daily_change,
            "daily_change_pct": daily_change_pct,
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": daily_pnl_pct,
            "daily_pnl_status": daily_pnl_status,
            "position_qty": position_qty,
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
        "tradingview": _crypto_tv_payload(tv_state, limit=50),
        "metrics": {
            "configured": len(configured),
            "tracked": len(rows),
            "ready": ready,
            "with_trade_levels": sum(1 for row in rows if int(row.get("trade_levels") or 0) > 0),
        },
    }


@app.get("/api/crypto/tradingview")
def api_crypto_tradingview_state():
    tv_state = _safe(load_tradingview_state, {})
    return _crypto_tv_payload(tv_state, limit=100)


@app.post("/api/crypto/add")
async def api_crypto_add(body: dict):
    try:
        raw_symbol = str((body or {}).get("symbol", "") or "").strip()
        normalized = normalize_okx_instrument(raw_symbol)
        if not normalized:
            raise ValueError("Crypto symbol is required.")
        duplicate_candidates = [normalized]
        if normalized.endswith("-USD") and not normalized.endswith("-USDT"):
            duplicate_candidates.append(f"{normalized[:-4]}-USDT")
        configured_ids = {
            str(row.get("inst_id") or "").upper()
            for row in _safe(da.load_crypto_symbols, [])
            if isinstance(row, dict) and row.get("inst_id")
        }
        duplicate = next((candidate for candidate in duplicate_candidates if candidate in configured_ids), None)
        if duplicate:
            return {
                "ok": True,
                "symbol": duplicate,
                "added": False,
                "duplicate": True,
                "pid": None,
                "log": None,
                "message": f"{duplicate} is already in the crypto universe. Candle loading was not queued again.",
            }

        inst_id, candidates = _resolve_crypto_add_instrument(raw_symbol)
        add_result = add_crypto_symbol(inst_id)
        if not add_result.get("added"):
            return {
                "ok": True,
                "symbol": inst_id,
                "added": False,
                "duplicate": True,
                "pid": None,
                "log": None,
                "message": f"{inst_id} is already in the crypto universe. Candle loading was not queued again.",
            }

        runtime_dir = CRYPTO_SETTINGS.memory_dir / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(char if char.isalnum() else "_" for char in inst_id)
        log_path = runtime_dir / f"onboard_crypto_{safe_name}.log"
        cmd = [
            sys.executable,
            "-m",
            "src.crypto.main",
            "--job",
            "collect_analyze",
            "--symbol",
            inst_id,
        ]
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n--- {datetime.now(ET).isoformat()} queued {' '.join(cmd)} ---\n")
            if len(candidates) > 1 and inst_id != candidates[0]:
                log.write(f"Resolved requested symbol {raw_symbol} to OKX instrument {inst_id}.\n")
            process = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        return {
            "ok": True,
            "symbol": inst_id,
            "requested": raw_symbol,
            "added": True,
            "duplicate": False,
            "pid": process.pid,
            "log": str(log_path),
            "message": f"Queued crypto onboarding for {inst_id}. The bot will fetch candles, calculate levels/zones, and refresh the Crypto tab.",
        }
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.post("/api/crypto/tradingview")
@app.post("/api/webhook/tradingview/crypto")
async def api_crypto_tradingview_webhook(body: dict, background_tasks: BackgroundTasks):
    try:
        accepted = enqueue_tradingview_webhook(body or {})
        execution_id = str((accepted.get("execution") or {}).get("id") or "")
        if execution_id and accepted.get("queued"):
            background_tasks.add_task(process_queued_tradingview_execution, execution_id)
        return accepted
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/api/stocks/tradingview")
@app.get("/api/webhook/tradingview/stocks")
def api_stock_tradingview_state():
    state = _safe(load_stock_tradingview_state, {})
    executions = state.get("executions", []) if isinstance(state, dict) else []
    return {
        "updated_at": state.get("updated_at") if isinstance(state, dict) else None,
        "executions": executions[:100] if isinstance(executions, list) else [],
    }


@app.post("/api/stocks/tradingview")
@app.post("/api/webhook/tradingview/stocks")
async def api_stock_tradingview_webhook(body: dict):
    try:
        return handle_stock_tradingview_webhook(body or {})
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))


def _forex_request_rows(limit: int = 30) -> list[dict]:
    requests = _safe(da.load_order_requests, [])
    rows = []
    for request in requests:
        if not isinstance(request, dict):
            continue
        symbol = str(request.get("symbol") or "")
        if str(request.get("source") or "") == "forex_tradingview" or fx_pair_components(symbol):
            rows.append(request)
    return rows[:limit]


@app.get("/api/forex")
def api_forex():
    state = _safe(load_forex_tradingview_state, {})
    executions = state.get("executions", []) if isinstance(state, dict) else []
    return {
        "updated_at": state.get("updated_at") if isinstance(state, dict) else None,
        "symbols": sorted({str(item).upper() for item in SETTINGS.fx_symbols if str(item).strip()}),
        "webhook_url": "/api/webhook/tradingview/forex",
        "executions": executions[:100] if isinstance(executions, list) else [],
        "order_requests": _forex_request_rows(50),
    }


@app.get("/api/forex/tradingview")
@app.get("/api/webhook/tradingview/forex")
def api_forex_tradingview_state():
    state = _safe(load_forex_tradingview_state, {})
    executions = state.get("executions", []) if isinstance(state, dict) else []
    return {
        "updated_at": state.get("updated_at") if isinstance(state, dict) else None,
        "executions": executions[:100] if isinstance(executions, list) else [],
    }


@app.post("/api/forex/tradingview")
@app.post("/api/webhook/tradingview/forex")
async def api_forex_tradingview_webhook(body: dict):
    try:
        return handle_forex_tradingview_webhook(body or {})
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))


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
    stock_tv = _safe(load_stock_tradingview_state, {})
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
        "stock_tradingview": {
            "updated_at": stock_tv.get("updated_at") if isinstance(stock_tv, dict) else None,
            "executions": (stock_tv.get("executions", []) if isinstance(stock_tv, dict) else [])[:20],
        },
    }


@app.get("/api/orders-journal")
def api_orders_journal(limit: int = 500):
    try:
        broker_sync = reconcile_ibkr_open_orders()
        payload = load_order_journal(limit=max(1, min(1000, int(limit or 500))))
        if isinstance(payload, dict):
            payload["broker_sync"] = broker_sync
        return payload
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.post("/api/orders-journal/review")
async def api_orders_journal_review(body: dict):
    journal_id = str(body.get("id") or body.get("journal_id") or "").strip()
    if not journal_id:
        raise HTTPException(400, "id is required")
    try:
        review = save_review(journal_id, body)
        return {"ok": True, "id": journal_id, "review": review}
    except Exception as exc:
        raise HTTPException(500, str(exc))


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
