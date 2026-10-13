"""Narrow loopback client used by the Daily Trader Hermes skills.

The client speaks only the documented Daily Trader HTTP contract.  It does not
import trading source files, invoke the scanner, or expose an arbitrary URL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:8550"
RENDERED_SYMBOL_PATTERN = re.compile(r"\bsymbol=([A-Za-z0-9][A-Za-z0-9.\-]*)")


def _base_url() -> str:
    value = str(os.environ.get("DAILY_TRADER_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Daily Trader API must use an HTTP loopback URL")
    if parsed.path not in {"", "/"}:
        raise ValueError("DAILY_TRADER_API_BASE_URL must contain only the loopback origin")
    return value


def _request(path: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    url = f"{_base_url()}{path}"
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("Daily Trader API unavailable. No market report generated.") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Daily Trader API returned invalid JSON.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Daily Trader API returned an invalid response object.")
    if result.get("read_only") is not True or result.get("orders_submitted") != 0:
        raise RuntimeError("Daily Trader API safety contract failed; no report generated.")
    return result


def _raw_request(path: str) -> dict:
    url = f"{_base_url()}{path}"
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("Daily Trader API unavailable. No market report generated.") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Daily Trader API returned invalid JSON.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Daily Trader API returned an invalid response object.")
    return result


def _radar(flips: bool, limit: int) -> dict:
    safe_limit = max(1, min(int(limit), 100))
    path = f"/api/radar/flips?limit={safe_limit}" if flips else f"/api/radar?limit={safe_limit}"
    result = _raw_request(path)
    rows = [row for row in (result.get("results") or []) if isinstance(row, dict)]
    return {
        "mode": "fresh_flips" if flips else "radar",
        "read_only": True,
        "orders_submitted": 0,
        "count": len(rows),
        "updated_at": result.get("updated_at"),
        "results": rows,
    }


def _setups(side: str | None) -> dict:
    side_filter = str(side or "").strip().upper()
    result = _raw_request("/api/radar?limit=5000")
    rows = [row for row in (result.get("results") or []) if isinstance(row, dict)]
    selected = []
    for row in rows:
        scanner = row.get("scanner_result")
        if not isinstance(scanner, dict):
            continue
        payload = scanner.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        signals = [item for item in (payload.get("signals") or []) if isinstance(item, dict)]
        if not signals:
            continue
        for signal in signals:
            signal_side = str(signal.get("side") or signal.get("direction") or scanner.get("side") or "").strip().upper()
            if side_filter and signal_side != side_filter:
                continue
            selected.append(
                {
                    "symbol": row.get("symbol"),
                    "radar": row,
                    "scanner": {
                        **signal,
                        "side": signal_side or signal.get("side") or signal.get("direction"),
                        "scanner_status": scanner.get("scanner_status"),
                        "scanner_label": scanner.get("scanner_label"),
                        "scan_timestamp": scanner.get("scan_timestamp"),
                        "source_scan_id": scanner.get("source_scan_id"),
                    },
                }
            )
    active_statuses = {"PENDING_ENTRY", "ENTRY_TRIGGERED", "READY"}
    active = [
        item
        for item in selected
        if str(item.get("scanner", {}).get("lifecycle_status") or item.get("scanner", {}).get("scanner_status") or "").upper()
        in active_statuses
    ]
    terminal = [item for item in selected if item not in active]
    return {
        "mode": "persisted_setups",
        "side": side_filter or "ALL",
        "read_only": True,
        "orders_submitted": 0,
        "updated_at": result.get("updated_at"),
        "summary": {
            "active_candidates": len(active),
            "terminal": len(terminal),
            "persisted_setups": len(selected),
        },
        "active": active,
        "terminal": terminal,
    }


def _value(value: object) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _setup_symbol(item: dict) -> str:
    symbol = item.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise RuntimeError("Daily Trader API returned a setup without an authoritative symbol.")
    return symbol.strip()


def _setup_line(item: dict) -> str:
    symbol = _setup_symbol(item)
    scanner = item.get("scanner")
    scanner = scanner if isinstance(scanner, dict) else {}
    radar = item.get("radar")
    radar = radar if isinstance(radar, dict) else {}
    lifecycle_status = scanner.get("lifecycle_status")
    scanner_status = scanner.get("scanner_status")
    scanner_trade_status = scanner.get("status")
    return " | ".join(
        [
            f"symbol={symbol}",
            f"strategy={_value(scanner.get('strategy'))}",
            f"side={_value(scanner.get('side'))}",
            f"lifecycle_status={_value(lifecycle_status)}",
            f"scanner_trade_status={_value(scanner_trade_status)}",
            f"scanner_status={_value(scanner_status)}",
            f"scanner_label={_value(scanner.get('scanner_label'))}",
            f"scan_timestamp={_value(scanner.get('scan_timestamp'))}",
            f"radar_trend={_value(radar.get('trend'))}",
            f"radar_score={_value(radar.get('radar_score'))}",
            f"planned_entry_price={_value(scanner.get('planned_entry_price'))}",
            f"entry_price={_value(scanner.get('entry_price'))}",
            f"stop_price={_value(scanner.get('stop_price'))}",
            f"take_profit_price={_value(scanner.get('take_profit_price'))}",
            f"rr={_value(scanner.get('rr'))}",
        ]
    )


def _source_setup_symbols(result: dict) -> list[str]:
    symbols: list[str] = []
    for section in ("active", "terminal"):
        for item in result.get(section) or []:
            if isinstance(item, dict):
                symbols.append(_setup_symbol(item))
    return symbols


def _validate_rendered_symbols(rendered: str, source_symbols: list[str]) -> None:
    rendered_symbols = RENDERED_SYMBOL_PATTERN.findall(rendered)
    if rendered_symbols != source_symbols:
        raise RuntimeError(
            "Rendered ticker integrity check failed; no report generated. "
            f"source={source_symbols!r} rendered={rendered_symbols!r}"
        )


def _render_setups_report(result: dict) -> str:
    source_symbols = _source_setup_symbols(result)
    lines = [
        "Daily Trader persisted setups",
        f"mode={_value(result.get('mode'))}",
        f"side={_value(result.get('side'))}",
        f"updated_at={_value(result.get('updated_at'))}",
        f"read_only={_value(result.get('read_only'))}",
        f"orders_submitted={_value(result.get('orders_submitted'))}",
        "symbol_source=authoritative JSON fields only",
        f"ticker_integrity_source_count={len(source_symbols)}",
    ]
    summary = result.get("summary")
    if isinstance(summary, dict):
        lines.append(
            "summary="
            + ", ".join(
                [
                    f"active_candidates={_value(summary.get('active_candidates'))}",
                    f"terminal={_value(summary.get('terminal'))}",
                    f"persisted_setups={_value(summary.get('persisted_setups'))}",
                ]
            )
        )
    lines.append("active:")
    active = [item for item in (result.get("active") or []) if isinstance(item, dict)]
    if active:
        lines.extend(_setup_line(item) for item in active)
    else:
        lines.append("-")
    lines.append("terminal:")
    terminal = [item for item in (result.get("terminal") or []) if isinstance(item, dict)]
    if terminal:
        lines.extend(_setup_line(item) for item in terminal)
    else:
        lines.append("-")
    lines.append("ticker_integrity=validated_against_source_json")
    rendered = "\n".join(lines)
    _validate_rendered_symbols(rendered, source_symbols)
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Call the read-only Daily Trader loopback API")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan")
    scan.add_argument("--mode", choices=("scheduled", "top", "breakouts", "fresh_flips"), default="scheduled")
    scan.add_argument("--top-n", type=int, default=10)
    scan.add_argument("--max-symbols", type=int, default=30)
    scan.add_argument("--no-breakouts", action="store_true")
    scan.add_argument("--no-fresh-flips", action="store_true")

    symbol = subparsers.add_parser("symbol")
    symbol.add_argument("symbol")
    radar = subparsers.add_parser("radar")
    radar.add_argument("--flips", action="store_true")
    radar.add_argument("--limit", type=int, default=20)
    setups = subparsers.add_parser("setups")
    setups.add_argument("--side", choices=("LONG", "SHORT"), default=None)
    setups.add_argument(
        "--format",
        choices=("json", "report"),
        default="json",
        help="Use deterministic report formatting for ticker-bearing Daily Trader output.",
    )
    subparsers.add_parser("health")
    args = parser.parse_args(argv)

    try:
        if args.command == "scan":
            result = _request(
                "/api/daily-trader/scan",
                method="POST",
                payload={
                    "mode": args.mode,
                    "top_n": args.top_n,
                    "include_breakouts": not args.no_breakouts,
                    "include_fresh_flips": not args.no_fresh_flips,
                    "max_symbols": args.max_symbols,
                },
            )
        elif args.command == "symbol":
            normalized = str(args.symbol or "").strip().upper()
            if not normalized or normalized == "ALL" or not normalized.replace(".", "").replace("-", "").isalnum():
                raise ValueError("A single stock symbol is required")
            result = _request(f"/api/daily-trader/symbol/{quote(normalized, safe='')}")
        elif args.command == "radar":
            result = _radar(args.flips, args.limit)
        elif args.command == "setups":
            result = _setups(args.side)
        else:
            result = _request("/api/daily-trader/health")
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.command == "setups" and args.format == "report":
        print(_render_setups_report(result))
    else:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
