"""IBKR Flex Statement catch-up import primitives.

Flex is deliberately optional and fail-closed: without locally configured
credentials the EOD job reports ``disabled`` and never fabricates executions.
"""

from __future__ import annotations

import json
import csv
import hashlib
from html.parser import HTMLParser
import os
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List
from zoneinfo import ZoneInfo

from src.config import LOGGER, MEMORY_DIR, SETTINGS

FLEX_BASE_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
FLEX_STATE_PATH = MEMORY_DIR / "runtime" / "flex_statement_reconciliation.json"
FLEX_ARCHIVE_DIR = MEMORY_DIR / "runtime" / "flex_statements"
FLEX_UNMATCHED_PATH = MEMORY_DIR / "runtime" / "flex_unmatched_trades.json"
_TRADE_REPORT_TZ = ZoneInfo("America/New_York")


def normalize_trade_timestamp(value: Any) -> str:
    """Normalize broker/report timestamps to ISO-8601 with an explicit offset."""
    text = str(value or "").strip()
    if not text:
        return ""
    candidates = (text, text.replace(",", " ").strip())
    for candidate in candidates:
        for fmt in (
            "%Y%m%d %H:%M:%S",
            "%Y%m%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                parsed = datetime.strptime(candidate, fmt)
            except ValueError:
                continue
            return parsed.replace(tzinfo=_TRADE_REPORT_TZ).isoformat(timespec="seconds")
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_TRADE_REPORT_TZ)
        return parsed.isoformat(timespec="seconds")
    return text


def _read_state() -> Dict[str, Any]:
    try:
        return json.loads(FLEX_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"imported_execution_ids": [], "last_successful_statement_at": None}


def _write_state(state: Dict[str, Any]) -> None:
    FLEX_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = FLEX_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temporary, FLEX_STATE_PATH)


def _request(path: str, params: Dict[str, str], timeout: int = 30) -> bytes:
    query = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{FLEX_BASE_URL}/{path}?{query}", timeout=timeout) as response:
        return response.read()


def _text(node: ET.Element, *names: str) -> str:
    for name in names:
        value = node.attrib.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


def parse_flex_trades(xml_bytes: bytes) -> List[Dict[str, Any]]:
    """Normalize Flex ``Trade`` elements without assuming one XML schema case."""
    root = ET.fromstring(xml_bytes)
    trades: List[Dict[str, Any]] = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].lower() != "trade":
            continue
        quantity = _text(node, "quantity", "Quantity")
        price = _text(node, "tradePrice", "price", "Price")
        commission = _text(node, "ibCommission", "commission", "Comm")
        fee = _text(node, "ibTax", "fee", "Fee")
        realized = _text(node, "fifoPnlRealized", "realizedPNL", "realizedPnl")
        trades.append(
            {
                "symbol": _text(node, "symbol", "Symbol").upper(),
                "side": _text(node, "buySell", "side", "BuySell").upper(),
                "quantity": float(quantity or 0),
                "price": float(price or 0),
                "commission": float(commission or 0),
                "fee": float(fee or 0),
                "realized_pnl": float(realized) if realized else None,
                "trade_time": normalize_trade_timestamp(_text(node, "tradeDate", "dateTime", "TradeDate")),
                "order_id": _text(node, "ibOrderID", "orderID", "orderId"),
                "exec_id": _text(node, "ibExecID", "execID", "execId"),
                "account": _text(node, "accountId", "account", "AcctID"),
            }
        )
    return [trade for trade in trades if trade["symbol"] and trade["quantity"]]


