"""Worker that executes dashboard order requests through the bot's OrderManager.

Safety model (authoritative here, not in the UI):

* **Paper only** — any request is rejected unless ``PAPER_TRADING`` is true.
* **Respect DRY_RUN** — a request runs simulated when ``DRY_RUN_MODE`` is true,
  unless it was explicitly marked ``live`` from the dashboard's confirm toggle.
* Every request is isolated in a try/except so one failure can't stall the loop.
"""

from __future__ import annotations

import time
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, Dict, List, Optional

from src.config import LOGGER, SETTINGS
from src.execution import order_requests as oq
from src.execution.order_manager import OrderManager
from src.jobs.session_utils import calculate_open_risk_amount
from src.strategy.signal_models import TradeSignal


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _effective_dry_run(request: Dict[str, Any]) -> bool:
    return False if request.get("live") else SETTINGS.dry_run_mode


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return {key: val for key, val in vars(value).items() if not key.startswith("_")}
    return value


def _signal_from_request(request: Dict[str, Any]) -> TradeSignal:
    return TradeSignal(
        symbol=str(request.get("symbol", "")).upper(),
        strategy=str(request.get("strategy") or "dashboard_manual"),
        signal=str(request.get("signal", "")).upper(),
        direction=str(request.get("direction") or ""),
        entry=_f(request.get("entry")),
        stop=_f(request.get("stop")),
        target=_f(request.get("target")),
        level_price=_f(request.get("level_price")),
        level_type=str(request.get("level_type") or "dashboard"),
        nearest_upper_level=(
            _f(request.get("nearest_upper_level")) if request.get("nearest_upper_level") is not None else None
        ),
        nearest_lower_level=(
            _f(request.get("nearest_lower_level")) if request.get("nearest_lower_level") is not None else None
        ),
        reward_risk=_f(request.get("reward_risk")),
        confidence=_f(request.get("confidence")),
    )


