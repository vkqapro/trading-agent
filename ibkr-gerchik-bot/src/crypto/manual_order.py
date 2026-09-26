"""Durable, demo-only execution for Crypto Strategy manual orders."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from src.config import LOGGER
from src.crypto.config import CRYPTO_SETTINGS, ensure_crypto_directories
from src.crypto.okx_client import OKXClient, OKXInstrument
from src.crypto.order_manager import CryptoOrderManager, CryptoOrderRequest


STATE_PATH = CRYPTO_SETTINGS.memory_dir / "runtime" / "manual_okx_orders.json"
MAX_ORDERS = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_template() -> Dict[str, Any]:
    return {"updated_at": None, "orders": []}


def load_manual_order_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return _state_template()
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return _state_template()
        state.setdefault("updated_at", None)
        state.setdefault("orders", [])
        if not isinstance(state["orders"], list):
            state["orders"] = []
        return state
    except (OSError, json.JSONDecodeError):
        return _state_template()


def _save_state(state: Dict[str, Any]) -> None:
    ensure_crypto_directories()
    state["updated_at"] = _now()
    state["orders"] = list(state.get("orders") or [])[:MAX_ORDERS]
    temp = STATE_PATH.with_name(f"{STATE_PATH.name}.{os.getpid()}.{uuid4().hex}.tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temp, STATE_PATH)


def _decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _floor_to_step(value: Any, step: Any) -> Decimal:
    amount = _decimal(value)
    increment = _decimal(step)
    if increment <= 0:
        increment = Decimal("0.00000001")
    return (amount / increment).to_integral_value(rounding=ROUND_DOWN) * increment


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f").rstrip("0").rstrip(".")
    return rendered or "0"


def _client_order_id(prefix: str, symbol: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]", "", f"sm{prefix}{symbol}")[:18]
    return f"{token}{uuid4().hex[:12]}"[:32]


def _order_id(response: Dict[str, Any]) -> str | None:
    rows = response.get("data") if isinstance(response, dict) else None
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return str(rows[0].get("ordId") or "") or None
    return None


def _instrument(client: OKXClient, symbol: str) -> OKXInstrument:
    for instrument in client.instruments("SPOT"):
        if instrument.inst_id.upper() == symbol.upper():
            return instrument
    raise ValueError(f"OKX SPOT instrument is unavailable: {symbol}")


def execute_manual_demo_order(
    request: CryptoOrderRequest,
    *,
    order_type: str,
    client: OKXClient | None = None,
) -> Dict[str, Any]:
    """Validate, persist, and submit one OKX demo SPOT order.

    This function deliberately has no production mode. It fails closed unless
    ``OKX_SIMULATED_TRADING`` is true, and the OKX client then signs the request
    with ``x-simulated-trading: 1``.
    """
    normalized_type = str(order_type or "LIMIT").strip().upper()
    plan = CryptoOrderManager().plan_order(request)
    local_id = uuid4().hex
    record: Dict[str, Any] = {
        "id": local_id,
        "created_at": _now(),
        "updated_at": _now(),
        "source": "CRYPTO_STRATEGY_MANUAL",
        "status": "planned" if plan.get("ok") else "rejected",
        "paper": True,
        "demo": True,
        "dry_run": False,
        "symbol": plan.get("symbol"),
        "side": plan.get("side"),
        "order_type": normalized_type,
        "entry": plan.get("entry"),
        "stop": plan.get("stop"),
        "target": plan.get("target"),
        "quantity": plan.get("quantity"),
        "plan": plan,
        "okx_order_id": None,
        "client_order_id": None,
        "attached_algo_client_order_id": None,
        "message": "Order passed local risk validation." if plan.get("ok") else (
            f"Crypto order rejected: {', '.join(plan.get('reasons') or ['invalid setup'])}."
        ),
    }
    state = load_manual_order_state()
    state["orders"].insert(0, record)
    _save_state(state)

    if not plan.get("ok"):
        return {"ok": False, **record}

    try:
        if not CRYPTO_SETTINGS.okx_simulated_trading:
            raise RuntimeError("OKX_SIMULATED_TRADING must stay true; live crypto orders are disabled.")
        if CRYPTO_SETTINGS.okx_account_mode != "spot":
            raise RuntimeError("Manual Crypto Strategy orders currently require OKX_ACCOUNT_MODE=spot.")
        if normalized_type not in {"MARKET", "LIMIT"}:
            raise ValueError("Crypto entry order type must be MARKET or LIMIT.")
        if str(plan.get("side") or "").upper() != "BUY":
            raise ValueError("SHORT/SELL entry is not supported in OKX SPOT mode; only LONG/BUY can open a position.")

        okx = client or OKXClient()
        instrument = _instrument(okx, str(plan["symbol"]))
        if instrument.state and instrument.state.lower() != "live":
            raise RuntimeError(f"OKX instrument {instrument.inst_id} is not live (state={instrument.state}).")

        quantity = _floor_to_step(plan["quantity"], instrument.lot_sz)
        minimum = _decimal(instrument.min_sz)
        if quantity <= 0 or (minimum > 0 and quantity < minimum):
            raise ValueError(
                f"Order quantity {_decimal_text(quantity)} is below OKX minimum {instrument.min_sz or 'unknown'} {instrument.base_ccy}."
            )

        entry = _floor_to_step(plan["entry"], instrument.tick_sz)
        stop = _floor_to_step(plan["stop"], instrument.tick_sz)
        target = _floor_to_step(plan["target"], instrument.tick_sz) if plan.get("target") is not None else None
        client_order_id = _client_order_id("buy", instrument.inst_id)
        algo_client_order_id = _client_order_id("protect", instrument.inst_id)

        record.update({
            "status": "submitting",
            "updated_at": _now(),
            "quantity": float(quantity),
            "entry": float(entry),
            "stop": float(stop),
            "target": float(target) if target is not None else None,
            "client_order_id": client_order_id,
            "attached_algo_client_order_id": algo_client_order_id,
            "message": "Submitting demo-only OKX SPOT order.",
        })
        _save_state(state)

        response = okx.place_spot_order(
            inst_id=instrument.inst_id,
            side="buy",
            size=_decimal_text(quantity),
            order_type=normalized_type,
            price=_decimal_text(entry) if normalized_type == "LIMIT" else None,
            client_order_id=client_order_id,
            stop=_decimal_text(stop),
            target=_decimal_text(target) if target is not None else None,
            attached_algo_client_order_id=algo_client_order_id,
        )
        exchange_order_id = _order_id(response)
        if not exchange_order_id:
            raise RuntimeError("OKX accepted the request without returning an order ID.")

        record.update({
            "status": "submitted",
            "updated_at": _now(),
            "okx_order_id": exchange_order_id,
            "protection_attached": True,
            "message": (
                f"OKX Demo {normalized_type} BUY submitted for {instrument.inst_id}; "
                f"attached stop/target will activate after the entry fills."
            ),
        })
        _save_state(state)
        return {"ok": True, **record}
    except Exception as exc:
        LOGGER.warning("Manual OKX demo order failed %s %s: %s", plan.get("side"), plan.get("symbol"), exc)
        record.update({
            "status": "error",
            "updated_at": _now(),
            "message": str(exc),
        })
        _save_state(state)
        return {"ok": False, **record}
