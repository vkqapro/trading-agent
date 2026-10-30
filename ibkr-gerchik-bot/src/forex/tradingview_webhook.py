"""TradingView webhook bridge for IBKR Forex alerts.

This is intentionally separate from the stock webhook: Forex symbols are CASH
contracts at IBKR, use pair symbols like ``EUR.USD``, and use quantity as base
currency units. The webhook only queues requests; the existing execute worker
is responsible for connecting to IBKR and writing the final result.
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

STATE_PATH = MEMORY_DIR / "runtime" / "tradingview_forex_state.json"
MAX_EXECUTIONS = 200


class ForexTradingViewWebhookError(ValueError):
    """Raised when a Forex TradingView webhook payload is invalid or unsafe."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_template() -> Dict[str, Any]:
    return {"updated_at": None, "executions": []}


def load_forex_tradingview_state() -> Dict[str, Any]:
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
        text = str(value).strip().replace(",", "").replace("%", "")
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


def _normalize_forex_symbol(raw: Any) -> str:
    symbol = str(raw or "").strip().upper()
    if ":" in symbol:
        symbol = symbol.rsplit(":", 1)[-1]
    symbol = symbol.replace("/", ".").replace("-", ".")
    symbol = re.sub(r"[^A-Z.]", "", symbol)
    pair = fx_pair_components(symbol)
    if not pair:
        return ""
    return f"{pair[0]}.{pair[1]}"


def _expected_bot_id() -> str:
    return (
        os.getenv("FOREX_TRADINGVIEW_BOT_ID", "").strip()
        or os.getenv("STOCK_TRADINGVIEW_BOT_ID", "").strip()
        or os.getenv("CRYPTO_TRADINGVIEW_BOT_ID", "").strip()
    )


def validate_forex_tradingview_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ForexTradingViewWebhookError("TradingView webhook body must be a JSON object.")

    expected_bot_id = _expected_bot_id()
    if not expected_bot_id:
        raise ForexTradingViewWebhookError("FOREX_TRADINGVIEW_BOT_ID, STOCK_TRADINGVIEW_BOT_ID, or CRYPTO_TRADINGVIEW_BOT_ID is not configured.")
    received_bot_id = str(payload.get("bot_id", "")).strip()
    if received_bot_id != expected_bot_id:
        raise PermissionError("TradingView bot_id does not match the configured Forex webhook bot id.")

    action = str(payload.get("action", "")).strip().upper()
    if action not in {"BUY", "SELL"}:
        raise ForexTradingViewWebhookError("TradingView action must be BUY or SELL.")

    symbol = _normalize_forex_symbol(payload.get("ticker") or payload.get("symbol"))
    if not symbol:
        raise ForexTradingViewWebhookError("TradingView ticker is not a supported Forex pair.")

    quantity = (
        _as_int(payload.get("quantity"))
        or _as_int(payload.get("qty"))
        or _as_int(payload.get("units"))
        or _as_int(payload.get("position_size"))
    )
    if action == "BUY" and (quantity is None or quantity <= 0):
        raise ForexTradingViewWebhookError("Forex BUY alert requires quantity/units/position_size greater than zero.")

    return {
        "action": action,
        "symbol": symbol,
        "quantity": quantity,
        "order_size": str(payload.get("order_size", "")).strip(),
        "position_size": _as_float(payload.get("position_size")),
        "schema": str(payload.get("schema", "")).strip(),
        "timestamp": str(payload.get("timestamp", "")).strip(),
        "bot_id": received_bot_id,
        "raw": payload,
    }


def handle_forex_tradingview_webhook(payload: Dict[str, Any]) -> Dict[str, Any]:
    signal = validate_forex_tradingview_payload(payload)
    state = load_forex_tradingview_state()
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
        "quantity": signal.get("quantity"),
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
                    "strategy": "tradingview_forex_webhook",
                    "signal": "BUY",
                    "direction": "LONG",
                    "entry": None,
                    "stop": None,
                    "target": None,
                    "level_type": "tradingview_forex",
                    "confidence": 1.0,
                    "quantity": signal["quantity"],
                    "source": "forex_tradingview",
                    "market_only": True,
                    "entry_order_type": "MARKET",
                },
                live=False,
            )
            execution.update({
                "status": "queued",
                "request_id": request_id,
                "message": "Forex market BUY queued for IBKR execute worker.",
            })
        else:
            request_id = oq.submit_close(symbol, live=False)
            execution.update({
                "status": "queued",
                "request_id": request_id,
                "message": "Forex SELL close queued for IBKR execute worker.",
            })
    except Exception as exc:
        LOGGER.warning("TradingView Forex webhook failed %s %s: %s", action, symbol, exc)
        execution["status"] = "error"
        execution["message"] = str(exc)

    _append_execution(state, execution)
    _save_state(state)
    return {
        "ok": execution["status"] == "queued",
        "execution": execution,
        "order_request_id": execution.get("request_id"),
    }

