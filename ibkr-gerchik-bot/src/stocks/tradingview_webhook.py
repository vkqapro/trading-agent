"""TradingView webhook bridge for stock alerts routed to IBKR.

TradingView posts to the dashboard FastAPI server, this module validates the
payload, then appends a paper-only request to the existing execution queue.
The ``execute_requests`` worker is the only process that talks to IBKR, so the
dashboard remains broker-read-only and all results continue to show up in the
Trades & Positions tab.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4

from src.config import LOGGER, MEMORY_DIR, SETTINGS, ensure_directories, fx_pair_components
from src.execution import order_requests as oq

STATE_PATH = MEMORY_DIR / "runtime" / "tradingview_stock_state.json"
MAX_EXECUTIONS = 200


class StockTradingViewWebhookError(ValueError):
    """Raised when a stock TradingView webhook payload is invalid or unsafe."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_template() -> Dict[str, Any]:
    return {"updated_at": None, "executions": []}


def load_stock_tradingview_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return _state_template()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _state_template()
        data.setdefault("executions", [])
        return data
    except (OSError, json.JSONDecodeError):
        return _state_template()


def _save_state(state: Dict[str, Any]) -> None:
    ensure_directories()
    state["updated_at"] = _now()
    executions = state.get("executions", [])
    if isinstance(executions, list):
        state["executions"] = executions[:MAX_EXECUTIONS]
    temp = STATE_PATH.with_name(f"{STATE_PATH.name}.{os.getpid()}.{uuid4().hex}.tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temp, STATE_PATH)


def _append_execution(state: Dict[str, Any], execution: Dict[str, Any]) -> None:
    state.setdefault("executions", [])
    state["executions"].insert(0, execution)


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        text = str(value).strip().replace("$", "").replace(",", "").replace("%", "")
        if text.lower() in {"na", "nan", "null", "none"}:
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    if number is None:
        return None
    qty = int(abs(number))
    return qty if qty > 0 else None


def _first_float(payload: Dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _as_float(payload.get(key))
        if value is not None:
            return value
    return None


def _normalize_stock_symbol(raw: Any) -> str:
    symbol = str(raw or "").strip().upper()
    if not symbol:
        return ""
    # TradingView may emit NASDAQ:AAPL, AMEX:SPY, or AAPL.US.
    if ":" in symbol:
        symbol = symbol.rsplit(":", 1)[-1]
    symbol = symbol.replace("/", "-")
    symbol = re.sub(r"\.(US|NASDAQ|NYSE|AMEX|ARCA)$", "", symbol)
    symbol = re.sub(r"[^A-Z0-9.\-]", "", symbol)
    return symbol


def _expected_bot_id() -> str:
    # Allows a separate stock token, but keeps setup easy by falling back to the
    # already-working crypto token if STOCK_TRADINGVIEW_BOT_ID is not set.
    return (
        os.getenv("STOCK_TRADINGVIEW_BOT_ID", "").strip()
        or os.getenv("CRYPTO_TRADINGVIEW_BOT_ID", "").strip()
    )


def validate_stock_tradingview_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise StockTradingViewWebhookError("TradingView webhook body must be a JSON object.")

    expected_bot_id = _expected_bot_id()
    if not expected_bot_id:
        raise StockTradingViewWebhookError("STOCK_TRADINGVIEW_BOT_ID or CRYPTO_TRADINGVIEW_BOT_ID is not configured.")
    received_bot_id = str(payload.get("bot_id", "")).strip()
    if received_bot_id != expected_bot_id:
        raise PermissionError("TradingView bot_id does not match the configured stock webhook bot id.")

    action = str(payload.get("action", "")).strip().upper()
    if action not in {"BUY", "SELL"}:
        raise StockTradingViewWebhookError("TradingView action must be BUY or SELL.")

    symbol = _normalize_stock_symbol(payload.get("ticker") or payload.get("symbol"))
    if not symbol:
        raise StockTradingViewWebhookError("TradingView ticker is empty or could not be normalized.")
    if fx_pair_components(symbol):
        raise StockTradingViewWebhookError("Forex pairs must use /api/webhook/tradingview/forex, not the stock webhook.")

    quantity = (
        _as_int(payload.get("quantity"))
        or _as_int(payload.get("qty"))
        or _as_int(payload.get("shares"))
        or _as_int(payload.get("position_size"))
    )

    entry = _first_float(payload, "entry", "entry_price", "price", "close", "order_price")
    stop = _first_float(payload, "stop", "stop_loss", "sl", "stopPrice", "stop_price")
    target = _first_float(payload, "target", "take_profit", "tp", "target_price", "limit_price")
    reward_risk = _first_float(payload, "reward_risk", "rr", "risk_reward")

    if action == "BUY":
        if quantity is None or quantity <= 0:
            raise StockTradingViewWebhookError("Stock market BUY alert requires quantity/shares/position_size greater than zero.")
        # TradingView stock alerts are intentionally market-only: no stop-loss
        # and no target are attached. If TradingView sends close/entry we keep
        # it only as informational metadata; IBKR receives a market order.
        stop = None
        target = None
        reward_risk = None
        resolved_risk = {
            "risk_source": "market_only_no_stop_target",
            "daily_atr": None,
            "nearest_lower_level": None,
            "nearest_upper_level": None,
        }
    else:
        resolved_risk = {}

    return {
        "action": action,
        "symbol": symbol,
        "entry": entry,
        "stop": stop,
        "target": target,
        "reward_risk": reward_risk,
        "quantity": quantity,
        "order_size": str(payload.get("order_size", "")).strip(),
        "position_size": _as_float(payload.get("position_size")),
        "schema": str(payload.get("schema", "")).strip(),
        "timestamp": str(payload.get("timestamp", "")).strip(),
        "bot_id": received_bot_id,
        "risk_source": resolved_risk.get("risk_source"),
        "daily_atr": resolved_risk.get("daily_atr"),
        "nearest_lower_level": resolved_risk.get("nearest_lower_level"),
        "nearest_upper_level": resolved_risk.get("nearest_upper_level"),
        "raw": payload,
    }


def handle_stock_tradingview_webhook(payload: Dict[str, Any]) -> Dict[str, Any]:
    signal = validate_stock_tradingview_payload(payload)
    state = load_stock_tradingview_state()
    executions = state.setdefault("executions", [])
    if not isinstance(executions, list):
        state["executions"] = []

    action = signal["action"]
    symbol = signal["symbol"]
    execution: Dict[str, Any] = {
        "id": uuid4().hex,
        "created_at": _now(),
        "action": action,
        "symbol": symbol,
        "entry": signal.get("entry"),
        "stop": signal.get("stop"),
        "target": signal.get("target"),
        "quantity": signal.get("quantity"),
        "risk_source": signal.get("risk_source"),
        "daily_atr": signal.get("daily_atr"),
        "order_size": signal.get("order_size"),
        "tv_position_size": signal.get("position_size"),
        "schema": signal.get("schema"),
        "tv_timestamp": signal.get("timestamp"),
        "status": "received",
        "request_id": None,
        "dry_run": SETTINGS.dry_run_mode,
        "message": "",
    }

    try:
        if action == "BUY":
            request_id = oq.submit_place(
                {
                    "symbol": symbol,
                    "strategy": "tradingview_webhook",
                    "signal": "BUY",
                    "direction": "LONG",
                    "entry": signal["entry"],
                    "stop": None,
                    "target": None,
                    "level_price": signal["entry"],
                    "level_type": "tradingview",
                    "reward_risk": signal["reward_risk"],
                    "nearest_lower_level": signal.get("nearest_lower_level"),
                    "nearest_upper_level": signal.get("nearest_upper_level"),
                    "confidence": 1.0,
                    "quantity": signal["quantity"],
                    "source": "tradingview",
                    "market_only": True,
                    "entry_order_type": "MARKET",
                },
                live=False,
            )
            execution.update(
                {
                    "status": "queued",
                    "request_id": request_id,
                    "message": "Market BUY queued for IBKR execute worker (no stop/target).",
                }
            )
        else:
            request_id = oq.submit_close(symbol, live=False)
            execution.update(
                {
                    "status": "queued",
                    "request_id": request_id,
                    "message": "SELL close queued for IBKR execute worker.",
                }
            )
    except Exception as exc:
        LOGGER.warning("TradingView stock webhook failed %s %s: %s", action, symbol, exc)
        execution["status"] = "error"
        execution["message"] = str(exc)

    _append_execution(state, execution)
    _save_state(state)
    return {
        "ok": execution["status"] == "queued",
        "execution": execution,
        "order_request_id": execution.get("request_id"),
    }