def parse_trade_report_csv(raw: bytes) -> List[Dict[str, Any]]:
    """Parse TWS Export Reports (comma-delimited, including UTF-8/UTF-16)."""
    text = raw.decode("utf-8-sig", errors="replace")
    if "\x00" in text:
        text = raw.decode("utf-16", errors="replace")
    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        return []
    aliases = {
        "symbol": ("symbol", "ticker"), "side": ("action", "side", "buy/sell", "buysell"),
        "quantity": ("quantity", "qty", "shares"), "price": ("price", "trade price", "tradeprice"),
        "trade_time": ("time", "trade time", "datetime", "date/time", "tradedate"),
        "trade_date": ("date", "trade date", "tradedate"),
        "order_id": ("order id", "orderid", "iborderid"), "exec_id": ("exec id", "execid", "ibexecid"),
        "commission": ("commission", "ibcommission"), "fee": ("fee", "fees", "ib tax", "ibtax"),
        "realized_pnl": ("realized pnl", "realized p&l", "realizedpnl", "fifopnlrealized"),
        "account": ("account", "account id", "accountid"),
    }
    def normalized(value: str) -> str:
        return " ".join(str(value or "").strip().lower().replace("_", " ").split())
    headers = {normalized(header): header for header in (reader.fieldnames or [])}
    def value(row: Dict[str, Any], key: str) -> str:
        for alias in aliases[key]:
            header = headers.get(normalized(alias))
            if header is not None and row.get(header) not in (None, ""):
                return str(row[header]).strip()
        return ""
    def number(value_text: str) -> float:
        try:
            return float(value_text.replace(",", "").replace("$", "").strip()) if value_text else 0.0
        except ValueError:
            return 0.0
    trades: List[Dict[str, Any]] = []
    for row in reader:
        symbol = value(row, "symbol").upper()
        quantity = number(value(row, "quantity"))
        if not symbol or not quantity:
            continue
        realized_text = value(row, "realized_pnl")
        trade_time = value(row, "trade_time")
        trade_date = value(row, "trade_date")
        if trade_date and trade_time and trade_date != trade_time:
            trade_time = f"{trade_date} {trade_time}"
        trade_time = normalize_trade_timestamp(trade_time)
        exec_id = value(row, "exec_id")
        if not exec_id:
            # TWS periodic reports often omit Exec ID.  Derive a stable key
            # from immutable report fields so repeated exports remain idempotent.
            identity = "|".join((symbol, value(row, "side").upper(), str(quantity), value(row, "price"), trade_time, value(row, "order_id")))
            exec_id = "tws-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        trades.append({
            "symbol": symbol, "side": value(row, "side").upper(), "quantity": abs(quantity),
            "price": number(value(row, "price")), "commission": number(value(row, "commission")),
            "fee": number(value(row, "fee")), "realized_pnl": number(realized_text) if realized_text else None,
            "trade_time": trade_time, "order_id": value(row, "order_id"),
            "exec_id": exec_id, "account": value(row, "account"),
        })
    return trades


