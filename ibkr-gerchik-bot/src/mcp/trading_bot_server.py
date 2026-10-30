"""Standalone read-only Trading Bot Data MCP over Streamable HTTP."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import CallToolResult, TextContent
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.config import SETTINGS
from src.decision.strategy_sources import StrategySnapshot, refresh_current_source_snapshot, snapshot_to_candidate
from src.decision.telegram_approval import ApprovalStore, PENDING_APPROVAL, PREPARED, plan_hash, register_canonical_setup

from .trading_data_service import (
    DataError,
    get_level_context as _get_level_context,
    get_symbol_data_coverage as _get_symbol_data_coverage,
    get_symbol_history as _get_symbol_history,
    get_symbol_levels as _get_symbol_levels,
    get_symbol_snapshot as _get_symbol_snapshot,
    render_symbol_chart as _render_symbol_chart,
)

LOGGER = logging.getLogger("trading_bot_data")
ENABLED = os.getenv("TRADING_BOT_MCP_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
HOST = os.getenv("TRADING_BOT_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("TRADING_BOT_MCP_PORT", "8765"))

mcp = FastMCP("trading_bot_data", stateless_http=True, json_response=True)


def _approval_store() -> ApprovalStore:
    return ApprovalStore(
        SETTINGS.decision_agent.database_path,
        ttl_seconds=SETTINGS.telegram_approval_ttl_seconds,
        allowed_user_ids=tuple(SETTINGS.telegram_allowed_user_ids),
        allowed_chat_ids=tuple(SETTINGS.telegram_allowed_chat_ids),
    )


def _safe(call: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    try:
        return call(**kwargs)
    except DataError as exc:
        return {"error": {"code": exc.code, "message": exc.message}}
    except Exception:
        LOGGER.exception("MCP data request failed")
        return {"error": {"code": "DATA_SOURCE_UNAVAILABLE", "message": "Persisted Trading Bot data is unavailable"}}


@mcp.tool()
def get_symbol_snapshot(symbol: str) -> dict[str, Any]:
    """Return a compact snapshot from persisted Trading Bot data.

    `current_price` is the latest persisted current-price bar;
    `latest_closed_price` is the most recent completed daily candle close.
    ATR, coverage, level counts, and freshness are included. This tool never
    fetches market data or recalculates levels.
    """
    return _safe(_get_symbol_snapshot, symbol=symbol)


@mcp.tool()
def get_symbol_data_coverage(symbol: str, timeframe: str | None = None) -> dict[str, Any]:
    """Return stored candle coverage for a symbol and optional timeframe."""
    return _safe(_get_symbol_data_coverage, symbol=symbol, timeframe=timeframe)


@mcp.tool()
def get_symbol_history(
    symbol: str,
    timeframe: str = "1D",
    lookback_days: int | None = None,
    limit: int | None = None,
    start: str | None = None,
    end: str | None = None,
    include_incomplete: bool = False,
) -> dict[str, Any]:
    """Return structured stored OHLCV candle data.

    Use this tool for raw candle/history values, numeric statistics, date
    ranges, tabular output, or calculations from candles. If the user asks to
    show, plot, chart, visualize, or display candles, prefer
    ``render_symbol_chart`` instead. Incomplete candles are excluded by
    default. `latest_closed_price` is always derived from the returned closed
    candle dataset and is never replaced with current snapshot price.
    """
    return _safe(_get_symbol_history, symbol=symbol, timeframe=timeframe, lookback_days=lookback_days, limit=limit, start=start, end=end, include_incomplete=include_incomplete)


@mcp.tool()
def get_symbol_levels(
    symbol: str,
    level_set: str = "consolidated",
    min_strength: float | None = None,
    level_type: str | None = None,
    side: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return existing raw or consolidated levels as structured data.

    Use this tool when the user asks to inspect, list, compare, filter, or
    analyze Trading Bot levels numerically. If the user asks to show levels on
    a chart or overlay levels on price action, prefer
    ``render_symbol_chart(show_levels=true)``. No new levels are calculated;
    distances and above/below filters use `current_price`, while the separate
    `level_reference_price` describes persisted level-generation context.
    """
    return _safe(_get_symbol_levels, symbol=symbol, level_set=level_set, min_strength=min_strength, level_type=level_type, side=side, limit=limit)


