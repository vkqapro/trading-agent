"""Worker that executes dashboard order requests through the bot's OrderManager.

Safety model (authoritative here, not in the UI):

* **Paper only** — any request is rejected unless ``PAPER_TRADING`` is true.
* **Respect DRY_RUN** — a request runs simulated when ``DRY_RUN_MODE`` is true,
  unless it was explicitly marked ``live`` from the dashboard's confirm toggle.
* Every request is isolated in a try/except so one failure can't stall the loop.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, Dict, List, Optional

from src.config import LOGGER, SETTINGS
from src.config import fx_pair_components
from src.execution import order_requests as oq
from src.execution.order_manager import OrderManager
from src.decision.telegram_approval import ApprovalStore, EXECUTED, EXECUTION_FAILED, SUBMITTING, enqueue_existing_worker
from src.risk.position_size import calculate_position_size, position_value_ok
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
    market_only = bool(request.get("market_only") or payload.get("market_only"))
    source = str(request.get("source") or payload.get("source") or "dashboard")
    protection_policy = "alert_managed_no_stop" if market_only else "bracket_managed"
    return {
        "symbol": str(request.get("symbol", "")).upper(),
        "quantity": signal.get("quantity") or payload.get("quantity"),
        "entry": request.get("entry"),
        "stop_loss": request.get("stop"),
        "target": request.get("target"),
        "side": request.get("signal"),
        "strategy": request.get("strategy"),
        "source": source,
        "market_only": market_only,
        "protection_policy": protection_policy,
        "opened_at": oq._now(),
        "market_order_id": payload.get("market_order_id"),
        "stop_order_id": payload.get("stop_order_id"),
        "limit_order_id": payload.get("limit_order_id"),
    }


def _position_symbol_key(position: Dict[str, Any]) -> str:
    symbol = str(position.get("symbol", "")).upper()
    sec_type = str(position.get("sec_type", "")).upper()
    currency = str(position.get("currency", "")).upper()
    if sec_type == "CASH" and len(symbol) == 3 and len(currency) == 3:
        return f"{symbol}.{currency}"
    return symbol


def _symbols_match(requested: str, position: Dict[str, Any]) -> bool:
    requested = str(requested or "").upper()
    if _position_symbol_key(position) == requested:
        return True
    pair = fx_pair_components(requested)
    if pair:
        return _position_symbol_key(position) == f"{pair[0]}.{pair[1]}"
    return str(position.get("symbol", "")).upper() == requested


def _market_order_tif(symbol: str) -> str | None:
    # TradingView webhook orders are market-only one-shot instructions. Force
    # DAY so TWS order presets cannot convert them to GTC and trigger IBKR
    # warning/error 10349.
    return "DAY" if str(symbol or "").strip() else None


def _reviewed_paper_gate(
    candidate: Any,
    *,
    account_id: str | None,
    paper_verified: bool,
    account_allowed: bool,
    account_equity: float,
    cash_available: float,
) -> Dict[str, Any]:
    """Apply execution-safety checks for an already human-approved setup.

    Telegram approval has already passed the canonical setup/plan identity and
    fresh scanner revalidation checks.  It must not be sent through the
    autonomous market-quality policy a second time: that policy is allowed to
    reject on market hours, quote age/quality, spread, news, ATR room, reward
    risk, or autonomous position-count rules.  Keep only the protections that
    are required to construct a safe Paper order here: Paper identity and
    account authorization, positive reviewed sizing, and cash/max-notional
    limits.
    """
    if not paper_verified or not account_allowed:
        return {
            "approved": False,
            "quantity": 0,
            "paper_verified": paper_verified,
            "account_allowed": account_allowed,
            "account_id": account_id,
            "reasons": ["paper_account_unverified" if not paper_verified else "paper_account_not_allowlisted"],
        }

    risk_pct = min(
        float(SETTINGS.risk.risk_per_trade),
        float(SETTINGS.decision_agent.ibkr_paper_risk_per_trade_pct) / 100.0,
    )
    quantity = calculate_position_size(
        float(account_equity), risk_pct, float(candidate.entry), float(candidate.stop)
    )
    if not position_value_ok(
        quantity,
        float(candidate.entry),
        float(SETTINGS.risk.max_position_value),
        float(cash_available),
    ):
        return {
            "approved": False,
            "quantity": quantity,
            "paper_verified": True,
            "account_allowed": True,
            "account_id": account_id,
            "reasons": ["cash_or_max_position_value_limit"],
        }
    return {
        "approved": True,
        "quantity": quantity,
        "risk_amount": abs(float(candidate.entry) - float(candidate.stop)) * quantity,
        "paper_verified": True,
        "account_allowed": True,
        "account_id": account_id,
        "reasons": [],
    }


def _process_place(
    request: Dict[str, Any],
    *,
    broker: Any,
    order_manager: OrderManager,
    account_equity: float,
    cash_available: float,
    tracked_positions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if request.get("immutable_intent"):
        if bool(request.get("live")) or not SETTINGS.paper_trading:
            return {"status": oq.REJECTED, "message": "Refused immutable approval outside Paper mode.", "result": None}
        identity_getter = getattr(broker, "get_account_identity", None)
        if not callable(identity_getter):
            return {"status": oq.REJECTED, "message": "ACCOUNT_NOT_PAPER: broker account identity cannot be verified.", "result": None}
        try:
            identity = identity_getter()
        except Exception as exc:
            return {"status": oq.REJECTED, "message": f"BROKER_DISCONNECTED: account identity check failed: {exc}", "result": None}
        if identity.get("paper_verified") is not True or not identity.get("account_id"):
            return {"status": oq.REJECTED, "message": f"ACCOUNT_NOT_PAPER: {identity.get('evidence') or 'account identity is ambiguous'}", "result": None}
        approval_store = ApprovalStore(SETTINGS.decision_agent.database_path)
        approval = approval_store.get(str(request.get("approval_id")))
        if not approval or approval.get("status") != "EXECUTION_READY" or approval.get("setup_id") != request.get("setup_id") or approval.get("plan_hash") != request.get("plan_hash") or approval.get("symbol") != request.get("symbol") or str(approval.get("direction", "")).lower() != "long":
            return {"status": oq.REJECTED, "message": "Immutable approval intent does not match authoritative state.", "result": None}
        if not approval_store.mark_execution(str(request.get("approval_id")), SUBMITTING):
            return {"status": oq.REJECTED, "message": "Approval is already submitting or terminal; reconciliation required.", "result": None}
        signal = _signal_from_request(request)
        order_manager.dry_run = bool(SETTINGS.dry_run_mode)
        # Telegram approval has already passed its canonical setup/plan-hash
        # revalidation and Paper-account checks above.  Execute it through the
        # same reviewed/manual service as Dashboard Place so a human-approved
        # Paper order is not subjected to autonomous quote and market-hours
        # vetoes a second time.
        reviewed_tif = "GTC"
        reviewed_outside_rth = True
        try:
            ok, payload = order_manager.execute_manual_order(
                signal,
                account_equity=account_equity,
                cash_available=cash_available,
                quantity=int(request.get("quantity") or 0),
                allow_extended_hours_order=reviewed_outside_rth,
                time_in_force=reviewed_tif,
                entry_order_type=str(request.get("entry_order_type") or "MARKET"),
            )
        except Exception as exc:
            # The request has already crossed the broker boundary.  Persist an
            # unknown outcome and leave the request PROCESSING/terminal rather
            # than allowing the next poll to blindly submit a duplicate.
            approval_store.mark_execution(
                str(request.get("approval_id")), EXECUTION_FAILED,
                failure=f"SUBMISSION_UNKNOWN: {exc}",
                broker_details={"account_id": identity.get("account_id"), "submission_outcome": "UNKNOWN"},
            )
            return {"status": oq.ERROR, "message": f"SUBMISSION_UNKNOWN: {exc}; reconciliation required.", "result": None}
        if ok:
            order_id = str(payload.get("market_order_id") or "") or None
            statuses = payload.get("broker_statuses") if isinstance(payload, dict) else None
            approval_store.mark_execution(
                str(request.get("approval_id")), EXECUTED, broker_order_id=order_id,
                broker_details={
                    "account_id": identity.get("account_id"), "perm_id": payload.get("broker_perm_id") or None, "parent_order_id": order_id,
                    "stop_order_id": payload.get("stop_order_id"), "limit_order_id": payload.get("limit_order_id"),
                    "broker_status": (statuses or {}).get("market_order") if isinstance(statuses, dict) else payload.get("status"),
                    "broker_statuses": statuses, "submitted_at": datetime.now(timezone.utc).isoformat(),
                    "tif": reviewed_tif, "outside_rth": reviewed_outside_rth, "submission_outcome": "KNOWN",
                },
            )
            return {"status": oq.SIMULATED if order_manager.dry_run else oq.DONE,
                    "message": "Telegram-approved Paper intent processed.", "result": payload}
        reason = "; ".join(str(x) for x in payload.get("reasons", ())) or "approved intent rejected"
        statuses = payload.get("broker_statuses") if isinstance(payload, dict) else None
        approval_store.mark_execution(
            str(request.get("approval_id")), EXECUTION_FAILED, broker_order_id=str(payload.get("market_order_id") or "") or None,
            failure=reason, broker_details={"account_id": identity.get("account_id"), "stop_order_id": payload.get("stop_order_id"),
            "limit_order_id": payload.get("limit_order_id"), "broker_status": (statuses or {}).get("market_order") if isinstance(statuses, dict) else None,
            "broker_statuses": statuses, "submitted_at": datetime.now(timezone.utc).isoformat(),
            "tif": reviewed_tif, "outside_rth": reviewed_outside_rth,
            "submission_outcome": "KNOWN_REJECTED"},
        )
        return {"status": oq.REJECTED, "message": reason, "result": payload}
    if bool(request.get("manual_setup")):
        if _f(request.get("stop")) <= 0 or _f(request.get("target")) <= 0:
            return {
                "status": oq.REJECTED,
                "message": "Manual Setup requires both a positive Stop and Target.",
                "result": None,
            }

    if bool(request.get("market_only")):
        symbol = str(request.get("symbol", "")).upper()
        side = str(request.get("signal", "")).upper()
        if side not in {"BUY", "SELL"} or not symbol:
            return {"status": oq.ERROR, "message": "Invalid market-only setup (need symbol and BUY/SELL).", "result": None}
        try:
            requested_qty = int(abs(float(request.get("quantity") or 0)))
        except (TypeError, ValueError):
            requested_qty = 0
        if requested_qty <= 0:
            return {"status": oq.ERROR, "message": "Market-only TradingView order requires quantity/position_size.", "result": None}

        if _effective_dry_run(request):
            payload = {
                "symbol": symbol,
                "action": side,
                "quantity": requested_qty,
                "status": "simulated",
                "market_only": True,
                "dry_run": True,
            }
            return {
                "status": oq.SIMULATED,
                "message": f"Simulated market-only {side} {requested_qty} {symbol}; DRY_RUN, no live order.",
                "result": payload,
            }

        result = broker.place_market_order(symbol, side, requested_qty, tif=_market_order_tif(symbol))
        payload = _serialize(result)
        if isinstance(payload, dict):
            payload["market_only"] = True
            broker_status = str(payload.get("status") or "").strip().lower()
            filled_qty = _f(payload.get("filled"))
            if filled_qty <= 0 and broker_status in {"cancelled", "inactive", "apicancelled"}:
                return {
                    "status": oq.REJECTED,
                    "message": f"Broker rejected/cancelled market-only order: {side} {requested_qty} {symbol} ({payload.get('status')}).",
                    "result": payload,
                }
        tracked_positions.append(_tracked_position_from_payload(payload if isinstance(payload, dict) else {}, request))
        return {
            "status": oq.DONE,
            "message": f"Market-only order submitted: {side} {requested_qty} {symbol}.",
            "result": payload,
        }

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
    if payload_status == "unknown_requires_reconciliation":
        return {
            "status": oq.RECONCILIATION_REQUIRED,
            "message": "Broker response is contradictory; no retry will be submitted until reconciliation confirms the outcome.",
            "result": payload,
        }
    reasons = payload.get("reasons") or [payload.get("status", "rejected")]
    return {"status": oq.REJECTED, "message": "; ".join(str(r) for r in reasons), "result": payload}


def _process_close(
    request: Dict[str, Any],
    *,
    broker: Any,
    tracked_positions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    symbol = str(request.get("symbol", "")).upper()
    position = next((p for p in broker.get_positions() if _symbols_match(symbol, p) and _f(p.get("position")) != 0), None)
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

    result = broker.place_market_order(symbol, action, abs_qty, tif=_market_order_tif(symbol))
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
            broker=broker,
            order_manager=order_manager,
            account_equity=account_equity,
            cash_available=cash_available,
            tracked_positions=tracked_positions,
        )
    if action == "close":
        return _process_close(request, broker=broker, tracked_positions=tracked_positions)
    return {"status": oq.ERROR, "message": f"Unknown request action '{action}'.", "result": None}


def process_approved_once(
    broker: Any,
    order_manager: OrderManager,
    account_equity: float,
    cash_available: float,
    tracked_positions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Move approved Telegram decisions through the existing risk/intent queue.

    This runs in the broker-owning worker, not inside the MCP server.  It
    creates reviewed Paper intents and never submits an order itself.
    """
    store = ApprovalStore(SETTINGS.decision_agent.database_path)
    get_identity = getattr(broker, "get_account_identity", None)
    if not callable(get_identity):
        return []
    identity = get_identity()
    paper_verified = identity.get("paper_verified") is True
    account_id = str(identity.get("account_id") or "")
    paper_allowlist = tuple(getattr(SETTINGS.decision_agent, "ibkr_paper_account_allowlist", ()) or ())
    account_allowed = paper_verified and bool(account_id) and account_id in paper_allowlist
    if not account_allowed:
        LOGGER.warning("Telegram approvals held/rejected: IBKR Paper identity unavailable: %s", identity.get("evidence"))

    def gate(candidate: Any) -> Dict[str, Any]:
        return _reviewed_paper_gate(
            candidate,
            account_id=account_id or None,
            paper_verified=paper_verified,
            account_allowed=account_allowed,
            account_equity=account_equity,
            cash_available=cash_available,
        )

    processed: List[Dict[str, Any]] = []
    claim_jobs = getattr(store, "claim_approved_jobs", None)
    complete_job = getattr(store, "complete_approved_job", None)
    if callable(claim_jobs):
        jobs = claim_jobs()
    else:
        # Compatibility seam for older test doubles and pre-job stores.
        jobs = [{"approval_id": row.get("approval_id")} for row in store.list_approvals(status="APPROVED")]
    for job in jobs:
        approval_id = str(job.get("approval_id"))
        try:
            try:
                # A callback MCP timeout can leave durable APPROVED plus a
                # REVALIDATION_PASSED event after the caller has disconnected.
                # Resume that lifecycle without repeating the slow scanner
                # lookup; ordinary approvals still use the normal path.
                reconcile = getattr(store, "reconcile_approved_execution", None)
                if not callable(reconcile):
                    raise ValueError("revalidation_not_recorded")
                result = reconcile(approval_id, risk_gate=gate, enqueue=enqueue_existing_worker)
            except ValueError as exc:
                if str(exc) != "revalidation_not_recorded":
                    raise
                result = store.process_approved(approval_id, risk_gate=gate, enqueue=enqueue_existing_worker)
            if callable(complete_job) and job.get("job_id"):
                complete_job(str(job["job_id"]), failure_message=None if result.status != EXECUTION_FAILED else result.message)
            processed.append({"approval_id": approval_id, "status": result.status, "message": result.message})
        except Exception as exc:
            LOGGER.exception("Approved Telegram setup processing failed approval_id=%s", approval_id)
            if callable(complete_job) and job.get("job_id"):
                complete_job(str(job["job_id"]), failure_message=str(exc))
            processed.append({"approval_id": approval_id, "status": "EXECUTION_FAILED", "message": str(exc)})
    return processed


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
            approved = process_approved_once(broker, order_manager, equity, cash, tracked)
            processed = process_pending_once(broker, order_manager, equity, cash, tracked)
            if approved or processed:
                state["tracked_positions"] = tracked
                save_state(state)
                total += len(processed)
        except Exception:  # keep the worker alive across transient broker/TWS drops
            LOGGER.exception("Execute-requests tick failed; will retry next poll.")
        sleep_fn(poll_seconds)
    LOGGER.info("Execute-requests worker finished (%d processed).", total)
    return {"job": "execute_requests", "processed": total}
