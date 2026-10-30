"""TradingView webhook execution bridge for OKX demo crypto trading.

The public ngrok URL should forward to the dashboard FastAPI server. This
module validates the TradingView payload, sends a demo-market order to OKX, and
keeps a small local execution/position ledger for the Crypto dashboard tab.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from src.config import LOGGER
from src.crypto.analysis import TIMEFRAMES, collect_crypto_bars, run_crypto_analysis
from src.crypto.bar_store import load_crypto_bars
from src.crypto.config import CRYPTO_SETTINGS, ensure_crypto_directories
from src.crypto.okx_client import OKXClient
from src.crypto.symbols import normalize_okx_instrument

STATE_PATH = CRYPTO_SETTINGS.memory_dir / "runtime" / "tradingview_okx_state.json"
MAX_EXECUTIONS = 200


class TradingViewWebhookError(ValueError):
    """Raised when the webhook payload is invalid or unsafe."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_template() -> Dict[str, Any]:
    return {"updated_at": None, "positions": {}, "executions": []}


def load_tradingview_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return _state_template()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _state_template()
        data.setdefault("positions", {})
        data.setdefault("executions", [])
        return data
    except (OSError, json.JSONDecodeError):
        return _state_template()


def _save_state(state: Dict[str, Any]) -> None:
    ensure_crypto_directories()
    state["updated_at"] = _now()
    executions = state.get("executions", [])
    if isinstance(executions, list):
        state["executions"] = executions[:MAX_EXECUTIONS]
    temp = STATE_PATH.with_name(f"{STATE_PATH.name}.{os.getpid()}.{uuid4().hex}.tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temp, STATE_PATH)


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        text = str(value).strip().replace("%", "")
        return float(text)
    except (TypeError, ValueError):
        return default


def _latest_price(inst_id: str) -> float | None:
    for timeframe in ("intraday_5m", "intraday_1h", "intraday_4h", "daily"):
        frame = load_crypto_bars(inst_id, timeframe)
        if frame is not None and not frame.empty and "close" in frame.columns:
            price = _as_float(frame["close"].iloc[-1])
            if price and price > 0:
                return price
    return None


def _safe_client_order_id(action: str, inst_id: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]", "", f"tv{action}{inst_id}")[:18]
    return f"{token}{uuid4().hex[:12]}"[:32]


def validate_tradingview_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise TradingViewWebhookError("TradingView webhook body must be a JSON object.")

    expected_bot_id = CRYPTO_SETTINGS.tradingview_bot_id
    if not expected_bot_id:
        raise TradingViewWebhookError("CRYPTO_TRADINGVIEW_BOT_ID is not configured.")
    received_bot_id = str(payload.get("bot_id", "")).strip()
    if received_bot_id != expected_bot_id:
        raise PermissionError("TradingView bot_id does not match CRYPTO_TRADINGVIEW_BOT_ID.")

    action = str(payload.get("action", "")).strip().upper()
    if action not in {"BUY", "SELL"}:
        raise TradingViewWebhookError("TradingView action must be BUY or SELL.")

    ticker = str(payload.get("ticker", "")).strip()
    inst_id = normalize_okx_instrument(ticker)
    if inst_id.endswith("-USD"):
        # TradingView often emits BTCUSD/ETHUSD for chart symbols, while OKX
        # spot demo trading is normally routed through USDT instruments.
        inst_id = f"{inst_id[:-4]}-USDT"
    if not inst_id:
        raise TradingViewWebhookError("TradingView ticker is empty or could not be normalized.")
    if inst_id.endswith("-SWAP"):
        raise TradingViewWebhookError("TradingView OKX webhook currently supports SPOT instruments only.")

    return {
        "action": action,
        "ticker": ticker,
        "inst_id": inst_id,
        "order_size": str(payload.get("order_size", "")).strip(),
        "position_size": _as_float(payload.get("position_size")),
        "schema": str(payload.get("schema", "")).strip(),
        "timestamp": str(payload.get("timestamp", "")).strip(),
        "bot_id": received_bot_id,
        "raw": payload,
    }


def _append_execution(state: Dict[str, Any], execution: Dict[str, Any]) -> None:
    state.setdefault("executions", [])
    state["executions"].insert(0, execution)


def _positions_and_executions(state: Dict[str, Any]) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    positions = state.setdefault("positions", {})
    executions = state.setdefault("executions", [])
    if not isinstance(positions, dict):
        positions = {}
        state["positions"] = positions
    if not isinstance(executions, list):
        executions = []
        state["executions"] = executions
    return positions, executions


def _execution_from_signal(signal: Dict[str, Any], *, status: str = "queued") -> Dict[str, Any]:
    inst_id = signal["inst_id"]
    return {
        "id": uuid4().hex,
        "created_at": _now(),
        "action": signal["action"],
        "ticker": signal["ticker"],
        "inst_id": inst_id,
        "price": _latest_price(inst_id),
        "order_size": signal["order_size"],
        "tv_position_size": signal["position_size"],
        "schema": signal["schema"],
        "tv_timestamp": signal["timestamp"],
        "status": status,
        "okx_order_id": None,
        "message": "Accepted from TradingView; OKX execution queued in background.",
    }


def _find_execution(state: Dict[str, Any], execution_id: str) -> Dict[str, Any] | None:
    executions = state.get("executions", [])
    if not isinstance(executions, list):
        return None
    for execution in executions:
        if isinstance(execution, dict) and str(execution.get("id")) == str(execution_id):
            return execution
    return None


def _extract_okx_order_id(response: Dict[str, Any]) -> str | None:
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return str(data[0].get("ordId") or "") or None
    return None


def _okx_fill_details(client: OKXClient, inst_id: str, order_id: str | None) -> Dict[str, float | None]:
    if not order_id:
        return {"price": None, "qty": None}
    try:
        response = client.order_details(inst_id=inst_id, order_id=order_id)
        data = response.get("data") if isinstance(response, dict) else None
        if isinstance(data, list) and data and isinstance(data[0], dict):
            row = data[0]
            return {
                "price": _as_float(row.get("avgPx")) or _as_float(row.get("fillPx")),
                "qty": _as_float(row.get("accFillSz")) or _as_float(row.get("fillSz")),
            }
    except Exception as exc:
        LOGGER.warning("OKX fill detail lookup failed %s %s: %s", inst_id, order_id, exc)
    return {"price": None, "qty": None}


def _needs_onboarding(inst_id: str) -> bool:
    for timeframe in TIMEFRAMES:
        bars = load_crypto_bars(inst_id, timeframe)
        if bars is None or bars.empty:
            return True
    return False


def _onboard_after_execution(inst_id: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "symbol": inst_id,
        "needed": _needs_onboarding(inst_id),
        "status": "skipped",
        "message": "Symbol already has crypto bars.",
    }
    try:
        if result["needed"]:
            collection = collect_crypto_bars([inst_id], timeframes=TIMEFRAMES)
            result["collection"] = collection
            failures = collection.get("failed", []) if isinstance(collection, dict) else []
            if failures:
                result["status"] = "error"
                result["message"] = f"Post-trade candle fetch had {len(failures)} failure(s)."
                return result
        analysis = run_crypto_analysis([inst_id])
        watchlist = analysis.get("watchlist", {}) if isinstance(analysis, dict) else {}
        plan = watchlist.get(inst_id, {}) if isinstance(watchlist, dict) else {}
        result["analysis_ready"] = bool(plan.get("ready")) if isinstance(plan, dict) else False
        result["status"] = "updated"
        result["message"] = "Post-trade crypto bars and analysis updated."
        return result
    except Exception as exc:
        LOGGER.warning("Post-trade crypto onboarding failed %s: %s", inst_id, exc)
        result["status"] = "error"
        result["message"] = str(exc)
        return result


def enqueue_tradingview_webhook(payload: Dict[str, Any]) -> Dict[str, Any]:
    signal = validate_tradingview_payload(payload)
    if not CRYPTO_SETTINGS.okx_simulated_trading:
        raise TradingViewWebhookError("OKX_SIMULATED_TRADING must stay true for this TradingView demo webhook.")
    if CRYPTO_SETTINGS.okx_account_mode != "spot":
        raise TradingViewWebhookError("TradingView OKX webhook currently supports OKX_ACCOUNT_MODE=spot only.")

    state = load_tradingview_state()
    positions, executions = _positions_and_executions(state)
    execution = _execution_from_signal(signal, status="queued")
    execution["raw"] = signal["raw"]
    if signal["action"] == "BUY":
        already_open_or_queued = signal["inst_id"] in positions or any(
            isinstance(row, dict)
            and str(row.get("inst_id")) == signal["inst_id"]
            and str(row.get("action")).upper() == "BUY"
            and str(row.get("status")).lower() in {"queued", "processing"}
            for row in executions
        )
        if already_open_or_queued:
            execution["status"] = "skipped"
            execution["message"] = "Position or BUY alert already open/queued in local TradingView/OKX ledger."
    _append_execution(state, execution)
    _save_state(state)
    queued = execution.get("status") == "queued"
    return {
        "ok": True,
        "accepted": True,
        "queued": queued,
        "execution": {key: value for key, value in execution.items() if key != "raw"},
        "positions": list(positions.values()),
        "message": (
            "TradingView alert accepted; OKX execution will continue in background."
            if queued else execution["message"]
        ),
    }


def process_queued_tradingview_execution(execution_id: str, *, client: OKXClient | None = None) -> Dict[str, Any]:
    state = load_tradingview_state()
    positions, _executions = _positions_and_executions(state)
    execution = _find_execution(state, execution_id)
    if execution is None:
        return {"ok": False, "message": f"Queued TradingView execution not found: {execution_id}"}
    if str(execution.get("status", "")).lower() not in {"queued", "received"}:
        return {"ok": True, "execution": {key: value for key, value in execution.items() if key not in {"okx_response", "raw"}}}

    inst_id = str(execution.get("inst_id") or "").strip()
    action = str(execution.get("action") or "").upper()
    if not inst_id or action not in {"BUY", "SELL"}:
        execution["status"] = "error"
        execution["message"] = "Queued execution is missing inst_id or BUY/SELL action."
        _save_state(state)
        return {"ok": False, "execution": execution}

    price = _latest_price(inst_id)
    if price and price > 0:
        execution["price"] = price
    created_at = str(execution.get("created_at") or _now())
    execution["status"] = "processing"
    execution["message"] = "Processing OKX demo market order."

    try:
        okx = client or OKXClient()
        if action == "BUY":
            if inst_id in positions:
                execution["status"] = "skipped"
                execution["message"] = "Position already open in local TradingView/OKX ledger."
            else:
                alert_qty = abs(float(execution.get("tv_position_size") or 0.0))
                if alert_qty <= 0:
                    raise TradingViewWebhookError("TradingView BUY alert position_size must be greater than zero.")
                estimated_quote = round(alert_qty * price, 8) if price and price > 0 else None
                response = okx.place_market_order(
                    inst_id=inst_id,
                    side="buy",
                    size=f"{alert_qty:.8f}".rstrip("0").rstrip("."),
                    td_mode="cash",
                    tgt_ccy="base_ccy",
                    client_order_id=_safe_client_order_id("buy", inst_id),
                )
                okx_order_id = _extract_okx_order_id(response)
                fill = _okx_fill_details(okx, inst_id, okx_order_id)
                fill_price = fill.get("price") or price
                fill_qty = fill.get("qty") or alert_qty
                estimated_quote = round(fill_qty * fill_price, 8) if fill_price and fill_qty else estimated_quote
                execution.update({
                    "status": "submitted",
                    "quote_amount": estimated_quote,
                    "estimated_qty": fill_qty,
                    "price": fill_price,
                    "okx_order_id": okx_order_id,
                    "message": "Demo market BUY submitted to OKX using TradingView position_size.",
                    "okx_response": response,
                })
                positions[inst_id] = {
                    "inst_id": inst_id,
                    "ticker": execution.get("ticker"),
                    "side": "LONG",
                    "entry": fill_price,
                    "qty": fill_qty,
                    "quote_amount": estimated_quote,
                    "opened_at": created_at,
                    "okx_order_id": okx_order_id,
                    "source": "TradingView webhook",
                }
        else:
            position = positions.get(inst_id)
            if not isinstance(position, dict):
                execution["status"] = "skipped"
                execution["message"] = "No open local position to close."
            else:
                qty = _as_float(position.get("qty"))
                if not qty or qty <= 0:
                    raise TradingViewWebhookError(f"No tracked quantity for {inst_id}; cannot close safely.")
                response = okx.place_market_order(
                    inst_id=inst_id,
                    side="sell",
                    size=f"{qty:.8f}".rstrip("0").rstrip("."),
                    td_mode="cash",
                    client_order_id=_safe_client_order_id("sell", inst_id),
                )
                okx_order_id = _extract_okx_order_id(response)
                fill = _okx_fill_details(okx, inst_id, okx_order_id)
                fill_price = fill.get("price") or price
                fill_qty = fill.get("qty") or qty
                entry = _as_float(position.get("entry"))
                pnl = round((fill_price - entry) * fill_qty, 8) if fill_price and entry and fill_qty else None
                execution.update({
                    "status": "submitted",
                    "estimated_qty": fill_qty,
                    "entry": entry,
                    "price": fill_price,
                    "opened_at": position.get("opened_at"),
                    "estimated_pnl": pnl,
                    "okx_order_id": okx_order_id,
                    "message": "Demo market SELL close submitted to OKX.",
                    "okx_response": response,
                })
                positions.pop(inst_id, None)
    except Exception as exc:
        LOGGER.warning("TradingView OKX webhook failed %s %s: %s", action, inst_id, exc)
        execution["status"] = "error"
        execution["message"] = str(exc)

    if execution.get("status") == "submitted":
        onboarding = _onboard_after_execution(inst_id)
        execution["onboarding"] = onboarding
        if onboarding.get("status") == "error":
            execution["message"] = f"{execution.get('message', '').rstrip()} Post-trade onboarding warning: {onboarding.get('message')}"

    latest_state = load_tradingview_state()
    latest_positions, _latest_executions = _positions_and_executions(latest_state)
    latest_execution = _find_execution(latest_state, execution_id)
    if latest_execution is not None:
        latest_execution.clear()
        latest_execution.update(execution)
    else:
        _append_execution(latest_state, execution)
    if execution.get("status") == "submitted":
        if action == "BUY" and inst_id in positions:
            latest_positions[inst_id] = positions[inst_id]
        elif action == "SELL":
            latest_positions.pop(inst_id, None)
    _save_state(latest_state)
    return {
        "ok": execution["status"] in {"submitted", "skipped"},
        "execution": {key: value for key, value in execution.items() if key not in {"okx_response", "raw"}},
        "positions": list(latest_positions.values()),
    }


def handle_tradingview_webhook(payload: Dict[str, Any], *, client: OKXClient | None = None) -> Dict[str, Any]:
    """Compatibility wrapper: queue then process synchronously."""
    accepted = enqueue_tradingview_webhook(payload)
    execution_id = str((accepted.get("execution") or {}).get("id") or "")
    if not execution_id:
        return accepted
    return process_queued_tradingview_execution(execution_id, client=client)