@mcp.tool()
def render_symbol_chart(
    symbol: str,
    timeframe: str = "1D",
    lookback_days: int = 60,
    start: str | None = None,
    end: str | None = None,
    show_levels: bool = False,
    level_set: str = "consolidated",
    min_strength: float | None = None,
    level_type: str | None = None,
    side: str | None = None,
    level_labels: str = "compact",
    show_current_price: bool = True,
    include_volume: bool = True,
    width: int | None = None,
    height: int | None = None,
) -> CallToolResult:
    """Render a visual candlestick chart from stored Trading Bot data.

    Prefer this tool when the user asks to show, plot, chart, visualize,
    display, draw, or view price action/candles for a symbol. Use
    ``get_symbol_history`` for raw OHLCV data, numeric calculations, tables, or
    historical values without a visualization. Use ``show_levels=true`` when
    the user asks to show or overlay Trading Bot raw or consolidated levels.
    Uses closed candles by default. It does not fetch market data, calculate
    new levels, generate signals, or place orders. The response includes JSON
    provenance metadata followed by a PNG image.
    """
    try:
        result = _render_symbol_chart(
            symbol=symbol, timeframe=timeframe, lookback_days=lookback_days,
            start=start, end=end, show_levels=show_levels, level_set=level_set,
            min_strength=min_strength, level_type=level_type, side=side,
            level_labels=level_labels, show_current_price=show_current_price,
            include_volume=include_volume, width=width, height=height,
        )
        image_content = Image(data=result["image_bytes"], format="png").to_image_content()
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result["metadata"], indent=2)), image_content])
    except DataError as exc:
        return CallToolResult(isError=True, content=[TextContent(type="text", text=json.dumps({"error": {"code": exc.code, "message": exc.message}}))])
    except Exception:
        LOGGER.exception("MCP chart rendering failed")
        return CallToolResult(isError=True, content=[TextContent(type="text", text=json.dumps({"error": {"code": "DATA_SOURCE_UNAVAILABLE", "message": "Chart rendering is unavailable"}}))])


_SCANNER_RUN_RE = re.compile(r"^scan_[A-Za-z0-9_-]{8,80}$")
_SCANNER_RUNS_ROOT = Path(__file__).resolve().parents[2] / "backtest_engine_v24.4" / "scanner" / "runs"


def _load_scanner_candidate_key(
    scanner_run_id: str,
    symbol: str,
    strategy: str,
    direction: str,
) -> dict[str, Any]:
    run_id = str(scanner_run_id or "").strip()
    if not _SCANNER_RUN_RE.fullmatch(run_id):
        raise ValueError("invalid_scanner_run_id")
    requested_symbol = str(symbol or "").strip().upper()
    requested_strategy = str(strategy or "").strip().upper()
    requested_direction = str(direction or "").strip().upper()
    if not requested_symbol or not requested_strategy or requested_direction != "LONG":
        raise ValueError("invalid_candidate_key")
    artifact = _SCANNER_RUNS_ROOT / run_id / "candidates.json"
    try:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("scanner_run_not_found")
    matches: list[dict[str, Any]] = []
    for item in payload.get("candidates", ()) if isinstance(payload, Mapping) else ():
        if not isinstance(item, Mapping):
            continue
        if (
            str(item.get("symbol", "")).upper() == requested_symbol
            and str(item.get("strategy_id", "")).upper() == requested_strategy
            and str(item.get("direction", "")).upper() == requested_direction
            and str(item.get("status", "")).upper() == "ENTRY_SIGNAL"
            and item.get("matched") is True
        ):
            matches.append(dict(item))
    LOGGER.info("scanner resolver filters run_id=%s symbol=%s strategy=%s direction=%s candidate_count=%s", run_id, requested_symbol, requested_strategy, requested_direction, len(matches))
    if len(matches) != 1:
        raise ValueError("ambiguous_or_unknown_scanner_candidate")
    signal = matches[0].get("signal") or {}
    signal_bar = signal.get("signal_bar_date") or signal.get("signal_bar")
    if not signal_bar:
        raise ValueError("scanner_signal_bar_missing")
    LOGGER.info("scanner resolver selected artifact run_id=%s symbol=%s strategy=%s direction=%s signal_bar=%s", run_id, requested_symbol, requested_strategy, requested_direction, str(signal_bar)[:10])
    return {
        "run_id": run_id, "symbol": requested_symbol, "strategy": requested_strategy,
        "direction": requested_direction, "signal_bar": str(signal_bar)[:10],
        "artifact": matches[0],
    }


