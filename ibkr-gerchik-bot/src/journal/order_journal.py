"""Unified local order journal for dashboard review.

This module is intentionally read-mostly. It derives a trader-review journal
from the stores the bot already writes:

* memory/order_requests.json for stock/forex dashboard + IBKR requests
* TradingView stock/forex state ledgers
* OKX crypto TradingView state ledger
* memory/runtime/state.json tracked positions

Manual review notes are stored separately in memory/order_journal_reviews.json so
analysis never mutates broker/execution history.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd

from src.config import MEMORY_DIR, SETTINGS
from src.crypto.config import CRYPTO_SETTINGS
from src.data.bar_store import load_bars

REVIEWS_PATH = MEMORY_DIR / "order_journal_reviews.json"
CLOSED_POSITIONS_PATH = MEMORY_DIR / "runtime" / "closed_positions.json"
MAX_ROWS = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _f(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        return number
    except Exception:
        return None


def _qty(value: Any) -> float | None:
    number = _f(value)
    return abs(number) if number is not None else None


def _fill_price_from_result(result: Any) -> float | None:
    """Return the best known fill/price from a broker result payload.

    IBKR/TWS responses are not perfectly uniform in this project. Some paths
    write a clean avgFillPrice/price field, while market-order reconciliation
    writes a detail string such as "Fill 1.0@133.37". The journal is an
    analysis surface, so it should recover that useful price when available.
    """
    if not isinstance(result, dict):
        return None
    for key in ("avgFillPrice", "avg_fill_price", "fill_price", "price", "entry"):
        value = _f(result.get(key))
        if value is not None:
            return value
    detail = str(result.get("detail") or "")
    match = re.search(r"@\s*([0-9]+(?:\.[0-9]+)?)", detail)
    if match:
        return _f(match.group(1))
    return None


def _result_has_fill(result: Any) -> bool:
    """Return whether a broker result contains evidence of an execution."""
    if not isinstance(result, dict):
        return False
    filled = _f(result.get("filled"))
    if filled is not None and filled > 0:
        return True
    for key in ("avgFillPrice", "avg_fill_price", "fill_price", "price"):
        price = _f(result.get(key))
        if price is not None and price > 0:
            return True
    broker_statuses = result.get("broker_statuses")
    if isinstance(broker_statuses, dict):
        return any(_upper(value) in {"FILLED", "EXECUTED"} for value in broker_statuses.values())
    return _upper(result.get("status")) in {"FILLED", "EXECUTED"}


def _order_request_ids() -> set[str]:
    """Return request ids already represented by the broker request store."""
    data = _read_json(MEMORY_DIR / "order_requests.json", {"requests": []})
    requests = data.get("requests") if isinstance(data, dict) else []
    if not isinstance(requests, list):
        return set()
    return {
        str(request.get("id"))
        for request in requests
        if isinstance(request, dict) and request.get("id")
    }


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _sort_key(row: Dict[str, Any]) -> str:
    return str(row.get("opened_at") or row.get("created_at") or row.get("closed_at") or "")


def _risk_per_share(side: str, entry: float | None, stop: float | None) -> float | None:
    if entry is None or stop is None:
        return None
    risk = abs(entry - stop)
    return risk if risk > 0 else None


def _planned_reward(side: str, entry: float | None, target: float | None) -> float | None:
    if entry is None or target is None:
        return None
    reward = abs(target - entry)
    return reward if reward > 0 else None


def _pnl(side: str, entry: float | None, exit_price: float | None, qty: float | None) -> float | None:
    if entry is None or exit_price is None or qty is None:
        return None
    if side == "SELL" or side == "SHORT":
        return (entry - exit_price) * qty
    return (exit_price - entry) * qty


def _r_multiple(pnl: float | None, entry: float | None, stop: float | None, qty: float | None) -> float | None:
    risk = _risk_per_share("BUY", entry, stop)
    if pnl is None or risk is None or qty is None or qty <= 0:
        return None
    denom = risk * qty
    return pnl / denom if denom else None


def _infer_bracket_exit_from_bars(
    *,
    symbol: str,
    side: str,
    opened_at: Any,
    stop: Any,
    target: Any,
) -> Dict[str, Any] | None:
    """Infer a bracket stop/target exit from saved 5-minute bars.

    IBKR bracket children can execute without this app writing a close request.
    When the runtime position disappears and no explicit close event exists, the
    journal can still mark the trade lifecycle by finding the first saved bar
    after entry that crossed the planned stop/target.
    """
    stop_price = _f(stop)
    target_price = _f(target)
    if stop_price is None and target_price is None:
        return None
    bars = load_bars(symbol, "intraday_5m")
    if bars is None or bars.empty or "date" not in bars:
        return None
    frame = bars.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date")
    opened = _parse_dt(opened_at)
    if opened is not None:
        opened_ts = pd.Timestamp(opened)
        if getattr(frame["date"].dt, "tz", None) is not None:
            opened_ts = opened_ts.tz_convert(frame["date"].dt.tz)
        else:
            opened_ts = opened_ts.tz_localize(None)
        frame = frame[frame["date"] >= opened_ts]
    if frame.empty:
        return None
    for column in ("high", "low"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    side_u = _upper(side)
    long_side = side_u not in {"SELL", "SHORT"}
    for _, bar in frame.iterrows():
        high = _f(bar.get("high"))
        low = _f(bar.get("low"))
        if high is None or low is None:
            continue
        stop_hit = bool(stop_price is not None and ((long_side and low <= stop_price) or ((not long_side) and high >= stop_price)))
        target_hit = bool(target_price is not None and ((long_side and high >= target_price) or ((not long_side) and low <= target_price)))
        if not stop_hit and not target_hit:
            continue
        if stop_hit and target_hit:
            # Without tick/fill data the true sequence inside one candle is
            # unknowable. Use the conservative stop outcome for risk review.
            return {
                "exit_price": stop_price,
                "closed_at": pd.Timestamp(bar["date"]).isoformat(),
                "exit_reason": "BRACKET_STOP_INFERRED_AMBIGUOUS_BAR",
            }
        if stop_hit:
            return {
                "exit_price": stop_price,
                "closed_at": pd.Timestamp(bar["date"]).isoformat(),
                "exit_reason": "BRACKET_STOP_INFERRED",
            }
        return {
            "exit_price": target_price,
            "closed_at": pd.Timestamp(bar["date"]).isoformat(),
            "exit_reason": "BRACKET_TARGET_INFERRED",
        }
    return None


def _base_row(
    *,
    journal_id: str,
    symbol: str,
    market: str,
    source: str,
    side: str,
    status: str,
    created_at: Any = None,
    opened_at: Any = None,
    closed_at: Any = None,
    planned_entry: Any = None,
    planned_stop: Any = None,
    planned_target: Any = None,
    current_stop: Any = None,
    current_target: Any = None,
    actual_entry: Any = None,
    actual_exit: Any = None,
    qty: Any = None,
    strategy: Any = None,
    exit_reason: str = "",
    request_id: str = "",
    execution_id: str = "",
    message: str = "",
    raw: Dict[str, Any] | None = None,
    actual_entry_fallback_to_plan: bool = True,
) -> Dict[str, Any]:
    pe = _f(planned_entry)
    ps = _f(planned_stop)
    pt = _f(planned_target)
    cs = _f(current_stop)
    ct = _f(current_target)
    ae = _f(actual_entry)
    if ae is None and actual_entry_fallback_to_plan:
        ae = pe
    ax = _f(actual_exit)
    q = _qty(qty)
    pnl = _pnl(side, ae, ax, q)
    risk = _risk_per_share(side, pe, ps)
    reward = _planned_reward(side, pe, pt)
    rr = reward / risk if reward is not None and risk else None
    return {
        "id": journal_id,
        "symbol": _upper(symbol),
        "market": market,
        "source": source,
        "strategy": str(strategy or ""),
        "side": _upper(side),
        "status": status,
        "exit_reason": exit_reason,
        "created_at": created_at,
        "opened_at": opened_at or created_at,
        "closed_at": closed_at,
        "planned_entry": pe,
        "planned_stop": ps,
        "planned_target": pt,
        "current_stop": cs,
        "current_target": ct,
        "actual_entry": ae,
        "actual_exit": ax,
        "quantity": q,
        "planned_risk_per_share": risk,
        "planned_reward_per_share": reward,
        "planned_reward_risk": rr,
        "planned_risk": risk * q if risk is not None and q is not None else None,
        "planned_reward": reward * q if reward is not None and q is not None else None,
        "pnl": pnl,
        "r_multiple": _r_multiple(pnl, pe, ps, q),
        "request_id": request_id,
        "execution_id": execution_id,
        "message": message,
        "raw": raw or {},
    }


def load_reviews() -> Dict[str, Any]:
    data = _read_json(REVIEWS_PATH, {"reviews": {}})
    reviews = data.get("reviews") if isinstance(data, dict) else {}
    return reviews if isinstance(reviews, dict) else {}


def save_review(journal_id: str, review: Dict[str, Any]) -> Dict[str, Any]:
    journal_id = str(journal_id or "").strip()
    if not journal_id:
        raise ValueError("journal_id is required")
    allowed = {
        "review_status",
        "grade",
        "mistake_type",
        "decision_quality",
        "notes",
    }
    clean = {key: review.get(key) for key in allowed if key in review}
    clean["updated_at"] = _now()
    data = _read_json(REVIEWS_PATH, {"reviews": {}})
    if not isinstance(data, dict):
        data = {"reviews": {}}
    reviews = data.setdefault("reviews", {})
    if not isinstance(reviews, dict):
        data["reviews"] = reviews = {}
    existing = reviews.get(journal_id, {}) if isinstance(reviews.get(journal_id), dict) else {}
    existing.update(clean)
    reviews[journal_id] = existing
    _write_json(REVIEWS_PATH, data)
    return existing


def _tracked_positions() -> List[Dict[str, Any]]:
    state = _read_json(MEMORY_DIR / "runtime" / "state.json", {})
    positions = state.get("tracked_positions") if isinstance(state, dict) else []
    return positions if isinstance(positions, list) else []


def _open_position_index() -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for p in _tracked_positions():
        if not isinstance(p, dict):
            continue
        symbol = _upper(p.get("symbol"))
        if symbol:
            index[symbol] = p
    return index


def _closed_positions_by_symbol() -> Dict[str, List[Dict[str, Any]]]:
    data = _read_json(CLOSED_POSITIONS_PATH, {"positions": []})
    positions = data.get("positions") if isinstance(data, dict) else []
    if not isinstance(positions, list):
        return {}
    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for position in positions:
        if not isinstance(position, dict):
            continue
        symbol = _upper(position.get("symbol"))
        # Older reconciliation versions wrote BROKER_POSITION_CLOSED when a
        # symbol was absent from one position snapshot. That is not enough to
        # prove a close: a working entry can have no position yet, and an API
        # snapshot can be incomplete. Keep only execution-backed records.
        if (
            symbol
            and not (
                _upper(position.get("exit_reason")) == "BROKER_POSITION_CLOSED"
                and not position.get("exit_execution_id")
            )
        ):
            by_symbol.setdefault(symbol, []).append(position)
    for items in by_symbol.values():
        items.sort(key=lambda item: str(item.get("closed_at") or ""), reverse=True)
    return by_symbol


def _order_ids(payload: Dict[str, Any]) -> set[str]:
    """Collect the parent and child broker ids for an order/position."""
    return {
        str(payload.get(key))
        for key in (
            "market_order_id",
            "order_id",
            "entry_order_id",
            "parent_order_id",
            "stop_order_id",
            "limit_order_id",
            "target_order_id",
        )
        if payload.get(key) not in (None, "", 0, "0")
    }


def _position_matches_request(position: Dict[str, Any] | None, result: Dict[str, Any]) -> bool:
    if not position:
        return False
    pos_order_ids = _order_ids(position)
    req_order_ids = _order_ids(result)
    if pos_order_ids and req_order_ids:
        return bool(pos_order_ids & req_order_ids)
    # If neither side recorded an order id, fall back to symbol-level matching.
    # Avoid doing that when only one side has an id because it creates false
    # OPEN rows for older same-symbol orders.
    return not pos_order_ids and not req_order_ids


def _position_opened_near_request(position: Dict[str, Any], request: Dict[str, Any]) -> bool:
    """Match a market-only request when broker ids were not persisted."""
    opened = _parse_dt(position.get("opened_at"))
    requested = _parse_dt(request.get("updated_at") or request.get("created_at"))
    if not opened or not requested:
        return False
    return abs((opened - requested).total_seconds()) <= 5 * 60


def _observed_close_matches_request(position: Dict[str, Any], request: Dict[str, Any], result: Dict[str, Any]) -> bool:
    pos_order_ids = _order_ids(position)
    req_order_ids = _order_ids(result)
    if pos_order_ids and req_order_ids:
        return bool(pos_order_ids & req_order_ids)

    opened = _parse_dt(position.get("opened_at"))
    requested = _parse_dt(request.get("updated_at") or request.get("created_at"))
    closed = _parse_dt(position.get("closed_at"))
    if requested and opened and abs((opened - requested).total_seconds()) <= 24 * 60 * 60:
        return True
    if requested and closed and closed >= requested:
        return True
    return not pos_order_ids and not req_order_ids


def _rows_from_order_requests(open_positions: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    data = _read_json(MEMORY_DIR / "order_requests.json", {"requests": []})
    requests = data.get("requests") if isinstance(data, dict) else []
    if not isinstance(requests, list):
        return []
    rows: List[Dict[str, Any]] = []
    close_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    observed_close_by_symbol = _closed_positions_by_symbol()
    for req in requests:
        if isinstance(req, dict) and req.get("action") == "close":
            close_by_symbol.setdefault(_upper(req.get("symbol")), []).append(req)
    for req in requests:
        if not isinstance(req, dict) or req.get("action") != "place":
            continue
        symbol = _upper(req.get("symbol"))
        result = req.get("result") if isinstance(req.get("result"), dict) else {}
        status_raw = str(req.get("status") or "").lower()
        candidate_open_pos = open_positions.get(symbol)
        open_pos = candidate_open_pos if _position_matches_request(candidate_open_pos, result) else None
        has_fill = _result_has_fill(result)
        if (
            not open_pos
            and candidate_open_pos
            and _upper(req.get("source")) == "TRADINGVIEW"
            and bool(req.get("market_only"))
            and has_fill
            and _position_opened_near_request(candidate_open_pos, req)
        ):
            open_pos = candidate_open_pos
        # TradingView market orders can be marked DONE when they were only
        # submitted to TWS. Do not turn a zero-fill historical request into a
        # journal trade; it has no entry price and cannot be analyzed.
        if (
            _upper(req.get("source")) == "TRADINGVIEW"
            and bool(req.get("market_only"))
            and status_raw in {"done", "simulated"}
            and not open_pos
            and not has_fill
        ):
            continue
        related_close = next(
            (
                c
                for c in close_by_symbol.get(symbol, [])
                if str(c.get("created_at") or "") >= str(req.get("created_at") or "")
                and _upper(c.get("status")) in {"DONE", "SIMULATED"}
            ),
            None,
        )
        observed_close = None if open_pos or related_close else next(
            (
                position
                for position in observed_close_by_symbol.get(symbol, [])
                if _observed_close_matches_request(position, req, result)
            ),
            None,
        )
        status = (
            "OPEN"
            if open_pos
            else "CLOSED"
            if related_close
            else "CLOSED_OBSERVED"
            if observed_close
            else "UNFILLED"
            if (
                status_raw in {"done", "simulated"}
                and not has_fill
                and "MARKET_SCREENER" in _upper(req.get("source"))
            )
            else "CLOSED_UNKNOWN"
            if status_raw in {"done", "simulated"}
            else status_raw.upper()
        )
        actual_exit = None
        closed_at = None
        exit_reason = ""
        observed_entry = None
        observed_quantity = None
        observed_execution_id = ""
        if related_close:
            close_result = related_close.get("result") if isinstance(related_close.get("result"), dict) else {}
            actual_exit = _fill_price_from_result(close_result)
            closed_at = related_close.get("updated_at") or related_close.get("created_at")
            exit_reason = "MANUAL_CLOSE" if related_close.get("source") == "dashboard" else "CLOSE_REQUEST"
        elif observed_close:
            closed_at = observed_close.get("closed_at")
            observed_entry = observed_close.get("avg_cost") or observed_close.get("entry")
            actual_exit = _f(observed_close.get("exit_price") or observed_close.get("actual_exit"))
            observed_quantity = observed_close.get("exit_quantity") or observed_close.get("quantity")
            observed_execution_id = str(observed_close.get("exit_execution_id") or "")
            exit_reason = str(observed_close.get("exit_reason") or "BROKER_POSITION_CLOSED")
        elif not open_pos and status_raw == "simulated":
            inferred = _infer_bracket_exit_from_bars(
                symbol=symbol,
                side=req.get("signal"),
                opened_at=req.get("updated_at") or req.get("created_at"),
                stop=req.get("stop"),
                target=req.get("target"),
            )
            if inferred:
                status = "CLOSED_STOP" if "STOP" in inferred.get("exit_reason", "") else "CLOSED_TARGET"
                actual_exit = inferred.get("exit_price")
                closed_at = inferred.get("closed_at")
                exit_reason = inferred.get("exit_reason", "BRACKET_EXIT_INFERRED")
            else:
                status = "CLOSED_UNKNOWN"
                exit_reason = "UNKNOWN_OR_BRACKET"
        elif not open_pos and status_raw == "done" and status != "UNFILLED":
            exit_reason = "UNKNOWN_OR_BROKER"
        rows.append(
            _base_row(
                journal_id=f"stock:req:{req.get('id')}",
                symbol=symbol,
                market="STOCK",
                source=str(req.get("source") or "dashboard"),
                side=req.get("signal"),
                status=status,
                created_at=req.get("created_at"),
                opened_at=(open_pos or {}).get("opened_at") or req.get("updated_at") or req.get("created_at"),
                closed_at=closed_at,
                planned_entry=req.get("entry"),
                planned_stop=req.get("stop"),
                planned_target=req.get("target"),
                current_stop=(open_pos or {}).get("current_stop_loss"),
                current_target=(open_pos or {}).get("current_target"),
                actual_entry=(open_pos or {}).get("avg_cost") or observed_entry or (_fill_price_from_result(result) if has_fill else None),
                actual_entry_fallback_to_plan=bool(open_pos or observed_close or has_fill),
                actual_exit=actual_exit,
                qty=(open_pos or {}).get("quantity") or observed_quantity or result.get("quantity") or req.get("quantity"),
                strategy=req.get("strategy"),
                exit_reason=exit_reason,
                request_id=str(req.get("id") or ""),
                execution_id=observed_execution_id,
                message=str(req.get("message") or ""),
                raw=req,
            )
        )
    return rows


def _rows_from_tv_state(
    path: Path,
    *,
    market: str,
    source: str,
    linked_request_ids: set[str] | None = None,
) -> List[Dict[str, Any]]:
    state = _read_json(path, {"executions": []})
    executions = state.get("executions") if isinstance(state, dict) else []
    if not isinstance(executions, list):
        return []
    rows = []
    for ex in executions:
        if not isinstance(ex, dict):
            continue
        action = _upper(ex.get("action"))
        symbol = _upper(ex.get("symbol") or ex.get("inst_id") or ex.get("ticker"))
        request_id = str(ex.get("request_id") or "")
        if request_id and request_id in (linked_request_ids or set()):
            # The broker request row is the canonical trade record. Keeping
            # this alert copy would create a second row for the same trade.
            continue
        status = str(ex.get("status") or "").upper()
        if action == "SELL":
            status = "CLOSE_ALERT" if status in {"QUEUED", "SUBMITTED"} else status
        elif action == "BUY" and status in {"QUEUED", "SUBMITTED"}:
            status = "OPEN_ALERT"
        rows.append(
            _base_row(
                journal_id=f"{market.lower()}:tv:{ex.get('id')}",
                symbol=symbol,
                market=market,
                source=source,
                side=action,
                status=status,
                created_at=ex.get("created_at"),
                opened_at=ex.get("opened_at") or ex.get("created_at"),
                closed_at=ex.get("created_at") if action == "SELL" else None,
                planned_entry=ex.get("entry") or ex.get("price"),
                planned_stop=ex.get("stop"),
                planned_target=ex.get("target"),
                actual_entry=ex.get("entry") or ex.get("price"),
                actual_exit=ex.get("price") if action == "SELL" else None,
                qty=ex.get("estimated_qty") or ex.get("quantity") or ex.get("tv_position_size"),
                strategy="tradingview_webhook",
                exit_reason="TRADINGVIEW_SELL" if action == "SELL" else "",
                request_id=request_id,
                execution_id=str(ex.get("id") or ""),
                message=str(ex.get("message") or ""),
                raw=ex,
            )
        )
    return rows


def _rows_from_crypto_state() -> List[Dict[str, Any]]:
    state = _read_json(CRYPTO_SETTINGS.memory_dir / "runtime" / "tradingview_okx_state.json", {"executions": []})
    executions = state.get("executions") if isinstance(state, dict) else []
    if not isinstance(executions, list):
        return []
    rows: List[Dict[str, Any]] = []
    submitted_buys: List[Dict[str, Any]] = []
    closed_buy_keys: set[str] = set()

    # Submitted SELL records in the OKX webhook ledger carry the original
    # opened_at/entry/estimated_qty/estimated_pnl. Represent those as one
    # round-trip row instead of two disconnected alert events.
    ordered = sorted(
        (ex for ex in executions if isinstance(ex, dict)),
        key=lambda ex: str(ex.get("created_at") or ex.get("tv_timestamp") or ""),
    )
    for ex in ordered:
        if not isinstance(ex, dict):
            continue
        action = _upper(ex.get("action"))
        status = str(ex.get("status") or "").upper()
        symbol = _upper(ex.get("inst_id") or ex.get("ticker"))
        if action == "BUY" and status == "SUBMITTED":
            submitted_buys.append(ex)
            continue
        if action == "SELL" and status == "SUBMITTED":
            opened_at = ex.get("opened_at")
            entry = _f(ex.get("entry"))
            qty = _qty(ex.get("estimated_qty") or ex.get("tv_position_size"))
            exit_price = _f(ex.get("price"))
            buy_match = next(
                (
                    buy
                    for buy in submitted_buys
                    if _upper(buy.get("inst_id") or buy.get("ticker")) == symbol
                    and str(buy.get("created_at")) == str(opened_at)
                ),
                None,
            )
            if buy_match:
                closed_buy_keys.add(f"{symbol}|{buy_match.get('created_at')}|{buy_match.get('id')}")
            row = _base_row(
                journal_id=f"crypto:okx:roundtrip:{(buy_match or {}).get('id') or opened_at}:{ex.get('id')}",
                symbol=symbol,
                market="CRYPTO",
                source="TradingView OKX",
                side="BUY",
                status="CLOSED",
                created_at=(buy_match or {}).get("created_at") or opened_at or ex.get("created_at"),
                opened_at=opened_at or (buy_match or {}).get("created_at"),
                closed_at=ex.get("created_at"),
                planned_entry=entry or (buy_match or {}).get("price"),
                actual_entry=entry or (buy_match or {}).get("price"),
                actual_exit=exit_price,
                qty=qty,
                strategy="tradingview_okx_webhook",
                exit_reason="TRADINGVIEW_SELL",
                request_id=str(ex.get("okx_order_id") or ""),
                execution_id=str(ex.get("id") or ""),
                message=str(ex.get("message") or ""),
                raw={"open": buy_match or {}, "close": ex},
            )
            if _f(ex.get("estimated_pnl")) is not None:
                row["pnl"] = _f(ex.get("estimated_pnl"))
            rows.append(row)
            continue

        # Keep non-round-trip events (errors/skips/unpaired sells) visible for
        # audit/debugging without pretending they were completed trades.
        rows.append(
            _base_row(
                journal_id=f"crypto:okx:{ex.get('id')}",
                symbol=symbol,
                market="CRYPTO",
                source="TradingView OKX",
                side=action,
                status=status,
                created_at=ex.get("created_at"),
                opened_at=ex.get("opened_at") or ex.get("created_at"),
                closed_at=None,
                planned_entry=ex.get("entry") or ex.get("price"),
                actual_entry=ex.get("entry") or ex.get("price"),
                actual_exit=None,
                qty=ex.get("estimated_qty") or ex.get("tv_position_size"),
                strategy="tradingview_okx_webhook",
                exit_reason="",
                request_id=str(ex.get("okx_order_id") or ""),
                execution_id=str(ex.get("id") or ""),
                message=str(ex.get("message") or ""),
                raw=ex,
            )
        )
    for buy in submitted_buys:
        symbol = _upper(buy.get("inst_id") or buy.get("ticker"))
        key = f"{symbol}|{buy.get('created_at')}|{buy.get('id')}"
        if key in closed_buy_keys:
            continue
        rows.append(
            _base_row(
                journal_id=f"crypto:okx:{buy.get('id')}",
                symbol=symbol,
                market="CRYPTO",
                source="TradingView OKX",
                side="BUY",
                status="OPEN",
                created_at=buy.get("created_at"),
                opened_at=buy.get("created_at"),
                planned_entry=buy.get("price"),
                actual_entry=buy.get("price"),
                qty=buy.get("estimated_qty") or buy.get("tv_position_size"),
                strategy="tradingview_okx_webhook",
                request_id=str(buy.get("okx_order_id") or ""),
                execution_id=str(buy.get("id") or ""),
                message=str(buy.get("message") or ""),
                raw=buy,
            )
        )
    return rows


def _merge_reviews(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    reviews = load_reviews()
    for row in rows:
        review = reviews.get(str(row.get("id")), {})
        if not isinstance(review, dict):
            review = {}
        row["review"] = {
            "review_status": review.get("review_status") or "Unreviewed",
            "grade": review.get("grade") or "",
            "mistake_type": review.get("mistake_type") or "",
            "decision_quality": review.get("decision_quality") or {},
            "notes": review.get("notes") or "",
            "updated_at": review.get("updated_at"),
        }
    return rows


def load_order_journal(*, limit: int = MAX_ROWS) -> Dict[str, Any]:
    open_positions = _open_position_index()
    linked_request_ids = _order_request_ids()
    rows: List[Dict[str, Any]] = []
    rows.extend(_rows_from_order_requests(open_positions))
    rows.extend(
        _rows_from_tv_state(
            MEMORY_DIR / "runtime" / "tradingview_stock_state.json",
            market="STOCK",
            source="TradingView Stock",
            linked_request_ids=linked_request_ids,
        )
    )
    rows.extend(
        _rows_from_tv_state(
            MEMORY_DIR / "runtime" / "tradingview_forex_state.json",
            market="FOREX",
            source="TradingView Forex",
            linked_request_ids=linked_request_ids,
        )
    )
    rows.extend(_rows_from_crypto_state())

    seen = set()
    unique: List[Dict[str, Any]] = []
    for row in rows:
        rid = str(row.get("id"))
        if not rid or rid in seen:
            continue
        seen.add(rid)
        unique.append(row)
    unique.sort(key=_sort_key, reverse=True)
    unique = _merge_reviews(unique[: max(1, int(limit or MAX_ROWS))])

    closed = [r for r in unique if str(r.get("status", "")).upper().startswith("CLOSED") or r.get("closed_at")]
    openish = [r for r in unique if str(r.get("status", "")).upper() in {"OPEN", "SUBMITTED", "OPEN_ALERT"}]
    pnl_values = [r.get("pnl") for r in unique if isinstance(r.get("pnl"), (int, float))]
    r_values = [r.get("r_multiple") for r in unique if isinstance(r.get("r_multiple"), (int, float))]
    wins = [v for v in pnl_values if v > 0]
    return {
        "updated_at": _now(),
        "rows": unique,
        "metrics": {
            "total": len(unique),
            "open": len(openish),
            "closed": len(closed),
            "unreviewed": sum(1 for r in unique if (r.get("review") or {}).get("review_status") == "Unreviewed"),
            "total_pnl": sum(pnl_values) if pnl_values else 0.0,
            "win_rate": (len(wins) / len(pnl_values)) if pnl_values else None,
            "avg_r": (sum(r_values) / len(r_values)) if r_values else None,
        },
    }
