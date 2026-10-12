"""Narrow loopback client used by the Daily Trader Hermes skills.

The client speaks only the documented Daily Trader HTTP contract.  It does not
import trading source files, invoke the scanner, or expose an arbitrary URL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:8550"


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
        else:
            result = _request("/api/daily-trader/health")
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
