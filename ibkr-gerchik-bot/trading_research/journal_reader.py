"""Read-only adapter for the existing Orders Journal persistence formats.

The production ``order_journal`` module is intentionally not imported because
its dependency graph initializes a repository file logger at import time.  This
adapter reads the same persisted ledgers and implements only the non-mutating
row projection needed for research.  It never writes, reconciles, or contacts
a broker.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import ProductionDataPaths
from .models import ResearchTrade, decimal_or_none, status_group
from .safety import assert_read_only_source
from .timezones import EASTERN


_UNKNOWN_STRATEGY_VALUES = {"", "broker_observed", "tradingview_webhook", "unknown", "none", "null"}
_EMBEDDED_STRATEGY_ID = re.compile(r"(?:^|_)(PB1|PB2|LB1|LB2|LP1|LP2|PRB1|PRB2)(?:_|$)")


def parse_timestamp(value: Any) -> datetime | None:
    """Normalize legacy compact and ISO journal timestamps to America/New_York."""
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().replace(",", " ").replace("Z", "+00:00")
    parsed: datetime | None = None
    for pattern in ("%Y%m%d %H:%M:%S", "%Y%m%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, pattern)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=EASTERN)
    return parsed.astimezone(EASTERN)


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _first_decimal(*values: Any) -> Decimal | None:
    for value in values:
        number = decimal_or_none(value)
        if number is not None:
            return number
    return None


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _result_fill_price(result: dict[str, Any]) -> Decimal | None:
    return _first_decimal(
        result.get("avgFillPrice"), result.get("avg_fill_price"), result.get("fill_price"), result.get("entry"), result.get("price")
    )


def _result_has_fill(result: dict[str, Any]) -> bool:
    filled = decimal_or_none(result.get("filled"))
    if filled is not None and filled > 0:
        return True
    if _upper(result.get("status")) in {"FILLED", "EXECUTED"} and _result_fill_price(result) is not None:
        return True
    statuses = result.get("broker_statuses")
    return isinstance(statuses, dict) and any(_upper(value) in {"FILLED", "EXECUTED"} for value in statuses.values())


class OrderJournalReader:
    """Expose normalized Trades from supplied rows or the current JSON ledgers."""

    def __init__(self, paths: ProductionDataPaths | None = None, rows: Iterable[dict[str, Any]] | None = None):
        self.paths = paths or ProductionDataPaths()
        self._rows = list(rows) if rows is not None else None

    def read(self) -> list[ResearchTrade]:
        rows = self._rows if self._rows is not None else self._read_persisted_rows()
        return [self._normalize(row) for row in rows if isinstance(row, dict)]

    def _read_json(self, path: Path, default: Any) -> Any:
        assert_read_only_source(path, repository_root=self.paths.repository_root)
        if not path.exists() or not path.is_file():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _read_persisted_rows(self) -> list[dict[str, Any]]:
        requests_payload = self._read_json(self.paths.order_requests, {"requests": []})
        requests = requests_payload.get("requests", []) if isinstance(requests_payload, dict) else []
        closed_payload = self._read_json(self.paths.closed_positions, {"positions": []})
        closed_positions = closed_payload.get("positions", []) if isinstance(closed_payload, dict) else []
        tracked_payload = self._read_json(self.paths.tracked_positions, {})
        tracked = tracked_payload.get("tracked_positions", []) if isinstance(tracked_payload, dict) else []
        request_rows = self._rows_from_requests(
            requests if isinstance(requests, list) else [],
            closed_positions if isinstance(closed_positions, list) else [],
            tracked if isinstance(tracked, list) else [],
        )
        request_ids = {str(row.get("request_id") or "") for row in request_rows}
        request_rows.extend(self._unlinked_broker_rows(closed_positions if isinstance(closed_positions, list) else [], request_ids))
        request_rows.extend(self._state_rows(self.paths.tradingview_stock, market="STOCK", source="TradingView Stock"))
        request_rows.extend(self._state_rows(self.paths.tradingview_forex, market="FOREX", source="TradingView Forex"))
        request_rows.extend(self._state_rows(self.paths.crypto_state, market="CRYPTO", source="TradingView OKX"))
        return request_rows

    def _rows_from_requests(
        self,
        requests: list[dict[str, Any]],
        closed_positions: list[dict[str, Any]],
        tracked_positions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        closes_by_symbol: dict[str, list[dict[str, Any]]] = {}
        for request in requests:
            if isinstance(request, dict) and _upper(request.get("action")) == "CLOSE":
                closes_by_symbol.setdefault(_upper(request.get("symbol")), []).append(request)
        active_symbols = {_upper(position.get("symbol")) for position in tracked_positions if isinstance(position, dict)}
        result: list[dict[str, Any]] = []
        for request in requests:
            if not isinstance(request, dict) or _upper(request.get("action")) != "PLACE":
                continue
            symbol = _upper(request.get("symbol"))
            broker_result = request.get("result") if isinstance(request.get("result"), dict) else {}
            fill_known = _result_has_fill(broker_result)
            close_request = next(
                (
                    item for item in closes_by_symbol.get(symbol, [])
                    if str(item.get("created_at") or "") >= str(request.get("created_at") or "")
                    and _upper(item.get("status")) in {"DONE", "SIMULATED", "FILLED", "EXECUTED"}
                ),
                None,
            )
            observed = next(
                (
                    item for item in closed_positions
                    if isinstance(item, dict)
                    and str(item.get("request_id") or "") == str(request.get("id") or "")
                    and item.get("exit_execution_id")
                ),
                None,
            )
            raw_status = _upper(request.get("status"))
            if symbol in active_symbols:
                status = "OPEN"
            elif observed:
                status = "CLOSED_OBSERVED"
            elif close_request:
                status = "CLOSED"
            elif raw_status in {"DONE", "SIMULATED"} and "MARKET_SCREENER" in _upper(request.get("source")) and not fill_known:
                status = "UNFILLED"
            elif raw_status in {"DONE", "SIMULATED"}:
                status = "CLOSED_UNKNOWN"
            else:
                status = raw_status or "UNKNOWN"
            close_result = close_request.get("result") if isinstance(close_request, dict) and isinstance(close_request.get("result"), dict) else {}
            observed = observed or {}
            actual_entry = _result_fill_price(broker_result) if fill_known else _first_decimal(observed.get("avg_cost"), observed.get("entry"))
            actual_exit = _first_decimal(
                observed.get("exit_price"), observed.get("actual_exit"), _result_fill_price(close_result)
            )
            result.append(
                {
                    "id": f"stock:req:{request.get('id')}",
                    "request_id": request.get("id"),
                    "symbol": symbol,
                    "market": "STOCK",
                    "source": request.get("source"),
                    "strategy": request.get("strategy") or broker_result.get("strategy"),
                    "strategy_version": request.get("strategy_version") or broker_result.get("strategy_version"),
                    "side": request.get("signal") or request.get("direction"),
                    "status": status,
                    "created_at": request.get("created_at"),
                    "opened_at": request.get("opened_at") or request.get("updated_at") or request.get("created_at"),
                    "closed_at": observed.get("closed_at") or (close_request or {}).get("updated_at") or (close_request or {}).get("created_at"),
                    "planned_entry": request.get("entry"),
                    "planned_stop": request.get("stop"),
                    "planned_target": request.get("target"),
                    "current_stop": observed.get("current_stop_loss") or observed.get("stop_loss"),
                    "current_target": observed.get("current_target") or observed.get("target"),
                    "actual_entry": actual_entry,
                    "actual_exit": actual_exit,
                    "quantity": observed.get("exit_quantity") or observed.get("quantity") or broker_result.get("quantity") or request.get("quantity"),
                    "pnl": observed.get("broker_realized_pnl"),
                    "exit_reason": observed.get("exit_reason") or ("CLOSE_REQUEST" if close_request else ""),
                    "execution_id": observed.get("exit_execution_id"),
                    "trading_mode": "LIVE" if request.get("live") else "PAPER" if request.get("live") is not None else None,
                }
            )
        return result

    def _unlinked_broker_rows(self, positions: list[dict[str, Any]], request_ids: set[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for position in positions:
            if not isinstance(position, dict) or not position.get("exit_execution_id"):
                continue
            request_id = str(position.get("request_id") or "")
            if request_id and request_id in request_ids:
                continue
            direction = _upper(position.get("direction"))
            rows.append(
                {
                    "id": f"stock:broker:{position.get('exit_execution_id')}",
                    "symbol": position.get("symbol"),
                    "market": "STOCK",
                    "source": position.get("source") or "IBKR",
                    "strategy": position.get("strategy"),
                    "side": position.get("side") or ("SHORT" if direction == "SHORT" else "LONG" if direction == "LONG" else "UNKNOWN"),
                    "status": "CLOSED_OBSERVED",
                    "created_at": position.get("closed_at"),
                    "opened_at": position.get("opened_at") or position.get("first_seen_at"),
                    "closed_at": position.get("closed_at"),
                    "planned_entry": position.get("entry") or position.get("avg_cost"),
                    "planned_stop": position.get("planned_stop_loss") or position.get("stop_loss"),
                    "planned_target": position.get("planned_target") or position.get("target"),
                    "current_stop": position.get("current_stop_loss") or position.get("stop_loss"),
                    "current_target": position.get("current_target") or position.get("target"),
                    "actual_entry": position.get("avg_cost") or position.get("entry"),
                    "actual_exit": position.get("exit_price") or position.get("actual_exit"),
                    "quantity": position.get("exit_quantity") or position.get("quantity"),
                    "pnl": position.get("broker_realized_pnl"),
                    "exit_reason": position.get("exit_reason") or "BROKER_SELL_EXECUTION",
                    "execution_id": position.get("exit_execution_id"),
                }
            )
        return rows

    def _state_rows(self, path: Path, *, market: str, source: str) -> list[dict[str, Any]]:
        payload = self._read_json(path, {"executions": []})
        executions = payload.get("executions", []) if isinstance(payload, dict) else []
        rows: list[dict[str, Any]] = []
        for execution in executions if isinstance(executions, list) else []:
            if not isinstance(execution, dict):
                continue
            action = _upper(execution.get("action"))
            status = _upper(execution.get("status"))
            if action == "BUY" and status in {"QUEUED", "SUBMITTED"}:
                status = "OPEN_ALERT"
            elif action == "SELL" and status in {"QUEUED", "SUBMITTED"}:
                status = "CLOSE_ALERT"
            rows.append(
                {
                    "id": f"{market.lower()}:state:{execution.get('id')}",
                    "symbol": execution.get("symbol") or execution.get("inst_id") or execution.get("ticker"),
                    "market": market,
                    "source": source,
                    "strategy": execution.get("strategy"),
                    "side": action,
                    "status": status or "UNKNOWN",
                    "created_at": execution.get("created_at"),
                    "opened_at": execution.get("opened_at") or execution.get("created_at"),
                    "closed_at": execution.get("created_at") if action == "SELL" else None,
                    "planned_entry": execution.get("entry") or execution.get("price"),
                    "planned_stop": execution.get("stop"),
                    "planned_target": execution.get("target"),
                    "actual_entry": execution.get("entry") if action == "BUY" else None,
                    "actual_exit": execution.get("price") if action == "SELL" else None,
                    "quantity": execution.get("estimated_qty") or execution.get("quantity") or execution.get("tv_position_size"),
                    "pnl": execution.get("estimated_pnl") or execution.get("realized_pnl"),
                    "exit_reason": "TRADINGVIEW_SELL" if action == "SELL" else None,
                    "execution_id": execution.get("id"),
                }
            )
        return rows

    def _normalize(self, row: dict[str, Any]) -> ResearchTrade:
        raw_strategy = _text(row.get("strategy"))
        normalized_strategy = raw_strategy.upper() if raw_strategy else ""
        matched_strategy = _EMBEDDED_STRATEGY_ID.search(normalized_strategy)
        strategy = (
            "strategy_unknown"
            if normalized_strategy.lower() in _UNKNOWN_STRATEGY_VALUES
            else matched_strategy.group(1)
            if matched_strategy
            else normalized_strategy
        )
        status = _upper(row.get("status")) or "UNKNOWN"
        side = _upper(row.get("side")) or "UNKNOWN"
        entry = _first_decimal(row.get("actual_entry"), row.get("entry_price"))
        exit_price = _first_decimal(row.get("actual_exit"), row.get("exit_price"))
        missing: list[str] = []
        if strategy == "strategy_unknown":
            missing.append("strategy")
        if entry is None:
            missing.append("entry_fill")
        if exit_price is None and status_group(status) == "completed":
            missing.append("exit_fill")
        if decimal_or_none(row.get("quantity")) is None:
            missing.append("quantity")
        return ResearchTrade(
            trade_id=str(row.get("id") or row.get("trade_id") or "unknown"),
            symbol=_upper(row.get("symbol")) or "symbol_unknown",
            market=_text(row.get("market")),
            source=_text(row.get("source")),
            strategy=strategy,
            strategy_version=_text(row.get("strategy_version")),
            side=side,
            status=status,
            status_group=status_group(status),
            created_at=parse_timestamp(row.get("created_at")),
            setup_time=parse_timestamp(row.get("setup_time")),
            signal_time=parse_timestamp(row.get("signal_time")),
            entry_time=parse_timestamp(row.get("opened_at") or row.get("entry_time")),
            exit_time=parse_timestamp(row.get("closed_at") or row.get("exit_time")),
            planned_entry=decimal_or_none(row.get("planned_entry")),
            planned_stop=decimal_or_none(row.get("planned_stop")),
            planned_target=decimal_or_none(row.get("planned_target")),
            current_stop=decimal_or_none(row.get("current_stop")),
            current_target=decimal_or_none(row.get("current_target")),
            entry_price=entry,
            exit_price=exit_price,
            quantity=decimal_or_none(row.get("quantity")),
            realized_pnl=decimal_or_none(row.get("pnl") or row.get("realized_pnl")),
            fees=decimal_or_none(row.get("fees") or row.get("fee") or row.get("commission")),
            exit_reason=_text(row.get("exit_reason")),
            approval_source=_text(row.get("approval_source")),
            execution_source=_text(row.get("execution_source") or row.get("source")),
            trading_mode=_text(row.get("trading_mode")),
            missing_fields=tuple(missing),
        )