def _tracked_position_from_payload(payload: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
    signal = payload.get("signal", {}) if isinstance(payload.get("signal"), dict) else {}
    return {
        "symbol": str(request.get("symbol", "")).upper(),
        "quantity": signal.get("quantity") or payload.get("quantity"),
        "entry": request.get("entry"),
        "stop_loss": request.get("stop"),
        "target": request.get("target"),
        "side": request.get("signal"),
        "strategy": request.get("strategy"),
        "source": "dashboard",
        "opened_at": oq._now(),
        "market_order_id": payload.get("market_order_id"),
        "stop_order_id": payload.get("stop_order_id"),
        "limit_order_id": payload.get("limit_order_id"),
    }


def _process_place(
    request: Dict[str, Any],
    *,
    order_manager: OrderManager,
    account_equity: float,
    cash_available: float,
    tracked_positions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    signal = _signal_from_request(request)
    if signal.signal not in {"BUY", "SELL"} or signal.entry <= 0 or signal.stop <= 0:
        return {"status": oq.ERROR, "message": "Invalid setup (need BUY/SELL with entry and stop).", "result": None}

    dry_run = _effective_dry_run(request)
    order_manager.dry_run = dry_run
    requested_qty = request.get("quantity")
    try:
        requested_qty = int(requested_qty) if requested_qty is not None else None
    except (TypeError, ValueError):
        requested_qty = None
    # Dashboard orders are explicit human decisions: use the manual-override path
    # that bypasses the autonomous signal-quality gates (spread, news, ATR,
    # reward:risk, market hours) and places the size the user reviewed.
    ok, payload = order_manager.execute_manual_order(
        signal,
        account_equity=account_equity,
        cash_available=cash_available,
        quantity=requested_qty,
        allow_extended_hours_order=True,
        time_in_force="GTC",
        entry_order_type=str(request.get("entry_order_type") or "MARKET"),
    )
    payload = payload if isinstance(payload, dict) else {"status": "unknown"}
    payload_status = str(payload.get("status", "")).lower()

    if ok and payload_status == "simulated":
        qty = payload.get("quantity")
        return {"status": oq.SIMULATED,
                "message": f"Simulated fill (DRY_RUN); no live order placed (qty {qty}).",
                "result": payload}
    if ok:
        tracked_positions.append(_tracked_position_from_payload(payload, request))
        qty = payload.get("quantity")
        return {"status": oq.DONE, "message": f"Order submitted to paper account (qty {qty}).", "result": payload}
    reasons = payload.get("reasons") or [payload.get("status", "rejected")]
    return {"status": oq.REJECTED, "message": "; ".join(str(r) for r in reasons), "result": payload}


def _process_close(
    request: Dict[str, Any],
    *,
    broker: Any,
    tracked_positions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    symbol = str(request.get("symbol", "")).upper()
    position = next(
        (p for p in broker.get_positions() if str(p.get("symbol", "")).upper() == symbol and _f(p.get("position")) != 0),
        None,
    )
    if position is None:
        return {"status": oq.ERROR, "message": f"No open broker position for {symbol}.", "result": None}

    quantity = _f(position.get("position"))
    action = "SELL" if quantity > 0 else "BUY"
    abs_qty = int(abs(quantity))
    if abs_qty <= 0:
        return {"status": oq.ERROR, "message": f"Position size for {symbol} is zero.", "result": None}

    if _effective_dry_run(request):
        return {
            "status": oq.SIMULATED,
            "message": f"Simulated close of {abs_qty} {symbol} ({action}); DRY_RUN, no live order.",
            "result": {"symbol": symbol, "action": action, "quantity": abs_qty, "status": "simulated"},
        }

    result = broker.place_market_order(symbol, action, abs_qty)
    tracked_positions[:] = [
        p for p in tracked_positions if str(p.get("symbol", "")).upper() != symbol
    ]
    return {
        "status": oq.DONE,
        "message": f"Close order submitted: {action} {abs_qty} {symbol}.",
        "result": _serialize(result),
    }


def _process_one(
    request: Dict[str, Any],
    *,
    broker: Any,
    order_manager: OrderManager,
    account_equity: float,
    cash_available: float,
    tracked_positions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not SETTINGS.paper_trading:
        return {
            "status": oq.REJECTED,
            "message": "Refused: live trading from the dashboard is not permitted (PAPER_TRADING is false).",
            "result": None,
        }
    action = request.get("action")
    if action == "place":
        return _process_place(
            request,
            order_manager=order_manager,
            account_equity=account_equity,
            cash_available=cash_available,
            tracked_positions=tracked_positions,
        )
    if action == "close":
        return _process_close(request, broker=broker, tracked_positions=tracked_positions)
    return {"status": oq.ERROR, "message": f"Unknown request action '{action}'.", "result": None}


def process_pending_once(
    broker: Any,
    order_manager: OrderManager,
    account_equity: float,
    cash_available: float,
    tracked_positions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Claim and execute all pending requests; write results back. Returns summaries."""
    claimed = oq.claim_pending()
    processed: List[Dict[str, Any]] = []
    for request in claimed:
        try:
            outcome = _process_one(
                request,
                broker=broker,
                order_manager=order_manager,
                account_equity=account_equity,
                cash_available=cash_available,
                tracked_positions=tracked_positions,
            )
        except Exception as exc:  # never let one request stall the worker
            LOGGER.exception("Order request %s failed", request.get("id"))
            outcome = {"status": oq.ERROR, "message": f"Worker error: {exc}", "result": None}
        oq.update_request(
            str(request.get("id")),
            status=outcome["status"],
            message=outcome.get("message", ""),
            result=outcome.get("result"),
        )
        LOGGER.info(
            "Processed %s request %s for %s -> %s",
            request.get("action"), request.get("id"), request.get("symbol"), outcome["status"],
        )
        processed.append({**request, **outcome})
    return processed


def run_execute_requests(
    broker: Any,
    order_manager: OrderManager,
    state: Dict[str, Any],
    *,
    save_state: Callable[[Dict[str, Any]], None],
    account_provider: Callable[[], tuple[float, float]],
    poll_seconds: float = 5.0,
    max_runtime_seconds: float = 23_400.0,
    now_provider: Optional[Callable[[], float]] = None,
    sleep_provider: Optional[Callable[[float], None]] = None,
) -> Dict[str, Any]:
    """Poll the request queue and execute pending requests until the deadline."""
    now_fn = now_provider or time.monotonic
    sleep_fn = sleep_provider or time.sleep
    deadline = now_fn() + max_runtime_seconds
    total = 0
    LOGGER.info(
        "Execute-requests worker started (paper=%s dry_run=%s poll=%ss).",
        SETTINGS.paper_trading, SETTINGS.dry_run_mode, poll_seconds,
    )
    while now_fn() < deadline:
        oq.write_heartbeat()
        try:
            equity, cash = account_provider()
            tracked = state.get("tracked_positions", [])
            if not isinstance(tracked, list):
                tracked = []
            processed = process_pending_once(broker, order_manager, equity, cash, tracked)
            if processed:
                state["tracked_positions"] = tracked
                save_state(state)
                total += len(processed)
        except Exception:  # keep the worker alive across transient broker/TWS drops
            LOGGER.exception("Execute-requests tick failed; will retry next poll.")
        sleep_fn(poll_seconds)
    LOGGER.info("Execute-requests worker finished (%d processed).", total)
    return {"job": "execute_requests", "processed": total}