class _TradeReportHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: List[List[str]] = []
        self._cells: List[str] | None = None
        self._cell_text: List[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._cells = []
        elif tag.lower() in {"td", "th"} and self._cells is not None:
            self._cell_text = []

    def handle_data(self, data: str) -> None:
        if self._cells is not None:
            self._cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._cells is not None:
            self._cells.append(" ".join("".join(self._cell_text).split()))
            self._cell_text = []
        elif lowered == "tr" and self._cells is not None:
            if self._cells:
                self.rows.append(self._cells)
            self._cells = None


def parse_trade_report_html(raw: bytes) -> List[Dict[str, Any]]:
    """Parse IBKR Trade Confirmation HTML exports (summary/detail rows)."""
    text = raw.decode("utf-8-sig", errors="replace")
    parser = _TradeReportHTMLParser()
    parser.feed(text)
    header = next((row for row in parser.rows if "Trade Date/Time" in row and "Type" in row), None)
    if not header:
        return []
    indexes = {name: header.index(name) for name in ("Acct ID", "Symbol", "Trade Date/Time", "Type", "Quantity", "Price", "Comm", "Fee", "Order Type") if name in header}
    if "Symbol" not in indexes or "Type" not in indexes:
        return []
    def field(row: List[str], name: str) -> str:
        index = indexes.get(name)
        return row[index] if index is not None and index < len(row) else ""
    def number(value_text: str) -> float:
        try:
            return float(value_text.replace(",", "").replace("$", "").strip()) if value_text else 0.0
        except ValueError:
            return 0.0
    trades: List[Dict[str, Any]] = []
    for row in parser.rows:
        side = field(row, "Type").upper()
        symbol = field(row, "Symbol").upper()
        quantity = abs(number(field(row, "Quantity")))
        if side not in {"BUY", "BOT", "SELL", "SLD"} or not symbol or not quantity:
            continue
        trade_time = normalize_trade_timestamp(field(row, "Trade Date/Time"))
        identity = "|".join((symbol, side, str(quantity), field(row, "Price"), trade_time))
        trades.append({
            "symbol": symbol, "side": side, "quantity": quantity, "price": number(field(row, "Price")),
            "commission": number(field(row, "Comm")), "fee": number(field(row, "Fee")),
            "realized_pnl": None, "trade_time": trade_time, "order_id": "",
            "exec_id": "tws-html-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
            "account": field(row, "Acct ID"),
        })
    unique: Dict[str, Dict[str, Any]] = {str(trade["exec_id"]): trade for trade in trades}
    return list(unique.values())


def discover_trade_report() -> tuple[Path | None, List[Dict[str, Any]]]:
    """Return the newest non-empty TWS report from the configured directory."""
    paths = getattr(SETTINGS, "paths", None)
    directory = getattr(paths, "trade_report_dir", MEMORY_DIR / "reports")
    if not directory.exists():
        return None, []
    candidates = sorted(
        (path for path in directory.iterdir() if path.is_file() and (path.suffix.lower() in {".csv", ".txt", ".htm", ".html"} or path.name.lower().startswith("trade_report"))),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            raw = path.read_bytes()
            trades = parse_trade_report_html(raw) if path.suffix.lower() in {".htm", ".html"} else parse_trade_report_csv(raw)
        except OSError:
            continue
        if trades:
            return path, trades
    return None, []


def fetch_flex_statement() -> bytes:
    if not SETTINGS.flex_statement_enabled or not SETTINGS.flex_statement_token or not SETTINGS.flex_statement_query_id:
        raise RuntimeError("IBKR Flex Statement is not configured")
    response = _request("SendRequest", {"t": SETTINGS.flex_statement_token, "q": SETTINGS.flex_statement_query_id, "v": "3"})
    root = ET.fromstring(response)
    reference = next((node.text for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "ReferenceCode"), None)
    status = next((node.text for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Status"), "")
    if status.lower() != "success" or not reference:
        raise RuntimeError(f"IBKR Flex SendRequest failed: {status or 'unknown response'}")
    return _request("GetStatement", {"q": reference, "t": SETTINGS.flex_statement_token, "v": "3"})


def archive_statement(xml_bytes: bytes, fetched_at: datetime | None = None) -> Path:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    FLEX_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = FLEX_ARCHIVE_DIR / f"flex_{fetched_at.strftime('%Y%m%dT%H%M%SZ')}.xml"
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(xml_bytes)
    os.replace(temporary, path)
    return path


def commit_flex_checkpoint(result: Dict[str, Any]) -> None:
    """Commit a fetched statement only after downstream reconciliation succeeds."""
    checkpoint = result.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return
    state = _read_state()
    known = {str(value) for value in state.get("imported_execution_ids", [])}
    known.update(str(value) for value in checkpoint.get("imported_execution_ids", []) if value)
    state["imported_execution_ids"] = sorted(known)
    state["last_successful_statement_at"] = checkpoint.get("last_successful_statement_at")
    state["last_statement_archive"] = checkpoint.get("last_statement_archive")
    _write_state(state)


def run_flex_catch_up(
    commit: bool = True,
    *,
    allow_local_report: bool = False,
) -> Dict[str, Any]:
    """Fetch/archive a statement and return normalized trades plus checkpoint data.

    A TWS trade report is a local, broker-exported execution source and does
    not require Flex credentials.  Callers that are explicitly reconciling
    journal data may opt into that source with ``allow_local_report``.  The
    default remains fail-closed for jobs that require configured Flex access.
    """
    state = _read_state()
    report_path, report_trades = discover_trade_report()
    flex_configured = bool(
        SETTINGS.flex_statement_enabled
        and SETTINGS.flex_statement_token
        and SETTINGS.flex_statement_query_id
    )
    if not flex_configured and not (allow_local_report and report_trades):
        return {"status": "disabled", "reason": "missing_flex_configuration", "trades": []}
    fetched_at = datetime.now(timezone.utc)
    try:
        previous_success = state.get("last_successful_statement_at")
        if previous_success and flex_configured:
            previous_dt = datetime.fromisoformat(str(previous_success).replace("Z", "+00:00"))
            gap_days = max(0, (fetched_at.date() - previous_dt.date()).days)
            if gap_days > max(1, int(SETTINGS.flex_statement_lookback_days)):
                return {
                    "status": "failed",
                    "reason": "gap_exceeds_configured_lookback",
                    "gap_days": gap_days,
                    "lookback_days": int(SETTINGS.flex_statement_lookback_days),
                    "trades": [],
                    "last_successful_statement_at": previous_success,
                }
        trades = list(report_trades)
        archive_path = report_path
        source_parts = ["tws_trade_report"] if report_trades else []
        # Always supplement the periodically-overwritten TWS file with Flex
        # when configured; this is what recovers executions from prior days.
        if flex_configured:
            try:
                xml_bytes = fetch_flex_statement()
                archive_path = archive_statement(xml_bytes, fetched_at)
                trades.extend(parse_flex_trades(xml_bytes))
                source_parts.append("ibkr_flex_statement")
            except Exception:
                if not report_trades:
                    raise
                LOGGER.exception("Flex supplement failed; continuing with local TWS trade report")
        if not trades:
            if not source_parts:
                return {"status": "disabled", "reason": "missing_flex_configuration", "trades": []}
            return {"status": "success", "trades": [], "total_trades": 0, "archive": str(archive_path) if archive_path else None, "source": "+".join(source_parts), "lookback_days": int(SETTINGS.flex_statement_lookback_days), "checkpoint": {"imported_execution_ids": [], "last_successful_statement_at": fetched_at.isoformat(), "last_statement_archive": str(archive_path) if archive_path else None}}
        source = "+".join(source_parts)
        known = {str(value) for value in state.get("imported_execution_ids", [])}
        new_trades = [trade for trade in trades if trade.get("exec_id") and str(trade["exec_id"]) not in known]
        known.update(str(trade["exec_id"]) for trade in new_trades if trade.get("exec_id"))
        result = {
            "status": "success", "trades": new_trades, "total_trades": len(trades),
            "archive": str(archive_path), "lookback_days": int(SETTINGS.flex_statement_lookback_days),
            "source": source,
            "checkpoint": {
                "imported_execution_ids": sorted(known),
                "last_successful_statement_at": fetched_at.isoformat(),
                "last_statement_archive": str(archive_path),
            },
        }
        if commit:
            commit_flex_checkpoint(result)
        return result
    except Exception as exc:
        LOGGER.exception("IBKR Flex catch-up failed")
        return {"status": "failed", "reason": str(exc), "trades": [], "last_successful_statement_at": state.get("last_successful_statement_at")}


def reconcile_statement_trades(trades: Iterable[Dict[str, Any]], return_details: bool = False) -> int | Dict[str, int]:
    """Persist statement-backed closes matched to local bracket requests."""
    requests_path = MEMORY_DIR / "order_requests.json"
    closes_path = MEMORY_DIR / "runtime" / "closed_positions.json"
    try:
        requests = json.loads(requests_path.read_text(encoding="utf-8")).get("requests", [])
    except (OSError, json.JSONDecodeError):
        requests = []
    try:
        payload = json.loads(closes_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {"positions": []}
    positions = payload.get("positions") if isinstance(payload, dict) else []
    if not isinstance(positions, list):
        positions = []
    # Older report imports could generate different synthetic IDs for the same
    # HTML summary/detail row. Collapse those duplicates before reconciling.
    deduped_positions: List[Dict[str, Any]] = []
    seen_position_keys: set[tuple[Any, ...]] = set()
    for item in positions:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("symbol") or "").upper(), abs(float(item.get("exit_quantity") or item.get("quantity") or 0)), round(float(item.get("exit_price") or 0), 8), " ".join(str(item.get("closed_at") or "").replace(",", " ").split()))
        if key in seen_position_keys:
            continue
        seen_position_keys.add(key)
        deduped_positions.append(item)
    positions_changed = len(deduped_positions) != len(positions)
    positions = deduped_positions
    existing_ids = {str(item.get("exit_execution_id")) for item in positions if isinstance(item, dict)}
    existing_natural = {
        (str(item.get("symbol") or "").upper(), abs(float(item.get("exit_quantity") or item.get("quantity") or 0)),
         round(float(item.get("exit_price") or 0), 8), " ".join(str(item.get("closed_at") or "").replace(",", " ").split()))
        for item in positions if isinstance(item, dict)
    }
    try:
        unmatched_payload = json.loads(FLEX_UNMATCHED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        unmatched_payload = {"trades": []}
    unmatched = unmatched_payload.get("trades") if isinstance(unmatched_payload, dict) else []
    if not isinstance(unmatched, list):
        unmatched = []
    unique_unmatched: Dict[tuple[Any, ...], Dict[str, Any]] = {}
    for item in unmatched:
        if isinstance(item, dict):
            key = (str(item.get("symbol") or "").upper(), abs(float(item.get("quantity") or 0)), round(float(item.get("price") or 0), 8), " ".join(str(item.get("trade_time") or "").replace(",", " ").split()))
            unique_unmatched.setdefault(key, item)
    unmatched = list(unique_unmatched.values())
    unmatched_ids = {str(item.get("exec_id")) for item in unmatched if isinstance(item, dict)}
    added = 0
    for sell in trades:
        if str(sell.get("side", "")).upper() not in {"SELL", "SLD"}:
            continue
        execution_id = str(sell.get("exec_id") or "")
        if not execution_id or execution_id in existing_ids:
            continue
        symbol = str(sell.get("symbol") or "").upper()
        quantity = abs(float(sell.get("quantity") or 0))
        natural_key = (symbol, quantity, round(float(sell.get("price") or 0), 8), " ".join(str(sell.get("trade_time") or "").replace(",", " ").split()))
        if natural_key in existing_natural:
            existing_ids.add(execution_id)
            continue
        candidates = []
        for request in requests:
            if not isinstance(request, dict) or str(request.get("symbol") or "").upper() != symbol:
                continue
            result = request.get("result") if isinstance(request.get("result"), dict) else {}
            ids = {str(result.get(key)) for key in ("stop_order_id", "limit_order_id") if result.get(key) not in (None, "")}
            if sell.get("order_id") and str(sell["order_id"]) in ids:
                candidates.insert(0, request)
            elif float(request.get("quantity") or result.get("quantity") or 0) == quantity:
                candidates.append(request)
        if not candidates:
            if execution_id not in unmatched_ids:
                unmatched.append(dict(sell))
                unmatched_ids.add(execution_id)
            continue
        request = candidates[0]
        result = request.get("result") if isinstance(request.get("result"), dict) else {}
        entry = float(request.get("entry") or result.get("entry") or 0)
        matching_buys = [trade for trade in trades if str(trade.get("symbol") or "").upper() == symbol and str(trade.get("side", "")).upper() in {"BUY", "BOT"} and abs(float(trade.get("quantity") or 0)) == quantity]
        buy_costs = 0.0
        if matching_buys:
            buy = sorted(matching_buys, key=lambda item: str(item.get("trade_time") or ""))[0]
            entry = float(buy.get("price") or entry)
            opened_at = buy.get("trade_time") or request.get("updated_at")
            buy_costs = float(buy.get("commission") or 0) + float(buy.get("fee") or 0)
        else:
            opened_at = request.get("updated_at") or request.get("created_at")
        exit_price = float(sell.get("price") or 0)
        net_pnl = sell.get("realized_pnl")
        if net_pnl is None:
            net_pnl = (exit_price - entry) * quantity + buy_costs + float(sell.get("commission") or 0) + float(sell.get("fee") or 0)
        position = {
            "symbol": symbol, "quantity": quantity, "entry": entry, "avg_cost": entry,
            "opened_at": opened_at, "closed_at": sell.get("trade_time"),
            "exit_price": exit_price, "exit_quantity": quantity,
            "exit_order_id": sell.get("order_id"), "exit_execution_id": execution_id,
            "exit_reason": (
                "BRACKET_STOP_FILLED"
                if sell.get("order_id") and str(sell.get("order_id")) == str(result.get("stop_order_id"))
                else "BRACKET_TARGET_FILLED"
                if sell.get("order_id") and str(sell.get("order_id")) == str(result.get("limit_order_id"))
                else "BROKER_SELL_EXECUTION"
            ),
            "market_order_id": result.get("market_order_id"), "stop_order_id": result.get("stop_order_id"),
            "limit_order_id": result.get("limit_order_id"), "request_id": str(request.get("id") or ""),
            "commission": float(sell.get("commission") or 0), "fee": float(sell.get("fee") or 0),
            "broker_realized_pnl": float(net_pnl), "source": "IBKR_FLEX_STATEMENT",
            "entry_price_source": "ibkr_flex_statement",
        }
        positions.append(position)
        existing_ids.add(execution_id)
        existing_natural.add(natural_key)
        added += 1
    if added or positions_changed:
        payload["positions"] = positions
        closes_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = closes_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, closes_path)
    if unmatched:
        FLEX_UNMATCHED_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = FLEX_UNMATCHED_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps({"trades": unmatched}, indent=2), encoding="utf-8")
        os.replace(temporary, FLEX_UNMATCHED_PATH)
    details = {"added": added, "unmatched": len(unmatched)}
    return details if return_details else added