def _artifact_candidate(key: Mapping[str, Any]):
    """Build one immutable canonical candidate from the selected stored run item.

    The scanner artifact is the canonical evaluator output for that run. This
    fallback is deliberately keyed by the complete identity, never symbol-only,
    and is used only when the current source snapshot has no historical row.
    """
    from src.decision.models import DecisionCandidate

    item = key.get("artifact")
    if not isinstance(item, Mapping):
        return None
    signal = item.get("signal") if isinstance(item.get("signal"), Mapping) else {}
    level = item.get("level") if isinstance(item.get("level"), Mapping) else {}
    market = item.get("market") if isinstance(item.get("market"), Mapping) else {}
    plan = item.get("trade_plan") if isinstance(item.get("trade_plan"), Mapping) else {}
    quality = item.get("quality") if isinstance(item.get("quality"), Mapping) else {}
    identity = f"{key['run_id']}|{key['symbol']}|{key['strategy']}|{key['direction']}|{key['signal_bar']}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    created = str(item.get("as_of") or key["signal_bar"])
    try:
        created_at = datetime.fromisoformat(created.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
    except ValueError:
        created_at = datetime.now(timezone.utc)
    try:
        entry, stop, target = (float(plan[name]) for name in ("entry", "stop", "target"))
    except (KeyError, TypeError, ValueError):
        return None
    return DecisionCandidate(
        candidate_id=f"scanner-{digest}", created_at=created_at, asset_class="stock",
        symbol=key["symbol"], strategy=key["strategy"], direction="long",
        entry=entry, stop=stop, target=target,
        level_price=float(level["price"]) if level.get("price") is not None else None,
        level_type=str(level.get("level_type") or ""), atr=float(market["atr"]) if market.get("atr") is not None else None,
        reward_risk=float(plan.get("reward_risk", plan.get("rr"))) if plan.get("reward_risk", plan.get("rr")) is not None else None,
        risk_per_share=abs(entry - stop), confidence=float(quality["score"]) if quality.get("score") is not None else None,
        source_signal_id=f"scanner:{identity}",
        market_context={"scan_id": key["run_id"], "strategy_source": "scanner_artifact", "signal_bar": key["signal_bar"]},
        metadata={"candidate_class": "EXECUTABLE_ENTRY_SIGNAL", "scanner_run_id": key["run_id"], "strategy_name": key["strategy"], "strategy_version": item.get("strategy_version"), "pattern_family": (item.get("pattern_definition") or {}).get("pattern_family") if isinstance(item.get("pattern_definition"), Mapping) else None, "signal_bar": key["signal_bar"], "artifact_identity": identity},
    )


def _resolve_canonical_setup_for_key(key: Mapping[str, Any]) -> tuple[str, str]:
    try:
        payload = refresh_current_source_snapshot(force=True, minimum_interval_seconds=0.0)
    except Exception:
        raise ValueError("canonical_source_unavailable")
    sources = payload.get("sources") if isinstance(payload, Mapping) else None
    if not isinstance(sources, Mapping):
        raise ValueError("canonical_source_unavailable")
    counts = {"executable": 0, "symbol": 0, "strategy": 0, "direction": 0, "signal_bar": 0, "artifact_plan": 0}
    matches: list[tuple[str, str]] = []
    artifact = key.get("artifact") if isinstance(key.get("artifact"), Mapping) else {}
    artifact_plan = artifact.get("trade_plan") if isinstance(artifact.get("trade_plan"), Mapping) else {}
    artifact_level = artifact.get("level") if isinstance(artifact.get("level"), Mapping) else {}
    for raw_items in sources.values():
        if not isinstance(raw_items, (list, tuple)):
            continue
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                continue
            try:
                snapshot = StrategySnapshot(**dict(raw))
                candidate, _ = snapshot_to_candidate(snapshot)
            except Exception:
                continue
            if snapshot.candidate_class != "EXECUTABLE_ENTRY_SIGNAL":
                continue
            counts["executable"] += 1
            if candidate.symbol.upper() != key["symbol"]:
                continue
            counts["symbol"] += 1
            if candidate.strategy.upper() != key["strategy"]:
                continue
            counts["strategy"] += 1
            if candidate.direction.upper() != key["direction"]:
                continue
            counts["direction"] += 1
            timestamp = snapshot.source_timestamp or snapshot.bar_timestamp or ""
            if str(timestamp)[:10] != key["signal_bar"]:
                continue
            counts["signal_bar"] += 1
            if artifact_plan:
                expected = (artifact_plan.get("entry"), artifact_plan.get("stop"), artifact_plan.get("target"), artifact_level.get("price"))
                actual = (candidate.entry, candidate.stop, candidate.target, candidate.level_price)
                if any(value is None for value in expected) or any(abs(float(a) - float(b)) > 1e-6 for a, b in zip(actual, expected)):
                    continue
                counts["artifact_plan"] += 1
            matches.append((candidate.candidate_id, plan_hash(candidate)))
    LOGGER.info("canonical resolver filters run_id=%s symbol=%s strategy=%s direction=%s signal_bar=%s counts=%s", key.get("run_id"), key.get("symbol"), key.get("strategy"), key.get("direction"), key.get("signal_bar"), counts)
    if len(matches) == 1:
        return matches[0]
    if len(matches) == 0:
        artifact_candidate = _artifact_candidate(key)
        if artifact_candidate is not None:
            LOGGER.info("canonical resolver using immutable scanner artifact fallback identity=%s", artifact_candidate.metadata.get("artifact_identity"))
            return artifact_candidate.candidate_id, plan_hash(artifact_candidate)
    raise ValueError("ambiguous_or_unknown_canonical_setup")
    return matches[0]


def _approval_card_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project authoritative candidate fields for the transport-only Telegram plugin."""
    try:
        candidate = json.loads(str(row.get("candidate_json") or "{}"))
    except (TypeError, ValueError):
        candidate = {}
    metadata = candidate.get("metadata") if isinstance(candidate, Mapping) else {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    try:
        intent = json.loads(str(row.get("execution_intent_json") or "{}"))
    except (TypeError, ValueError):
        intent = {}
    quantity = row.get("reviewed_quantity")
    if quantity is None:
        quantity = candidate.get("quantity", metadata.get("quantity"))
    if quantity is None and isinstance(intent, Mapping):
        quantity = intent.get("quantity")
    try:
        quantity = int(quantity) if quantity is not None else None
    except (TypeError, ValueError):
        quantity = None
    risk_amount = row.get("risk_amount")
    reward_amount = row.get("reward_amount")
    if risk_amount is None and isinstance(intent, Mapping):
        risk_amount = intent.get("risk_amount")
    if reward_amount is None and isinstance(intent, Mapping):
        reward_amount = intent.get("reward_amount")
    entry = candidate.get("entry")
    stop = candidate.get("stop")
    target = candidate.get("target")
    if quantity is not None and risk_amount is None and entry is not None and stop is not None:
        risk_amount = abs(float(entry) - float(stop)) * quantity
    if quantity is not None and reward_amount is None and entry is not None and target is not None:
        reward_amount = abs(float(target) - float(entry)) * quantity
    if risk_amount is not None:
        risk_amount = round(float(risk_amount), 2)
    if reward_amount is not None:
        reward_amount = round(float(reward_amount), 2)
    return {
        "approval_id": str(row.get("approval_id") or ""),
        "status": str(row.get("status") or ""),
        "expires_at": None if row.get("status") == PREPARED else row.get("expires_at"),
        "symbol": candidate.get("symbol"),
        "strategy": candidate.get("strategy"),
        "direction": candidate.get("direction"),
        "level": candidate.get("level_price"),
        "entry": entry,
        "stop": stop,
        "target": target,
        "quantity": quantity,
        "risk_amount": risk_amount,
        "reward_amount": reward_amount,
        "rr": candidate.get("reward_risk"),
        "score": metadata.get("score", candidate.get("confidence")),
    }


@mcp.tool()
def prepare_setup_approval(
    scanner_run_id: str,
    symbol: str,
    strategy: str,
    direction: str = "LONG",
) -> dict[str, Any]:
    """Resolve, register, and prepare approval for one scanner artifact candidate.

    Only the scanner run and immutable candidate key are accepted. All trade
    fields, setup identity, and plan hash come from backend canonical sources.
    """
    try:
        key = _load_scanner_candidate_key(scanner_run_id, symbol, strategy, direction)
        setup_id, canonical_hash = _resolve_canonical_setup_for_key(key)
        artifact_candidate = _artifact_candidate(key)
        if artifact_candidate is not None and artifact_candidate.candidate_id == setup_id:
            registration = register_canonical_setup(
                setup_id, scanner_run_id=key["run_id"], database_path=SETTINGS.decision_agent.database_path,
                canonical_candidate=artifact_candidate,
            )
        else:
            registration = register_canonical_setup(setup_id, scanner_run_id=key["run_id"], database_path=SETTINGS.decision_agent.database_path)
        if registration is None or registration.get("plan_hash") != canonical_hash:
            raise ValueError("canonical_registration_failed")
        store = _approval_store()
        result = store.create(setup_id, scanner_run_id=key["run_id"])
        row = store.get(result.approval_id) or {}
        card_payload = _approval_card_payload(row)
        return {
            "approval_id": result.approval_id,
            "setup_id": setup_id,
            "plan_hash": canonical_hash,
            "expires_at": card_payload["expires_at"],
            "status": result.status,
            **{key: card_payload[key] for key in ("symbol", "strategy", "direction", "level", "entry", "stop", "target", "quantity", "risk_amount", "reward_amount", "rr", "score")},
            "card_payload": card_payload,
        }
    except ValueError as exc:
        return {"error": {"code": str(exc), "message": "scanner candidate is unavailable for approval"}}
    except Exception:
        LOGGER.exception("Setup approval preparation failed")
        return {"error": {"code": "APPROVAL_UNAVAILABLE", "message": "approval preparation is unavailable"}}


@mcp.tool()
def create_setup_approval(
    setup_id: str,
    scanner_run_id: str | None = None,
    telegram_chat_id: str | None = None,
    telegram_message_id: str | None = None,
) -> dict[str, Any]:
    """Create a Telegram approval for one persisted scanner setup.

    Only the immutable setup reference is accepted. Symbol, prices, side,
    quantity, account, and strategy are loaded from the authoritative audit DB.
    """
    try:
        registration = register_canonical_setup(
            setup_id,
            scanner_run_id=scanner_run_id,
            database_path=SETTINGS.decision_agent.database_path,
        )
        LOGGER.info(
            "canonical setup registration setup_id=%s registered=%s reused=%s",
            setup_id,
            bool(registration and registration.get("registered")),
            bool(registration and registration.get("reused")),
        )
        result = _approval_store().create(
            setup_id,
            scanner_run_id=scanner_run_id,
            chat_id=telegram_chat_id,
            message_id=telegram_message_id,
        )
        return {"approval_id": result.approval_id, "status": result.status, "message": result.message}
    except ValueError as exc:
        return {"error": {"code": str(exc), "message": "setup is unavailable for approval"}}
    except Exception:
        LOGGER.exception("Approval creation failed")
        return {"error": {"code": "APPROVAL_UNAVAILABLE", "message": "approval state is unavailable"}}


@mcp.tool()
def confirm_approval_card_delivery(approval_id: str, telegram_chat_id: str, telegram_message_id: str | None = None) -> dict[str, Any]:
    """Atomically start one approval review window after native Telegram delivery.

    This is idempotent by approval_id and never recalculates trade data or
    resets an already-started expiry.
    """
    try:
        result = _approval_store().confirm_delivery(
            approval_id, chat_id=telegram_chat_id, message_id=telegram_message_id,
        )
        row = _approval_store().get(approval_id) or {}
        return {
            "approval_id": result.approval_id,
            "status": result.status,
            "message": result.message,
            "expires_at": row.get("expires_at"),
            "delivered_at": row.get("delivered_at"),
            "card_payload": _approval_card_payload(row),
        }
    except ValueError as exc:
        return {"error": {"code": str(exc), "message": "approval delivery confirmation unavailable"}}
    except Exception:
        LOGGER.exception("Approval delivery confirmation failed")
        return {"error": {"code": "APPROVAL_DELIVERY_CONFIRMATION_FAILED", "message": "approval review window was not started"}}


@mcp.tool()
def record_approval_delivery_ack_unknown(approval_id: str, telegram_chat_id: str, reason: str) -> dict[str, Any]:
    """Record an ambiguous Telegram send acknowledgement without starting review TTL."""
    try:
        result = _approval_store().record_delivery_ack_unknown(approval_id, chat_id=telegram_chat_id, reason=reason)
        row = _approval_store().get(approval_id) or {}
        return {
            "approval_id": result.approval_id,
            "status": result.status,
            "message": result.message,
            "delivered_at": row.get("delivered_at"),
            "expires_at": None if row.get("status") == PREPARED else row.get("expires_at"),
            "reconciliation_required": row.get("failure_code") == "delivery_ack_unknown",
        }
    except ValueError as exc:
        return {"error": {"code": str(exc), "message": "approval acknowledgement record unavailable"}}
    except Exception:
        LOGGER.exception("Approval acknowledgement recording failed")
        return {"error": {"code": "APPROVAL_ACK_RECORD_FAILED", "message": "approval acknowledgement record unavailable"}}


@mcp.tool()
def approve_setup(approval_id: str, telegram_user_id: str, telegram_chat_id: str, telegram_callback_id: str | None = None) -> dict[str, Any]:
    """Authorize and durably enqueue approval; slow work belongs to the worker."""
    try:
        store = _approval_store()
        result = store.decide(approval_id, approve=True, user_id=telegram_user_id, chat_id=telegram_chat_id, callback_id=telegram_callback_id)
        current = store.get(approval_id) or {}
        job = store.approval_job(approval_id)
        accepted = result.status == "APPROVED"
        return {"approval_id": result.approval_id, "status": current.get("status", result.status),
                "message": "Approval accepted and queued for asynchronous processing" if accepted else result.message,
                "reasons": list(result.reasons), "approval_job_id": job.get("job_id") if job else None,
                "approval_job_status": job.get("status") if job else None}
    except PermissionError:
        return {"error": {"code": "TELEGRAM_UNAUTHORIZED", "message": "telegram identity is not authorized"}}
    except ValueError as exc:
        return {"error": {"code": str(exc), "message": "approval is unavailable"}}


@mcp.tool()
def reject_setup(approval_id: str, telegram_user_id: str, telegram_chat_id: str, telegram_callback_id: str | None = None) -> dict[str, Any]:
    """Record an authorized rejection and permanently prevent execution."""
    try:
        result = _approval_store().decide(approval_id, approve=False, user_id=telegram_user_id, chat_id=telegram_chat_id, callback_id=telegram_callback_id)
        return {"approval_id": result.approval_id, "status": result.status, "message": result.message}
    except PermissionError:
        return {"error": {"code": "TELEGRAM_UNAUTHORIZED", "message": "telegram identity is not authorized"}}
    except ValueError as exc:
        return {"error": {"code": str(exc), "message": "approval is unavailable"}}


@mcp.tool()
def get_approval_status(approval_id: str) -> dict[str, Any]:
    """Return persisted approval status and correlation identifiers."""
    row = _approval_store().get(approval_id)
    if row is None:
        return {"error": {"code": "APPROVAL_NOT_FOUND", "message": "approval not found"}}
    card_payload = _approval_card_payload(row)
    row.pop("candidate_json", None)
    row["card_payload"] = card_payload
    return row


@mcp.tool()
def get_approval_card_payload(approval_id: str) -> dict[str, Any]:
    """Return the durable, transport-ready card projection for one approval.

    The caller supplies only an approval ID. Trade fields are projected from
    the persisted candidate JSON; they are never reconstructed by the caller.
    A delivered approval is reported as already delivered so callers do not
    send a duplicate card unless an explicit resend operation is added later.
    """
    row = _approval_store().get(approval_id)
    if row is None:
        return {"error": {"code": "APPROVAL_NOT_FOUND", "message": "approval not found"}}
    card_payload = _approval_card_payload(row)
    already_delivered = (
        row.get("status") == PENDING_APPROVAL
        and row.get("telegram_chat_id") is not None
        and row.get("telegram_message_id") is not None
    )
    return {
        "approval_id": str(row["approval_id"]),
        "status": str(row["status"]),
        "card_payload": card_payload,
        "delivery_allowed": row.get("status") == PREPARED and not already_delivered,
        "already_delivered": already_delivered,
        "telegram_chat_id": row.get("telegram_chat_id"),
        "telegram_message_id": row.get("telegram_message_id"),
    }


@mcp.tool()
def list_telegram_card_reconciliation() -> dict[str, Any]:
    """List durable approval cards that the Telegram transport may refresh.

    The backend returns correlation identifiers and authoritative status only;
    the connected Telegram plugin performs the in-place edit.
    """
    rows = _approval_store().list_telegram_card_reconciliation()
    updates = []
    for row in rows:
        payload = _approval_card_payload(row)
        row.pop("candidate_json", None)
        updates.append({
            "approval_id": row["approval_id"],
            "status": row["status"],
            "telegram_chat_id": row.get("telegram_chat_id"),
            "telegram_message_id": row.get("telegram_message_id"),
            "failure_code": row.get("failure_code"),
            "failure_message": row.get("failure_message"),
            "execution_intent_id": row.get("execution_intent_id"),
            "broker_order_id": row.get("broker_order_id"),
            "broker_account_id": row.get("broker_account_id"),
            "broker_status": row.get("broker_status"),
            "broker_submitted_at": row.get("broker_submitted_at"),
            "broker_outside_rth": row.get("broker_outside_rth"),
            "card_payload": payload,
        })
    return {"updates": updates}


@mcp.tool()
def get_level_context(symbol: str, level_price: float) -> dict[str, Any]:
    """Return persisted level metadata within 0.005 price units.

    Separates current-price distance from level-reference-price distance.
    """
    return _safe(_get_level_context, symbol=symbol, level_price=level_price)


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "Trading Bot Data MCP", "mode": "data_read_only_approval_state_machine"})


def _app():
    app = mcp.streamable_http_app()
    app.routes.append(Route("/health", health, methods=["GET"]))
    return app


def main() -> None:
    import uvicorn

    if not ENABLED:
        raise SystemExit("Trading Bot Data MCP is disabled (TRADING_BOT_MCP_ENABLED=false)")
    logging.basicConfig(level=os.getenv("TRADING_BOT_MCP_LOG_LEVEL", "INFO"))
    LOGGER.info("Trading Bot Data MCP server started transport=streamable-http host=%s port=%s tools=10", HOST, PORT)
    uvicorn.run(_app(), host=HOST, port=PORT, log_level=os.getenv("TRADING_BOT_MCP_LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    main()
