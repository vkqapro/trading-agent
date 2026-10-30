"""Reconcile persisted Order Journal closes with an IBKR account-trades CSV.

This is a read/repair utility for broker-observed close facts.  It never
creates orders, approvals, execution intents, or strategy changes.  The CSV is
expected to be an authenticated Paper-account export containing ``sell_trade_id``
and ``pnl_realized`` columns.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value or "").replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return default


def _date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10]


def _key(symbol: Any, quantity: Any, price: Any, close_time: Any) -> tuple[str, float, float, str]:
    return (
        str(symbol or "").upper(),
        abs(_number(quantity)),
        round(_number(price), 8),
        _date(close_time),
    )


def _order_ids(result: dict[str, Any]) -> set[str]:
    return {
        str(result.get(name))
        for name in ("market_order_id", "stop_order_id", "limit_order_id")
        if result.get(name) not in (None, "")
    }


def _request_for(row: dict[str, str], requests: list[dict[str, Any]]) -> dict[str, Any] | None:
    order_id = str(row.get("sell_order_id") or "")
    symbol = str(row.get("symbol") or "").upper()
    quantity = _number(row.get("share_qty"))
    candidates: list[dict[str, Any]] = []
    for request in requests:
        if str(request.get("symbol") or "").upper() != symbol:
            continue
        result = request.get("result") if isinstance(request.get("result"), dict) else {}
        if order_id and order_id in _order_ids(result):
            return request
        if _number(request.get("quantity") or result.get("quantity")) == quantity:
            candidates.append(request)
    return candidates[0] if len(candidates) == 1 else None


def reconcile(csv_path: Path, closes_path: Path, requests_path: Path) -> dict[str, Any]:
    rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8-sig")))
    payload = json.loads(closes_path.read_text(encoding="utf-8")) if closes_path.exists() else {"positions": []}
    positions = payload.get("positions") if isinstance(payload, dict) else []
    if not isinstance(positions, list):
        positions = []
    requests_payload = json.loads(requests_path.read_text(encoding="utf-8")) if requests_path.exists() else {"requests": []}
    requests = requests_payload.get("requests") if isinstance(requests_payload, dict) else []
    if not isinstance(requests, list):
        requests = []

    by_execution = {
        str(position.get("exit_execution_id")): position
        for position in positions
        if isinstance(position, dict) and position.get("exit_execution_id")
    }
    by_key = {
        _key(position.get("symbol"), position.get("exit_quantity") or position.get("quantity"), position.get("exit_price"), position.get("closed_at")): position
        for position in positions
        if isinstance(position, dict) and position.get("closed_at")
    }
    updated = 0
    created = 0
    for row in rows:
        if str(row.get("sec_type") or "").upper() != "STK":
            continue
        trade_id = str(row.get("sell_trade_id") or "")
        if not trade_id:
            continue
        symbol = str(row.get("symbol") or "").upper()
        quantity = abs(_number(row.get("share_qty")))
        close_price = _number(row.get("close_price"))
        close_time = row.get("close_time_utc") or row.get("close_date") or ""
        position = by_execution.get(trade_id) or by_key.get(_key(symbol, quantity, close_price, close_time))
        request = _request_for(row, requests)
        request_id = str(request.get("id") or "") if request else ""
        if position is None:
            position = {
                "symbol": symbol,
                "quantity": quantity,
                "entry": _number(row.get("open_price"), default=0.0) or None,
                "avg_cost": _number(row.get("open_price"), default=0.0) or None,
                "opened_at": row.get("open_time_utc") or row.get("open_date") or None,
                "closed_at": close_time,
                "exit_price": close_price,
                "exit_quantity": quantity,
                "exit_order_id": row.get("sell_order_id") or "",
                "exit_execution_id": trade_id,
                "exit_reason": "BRACKET_STOP_FILLED" if str(row.get("close_order_type") or "").upper() == "STOP" else "BRACKET_TARGET_FILLED" if str(row.get("close_order_type") or "").upper() == "LIMIT" else "BROKER_SELL_EXECUTION",
                "request_id": request_id,
                "source": "IBKR_ACCOUNT_TRADES",
                "entry_price_source": "ibkr_account_trades",
            }
            positions.append(position)
            created += 1
        # Broker facts are authoritative; unrelated local strategy fields are
        # intentionally left untouched.
        position.update({
            "symbol": symbol,
            "quantity": quantity,
            "exit_quantity": quantity,
            "closed_at": close_time,
            "exit_price": close_price,
            "exit_order_id": row.get("sell_order_id") or position.get("exit_order_id") or "",
            "exit_execution_id": trade_id,
            "broker_realized_pnl": _number(row.get("pnl_realized")),
            "commission": _number(row.get("commission_allocated")),
            "fee": 0.0,
            "source": "IBKR_ACCOUNT_TRADES",
            "entry_price_source": "ibkr_account_trades",
        })
        if request_id:
            position["request_id"] = request_id
        if _number(row.get("open_price")):
            position["entry"] = _number(row.get("open_price"))
            position["avg_cost"] = _number(row.get("open_price"))
        if row.get("open_time_utc"):
            position["opened_at"] = row["open_time_utc"]
        by_execution[trade_id] = position
        by_key[_key(symbol, quantity, close_price, close_time)] = position
        updated += 1

    payload["positions"] = positions
    closes_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = closes_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, closes_path)
    return {"csv_rows": len(rows), "created": created, "updated": updated, "positions": len(positions)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--closes", type=Path, default=Path("memory/runtime/closed_positions.json"))
    parser.add_argument("--requests", type=Path, default=Path("memory/order_requests.json"))
    args = parser.parse_args()
    print(json.dumps(reconcile(args.csv_path, args.closes, args.requests), sort_keys=True))


if __name__ == "__main__":
    main()
