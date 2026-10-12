"""Narrow loopback client for the read-only Pre-Trade Reviewer API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pre_trade_reviewer import review_batch_payload, review_symbol_payload

DEFAULT_BASE_URL = "http://127.0.0.1:8550"


def _base_url() -> str:
    value = str(os.environ.get("PRE_TRADE_REVIEW_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path not in {"", "/"}:
        raise ValueError("Pre-Trade Reviewer API must use an HTTP loopback URL")
    return value


def _request(path: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    request = Request(f"{_base_url()}{path}", data=body, method=method, headers={"Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode())
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("PRE-TRADE REVIEW UNAVAILABLE\nLocal trading API is not reachable.\nNo review generated.\nNo trading action taken.") from exc
    if not isinstance(result, dict) or result.get("read_only") is not True or result.get("orders_submitted") != 0:
        raise RuntimeError("PRE-TRADE REVIEW UNAVAILABLE\nReviewer safety contract failed.\nNo review generated.\nNo trading action taken.")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Call the read-only Pre-Trade Reviewer loopback API")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("symbol")
    one.add_argument("symbol")
    batch = sub.add_parser("batch")
    batch.add_argument("symbols", nargs="+")
    sub.add_parser("health")
    args = parser.parse_args(argv)
    try:
        if args.command == "symbol":
            source = _request(f"/api/daily-trader/symbol/{quote(str(args.symbol).strip().upper(), safe='')}")
            result = {"symbol": source.get("symbol"), "reviews": [item.to_dict() for item in review_symbol_payload(source)], "read_only": source.get("read_only"), "orders_submitted": source.get("orders_submitted", 0), "approval_created": False}
        elif args.command == "batch":
            sources = [_request(f"/api/daily-trader/symbol/{quote(str(symbol).strip().upper(), safe='')}") for symbol in dict.fromkeys(args.symbols)]
            result = {"requested": len(args.symbols), "reviews": [item.to_dict() for item in review_batch_payload(sources)], "read_only": all(item.get("read_only") is True for item in sources), "orders_submitted": sum(int(item.get("orders_submitted") or 0) for item in sources), "approval_created": False}
        else:
            result = _request("/api/daily-trader/health")
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
