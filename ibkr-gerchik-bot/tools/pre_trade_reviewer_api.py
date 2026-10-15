"""Narrow loopback client for the read-only Pre-Trade Reviewer API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from uuid import UUID
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pre_trade_reviewer import review_batch_payload, review_symbol_payload

DEFAULT_BASE_URL = "http://127.0.0.1:8550"


def parse_candidate_reference(reference: str) -> dict[str, object]:
    parts = str(reference or "").strip().split(":")
    if len(parts) != 4:
        raise ValueError("Candidate reference must be <full-scan-uuid>:<active|terminal>:<symbol>:<signal-ordinal>")
    scan_id, section, symbol, ordinal = parts
    try:
        scan_id = str(UUID(scan_id))
        signal_ordinal = int(ordinal)
    except (ValueError, TypeError) as exc:
        raise ValueError("Candidate reference must contain a full scan UUID and positive signal ordinal") from exc
    if section not in {"active", "terminal"} or not symbol.strip() or signal_ordinal < 1:
        raise ValueError("Candidate reference has invalid section, symbol, or signal ordinal")
    return {
        "scan_id": scan_id,
        "section": section,
        "symbol": symbol.strip().upper(),
        "signal_ordinal": signal_ordinal,
    }


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


def _scan_status_request(scan_id: str) -> dict:
    safe_scan_id = quote(str(scan_id or "").strip(), safe="")
    if not safe_scan_id:
        raise ValueError("Candidate review requires scan_id")
    request = Request(
        f"{_base_url()}/api/daily-trader/scans/{safe_scan_id}",
        method="GET",
        headers={"Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode())
    except HTTPError as exc:
        if exc.code == 404:
            raise ValueError("Candidate review scan_id was not found") from exc
        raise RuntimeError("PRE-TRADE REVIEW UNAVAILABLE\nDaily Trader scan lookup failed.\nNo review generated.\nNo trading action taken.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("PRE-TRADE REVIEW UNAVAILABLE\nDaily Trader scan lookup is not reachable.\nNo review generated.\nNo trading action taken.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("PRE-TRADE REVIEW UNAVAILABLE\nDaily Trader scan lookup returned invalid JSON.\nNo review generated.\nNo trading action taken.")
    if any(
        (
            result.get("read_only") is not True,
            result.get("orders_submitted") != 0,
            result.get("approvals_created") != 0,
            result.get("execution_intents_created") != 0,
        )
    ):
        raise RuntimeError("PRE-TRADE REVIEW UNAVAILABLE\nDaily Trader scan safety contract failed.\nNo review generated.\nNo trading action taken.")
    return result


def _latest_scan_request() -> dict:
    request = Request(
        f"{_base_url()}/api/daily-trader/scans/latest",
        method="GET",
        headers={"Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode())
    except HTTPError as exc:
        if exc.code == 404:
            raise ValueError("No completed Daily Trader scan with a valid persisted result was found") from exc
        raise RuntimeError("PRE-TRADE REVIEW UNAVAILABLE\nLatest Daily Trader scan lookup failed.\nNo review generated.\nNo trading action taken.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("PRE-TRADE REVIEW UNAVAILABLE\nLatest Daily Trader scan lookup is not reachable.\nNo review generated.\nNo trading action taken.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("PRE-TRADE REVIEW UNAVAILABLE\nLatest Daily Trader scan lookup returned invalid JSON.\nNo review generated.\nNo trading action taken.")
    if any(
        (
            result.get("read_only") is not True,
            result.get("orders_submitted") != 0,
            result.get("approvals_created") != 0,
            result.get("execution_intents_created") != 0,
        )
    ):
        raise RuntimeError("PRE-TRADE REVIEW UNAVAILABLE\nLatest Daily Trader scan safety contract failed.\nNo review generated.\nNo trading action taken.")
    return result


def _candidate_resolution_error(*, scan_id: str, status: str, reason: str, detail: str) -> dict:
    return {
        "scan_id": scan_id,
        "resolution_status": status,
        "resolution_reason": reason,
        "resolution_detail": detail,
        "reviews": [],
        "read_only": True,
        "orders_submitted": 0,
        "approval_created": False,
        "approvals_created": 0,
        "execution_intents_created": 0,
    }


def _resolve_candidate(status: dict, *, scan_id: str, symbol: str, section: str, signal_ordinal: int) -> dict:
    normalized_symbol = str(symbol or "").strip().upper()
    normalized_section = str(section or "").strip().lower()
    if not normalized_symbol:
        raise ValueError("Candidate review requires symbol")
    if normalized_section not in {"active", "terminal"}:
        raise ValueError("Candidate review section must be active or terminal")
    if int(signal_ordinal) < 1:
        raise ValueError("Candidate review signal_ordinal must be positive")

    scan_status = str(status.get("status") or "").upper()
    if scan_status != "COMPLETED":
        return _candidate_resolution_error(
            scan_id=scan_id,
            status="SCAN_NOT_COMPLETED",
            reason="DAILY_TRADER_SCAN_NOT_COMPLETED",
            detail=f"The selected scan is {scan_status or 'UNKNOWN'}; only COMPLETED scans are reviewable.",
        )
    result = status.get("result")
    if not isinstance(result, dict) or not result.get("radar_as_of"):
        return _candidate_resolution_error(
            scan_id=scan_id,
            status="STALE_OR_INCOMPLETE",
            reason="DAILY_TRADER_SCAN_RESULT_UNAVAILABLE",
            detail="The completed scan does not contain an authoritative persisted result snapshot.",
        )

    rows = result.get(normalized_section)
    if not isinstance(rows, list):
        return _candidate_resolution_error(
            scan_id=scan_id,
            status="INCOMPLETE",
            reason="DAILY_TRADER_SCAN_SECTION_UNAVAILABLE",
            detail=f"The completed scan does not contain a valid {normalized_section} section.",
        )

    matches = []
    for index, item in enumerate(rows, start=1):
        if not isinstance(item, dict):
            continue
        row_ordinal = item.get("signal_ordinal", index)
        try:
            row_ordinal = int(row_ordinal)
        except (TypeError, ValueError):
            continue
        if row_ordinal == int(signal_ordinal) and str(item.get("symbol") or "").strip().upper() == normalized_symbol:
            matches.append(item)
    if not matches:
        symbol_count = sum(
            1
            for item in rows
            if isinstance(item, dict) and str(item.get("symbol") or "").strip().upper() == normalized_symbol
        )
        reason = "DAILY_TRADER_CANDIDATE_NOT_FOUND" if symbol_count == 0 else "DAILY_TRADER_CANDIDATE_ORDINAL_NOT_FOUND"
        detail = (
            f"No {normalized_section} candidate matches symbol={normalized_symbol} and signal_ordinal={signal_ordinal}."
            if symbol_count == 0
            else f"The symbol has {symbol_count} {normalized_section} signal(s), but signal_ordinal={signal_ordinal} is not present."
        )
        return _candidate_resolution_error(scan_id=scan_id, status="NOT_FOUND", reason=reason, detail=detail)
    if len(matches) != 1:
        return _candidate_resolution_error(
            scan_id=scan_id,
            status="AMBIGUOUS",
            reason="DAILY_TRADER_CANDIDATE_AMBIGUOUS",
            detail=f"More than one candidate matched the explicit selection for {normalized_symbol}.",
        )

    item = matches[0]
    scanner = item.get("scanner") if isinstance(item.get("scanner"), dict) else {}
    candidate = {
        "scan_id": scan_id,
        "section": normalized_section,
        "signal_ordinal": int(signal_ordinal),
        "symbol": normalized_symbol,
        "strategy": scanner.get("strategy"),
        "side": scanner.get("side"),
        "level_price": scanner.get("level_price", scanner.get("level")),
        "source_scan_id": scanner.get("source_scan_id"),
        "scan_timestamp": scanner.get("scan_timestamp"),
        "lifecycle_status": scanner.get("lifecycle_status"),
        "scanner": dict(scanner),
        "radar": item.get("radar"),
        "market_status": result.get("market_status"),
        "read_only": True,
        "orders_submitted": 0,
    }
    review_payload = {
        "symbol": normalized_symbol,
        "market_status": result.get("market_status"),
        "read_only": True,
        "orders_submitted": 0,
        "active": [item] if normalized_section == "active" else [],
        "terminal": [item] if normalized_section == "terminal" else [],
        "no_setup": [],
        "scanner_status": scanner.get("scanner_status"),
    }
    reviews = [review.to_dict() for review in review_symbol_payload(review_payload)]
    identity = {
        "scan_id": scan_id,
        "section": normalized_section,
        "symbol": normalized_symbol,
        "signal_ordinal": int(signal_ordinal),
    }
    for review in reviews:
        review["candidate_identity"] = identity
    return {
        "scan_id": scan_id,
        "resolution_status": "RESOLVED",
        "candidate": candidate,
        "reviews": reviews,
        "read_only": True,
        "orders_submitted": 0,
        "approval_created": False,
        "approvals_created": 0,
        "execution_intents_created": 0,
    }


def review_candidate(*, scan_id: str, symbol: str, section: str, signal_ordinal: int) -> dict:
    status = _scan_status_request(scan_id)
    return _resolve_candidate(
        status,
        scan_id=str(scan_id).strip(),
        symbol=symbol,
        section=section,
        signal_ordinal=signal_ordinal,
    )


def review_latest(*, symbol: str | None = None) -> dict:
    status = _latest_scan_request()
    if str(status.get("status") or "").upper() != "COMPLETED":
        raise RuntimeError("Latest scan resolver returned a non-completed scan")
    scan_id = str(status.get("scan_id") or "").strip()
    result = status.get("result")
    if not scan_id or not isinstance(result, dict) or not result.get("radar_as_of"):
        raise RuntimeError("Latest scan resolver returned an incomplete persisted result")
    normalized_symbol = str(symbol or "").strip().upper() or None
    active_reviews: list[dict] = []
    terminal_reviews: list[dict] = []
    for section, destination in (("active", active_reviews), ("terminal", terminal_reviews)):
        rows = result.get(section)
        if not isinstance(rows, list):
            raise RuntimeError(f"Latest scan persisted result has no valid {section} section")
        for index, item in enumerate(rows, start=1):
            if not isinstance(item, dict):
                continue
            item_symbol = str(item.get("symbol") or "").strip().upper()
            if normalized_symbol and item_symbol != normalized_symbol:
                continue
            resolved = _resolve_candidate(
                status,
                scan_id=scan_id,
                symbol=item_symbol,
                section=section,
                signal_ordinal=int(item.get("signal_ordinal", index)),
            )
            if resolved.get("resolution_status") != "RESOLVED":
                raise RuntimeError(resolved.get("resolution_detail") or "Latest candidate resolution failed")
            destination.extend(resolved.get("reviews") or [])
    return {
        "scan_id": scan_id,
        "scan_status": status.get("status"),
        "scan_timestamp": result.get("scan_timestamp") or status.get("completed_at"),
        "radar_as_of": result.get("radar_as_of"),
        "symbol_filter": normalized_symbol,
        "active_candidates_reviewed": len(active_reviews),
        "terminal_candidates": terminal_reviews,
        "terminal_candidates_count": len(terminal_reviews),
        "reviews": active_reviews,
        "read_only": True,
        "orders_submitted": 0,
        "approval_created": False,
        "approvals_created": 0,
        "execution_intents_created": 0,
    }


def _review_scan_rows(status: dict, *, scan_id: str, section: str, rows: list) -> list[dict]:
    reviews: list[dict] = []
    identities: set[tuple[str, str, int]] = set()
    result = status["result"]
    for index, item in enumerate(rows, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"Persisted scan {section} section contains a non-object candidate")
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol:
            raise RuntimeError(f"Persisted scan {section} candidate is missing symbol")
        try:
            ordinal = int(item.get("signal_ordinal", index))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Persisted scan {section} candidate has invalid signal_ordinal") from exc
        if ordinal < 1:
            raise RuntimeError(f"Persisted scan {section} candidate has invalid signal_ordinal")
        identity = (section, symbol, ordinal)
        if identity in identities:
            raise RuntimeError(f"Persisted scan contains duplicate candidate identity: {scan_id}:{section}:{symbol}:{ordinal}")
        identities.add(identity)
        payload = {
            "symbol": symbol,
            "market_status": result.get("market_status"),
            "read_only": status.get("read_only") is True,
            "orders_submitted": int(status.get("orders_submitted") or 0),
            "active": [item] if section == "active" else [],
            "terminal": [item] if section == "terminal" else [],
            "no_setup": [],
            "scanner_status": (item.get("scanner") or {}).get("scanner_status"),
        }
        classified = [review.to_dict() for review in review_symbol_payload(payload)]
        if len(classified) != 1:
            raise RuntimeError(f"Persisted scan candidate did not produce exactly one review: {scan_id}:{section}:{symbol}:{ordinal}")
        classified[0]["candidate_identity"] = {
            "scan_id": scan_id,
            "section": section,
            "symbol": symbol,
            "signal_ordinal": ordinal,
        }
        reviews.extend(classified)
    return reviews


def review_scan(*, scan_id: str, scope: str = "all_active", include_terminal: bool = False, read_only: bool = True) -> dict:
    """Review one completed persisted scan without latest-scan or symbol fallback."""
    normalized_scan_id = str(scan_id or "").strip()
    try:
        canonical_scan_id = str(UUID(normalized_scan_id))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Exact scan review requires a full scan UUID") from exc
    if canonical_scan_id != normalized_scan_id.lower():
        raise ValueError("Exact scan review requires the canonical full scan UUID")
    if scope != "all_active":
        raise ValueError("Exact scan review scope must be all_active")
    if read_only is not True:
        raise ValueError("Exact scan review requires read_only=true")

    status = _scan_status_request(canonical_scan_id)
    if str(status.get("status") or "").upper() != "COMPLETED":
        raise RuntimeError("Exact scan review requires a COMPLETED Daily Trader scan")
    result = status.get("result")
    if not isinstance(result, dict) or not result.get("radar_as_of"):
        raise RuntimeError("Exact scan review requires an authoritative persisted scan result")
    for field in ("orders_submitted", "approvals_created", "execution_intents_created"):
        if int(status.get(field) or 0) != 0:
            raise RuntimeError(f"Exact scan review safety contract failed: {field} must be zero")
    active = result.get("active")
    terminal = result.get("terminal")
    if not isinstance(active, list) or (include_terminal and not isinstance(terminal, list)):
        raise RuntimeError("Exact scan review requires valid persisted active and terminal sections")

    active_reviews = _review_scan_rows(status, scan_id=canonical_scan_id, section="active", rows=active)
    terminal_reviews = (
        _review_scan_rows(status, scan_id=canonical_scan_id, section="terminal", rows=terminal or [])
        if include_terminal else []
    )
    warnings = list(result.get("warnings") or [])
    if result.get("errors"):
        warnings.extend(f"Persisted scan error: {error}" for error in result["errors"])
    return {
        "scan_id": canonical_scan_id,
        "scan_status": status.get("status"),
        "scan_timestamp": result.get("scan_timestamp") or status.get("completed_at"),
        "radar_as_of": result.get("radar_as_of"),
        "review_scope": scope,
        "include_terminal": include_terminal,
        "active_reviews": active_reviews,
        "terminal_reviews": terminal_reviews,
        "active_candidates_reviewed": len(active_reviews),
        "terminal_candidates_reviewed": len(terminal_reviews),
        "warnings": warnings,
        "read_only": True,
        "orders_submitted": 0,
        "approvals_created": 0,
        "execution_intents_created": 0,
    }


def _render_scan_report(result: dict) -> str:
    lines = [
        "Daily Trader exact scan review",
        f"scan_id={result.get('scan_id') or '-'}",
        f"scan_status={result.get('scan_status') or '-'}",
        f"scan_timestamp={result.get('scan_timestamp') or '-'}",
        f"radar_as_of={result.get('radar_as_of') or '-'}",
        f"review_scope={result.get('review_scope') or '-'}",
        f"active_candidates_reviewed={result.get('active_candidates_reviewed') or 0}",
        f"terminal_candidates_reviewed={result.get('terminal_candidates_reviewed') or 0}",
    ]
    for section in ("active_reviews", "terminal_reviews"):
        for review in result.get(section) or []:
            identity = review.get("candidate_identity") or {}
            candidate = review.get("candidate") or {}
            lines.append(
                "candidate=" + ", ".join([
                    f"id={identity.get('scan_id')}:{identity.get('section')}:{identity.get('symbol')}:{identity.get('signal_ordinal')}",
                    f"symbol={review.get('symbol') or '-'}",
                    f"strategy={review.get('strategy') or '-'}",
                    f"side={review.get('side') or '-'}",
                    f"level_price={candidate.get('level_price') or candidate.get('level') or '-'}",
                    f"lifecycle={review.get('lifecycle_status') or '-'}",
                    f"classification={review.get('review_status') or '-'}",
                    f"warnings={';'.join(review.get('warnings') or []) or '-'}",
                ])
            )
    lines.append("warnings=" + (";".join(result.get("warnings") or []) or "-"))
    lines.append(
        "read_only_safety=" + ", ".join([
            f"read_only={result.get('read_only')}",
            f"orders_submitted={result.get('orders_submitted')}",
            f"approvals_created={result.get('approvals_created')}",
            f"execution_intents_created={result.get('execution_intents_created')}",
        ])
    )
    lines.append("ENTRY_TRIGGERED and READY describe scanner lifecycle states, not broker execution.")
    return "\n".join(lines)


def _render_latest_report(result: dict) -> str:
    lines = [
        "Daily Trader latest scan review",
        f"scan_id={result.get('scan_id') or '-'}",
        f"scan_timestamp={result.get('scan_timestamp') or '-'}",
        f"radar_as_of={result.get('radar_as_of') or '-'}",
        f"symbol_filter={result.get('symbol_filter') or 'ALL'}",
        f"active_candidates_reviewed={result.get('active_candidates_reviewed') or 0}",
        f"terminal_candidates={result.get('terminal_candidates_count') or 0}",
    ]
    for review in result.get("reviews") or []:
        candidate = review.get("candidate") if isinstance(review.get("candidate"), dict) else {}
        identity = review.get("candidate_identity") if isinstance(review.get("candidate_identity"), dict) else {}
        lines.append(
            "candidate="
            + ", ".join(
                [
                    f"id={identity.get('scan_id')}:{identity.get('section')}:{identity.get('symbol')}:{identity.get('signal_ordinal')}",
                    f"symbol={review.get('symbol') or '-'}",
                    f"strategy={review.get('strategy') or '-'}",
                    f"side={review.get('side') or '-'}",
                    f"entry={candidate.get('planned_entry') or candidate.get('planned_entry_price') or '-'}",
                    f"stop={candidate.get('stop') or candidate.get('stop_price') or '-'}",
                    f"target={candidate.get('target') or candidate.get('take_profit_price') or '-'}",
                    f"classification={review.get('review_status') or '-'}",
                    f"warnings={';'.join(review.get('warnings') or []) or '-'}",
                ]
            )
        )
    lines.append(
        "read_only_safety="
        + ", ".join(
            [
                f"read_only={result.get('read_only')}",
                f"orders_submitted={result.get('orders_submitted')}",
                f"approvals_created={result.get('approvals_created')}",
                f"execution_intents_created={result.get('execution_intents_created')}",
            ]
        )
    )
    lines.append("ENTRY_TRIGGERED and READY describe scanner lifecycle states, not broker execution.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Call the read-only Pre-Trade Reviewer loopback API")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("symbol")
    one.add_argument("symbol")
    batch = sub.add_parser("batch")
    batch.add_argument("symbols", nargs="+")
    candidate = sub.add_parser("candidate")
    candidate.add_argument("--scan-id")
    candidate.add_argument("--symbol")
    candidate.add_argument("--section", choices=("active", "terminal"))
    candidate.add_argument("--signal-ordinal", type=int)
    candidate.add_argument("--candidate-ref", help="<full-scan-uuid>:<active|terminal>:<symbol>:<signal-ordinal>")
    latest = sub.add_parser("latest")
    latest.add_argument("--symbol")
    latest.add_argument("--format", choices=("json", "report"), default="report")
    scan = sub.add_parser("scan")
    scan.add_argument("--scan-id", required=True)
    scan.add_argument("--scope", choices=("all_active",), default="all_active")
    scan.add_argument("--include-terminal", action="store_true")
    scan.add_argument("--format", choices=("json", "report"), default="json")
    sub.add_parser("health")
    args = parser.parse_args(argv)
    try:
        if args.command == "symbol":
            source = _request(f"/api/daily-trader/symbol/{quote(str(args.symbol).strip().upper(), safe='')}")
            result = {"symbol": source.get("symbol"), "reviews": [item.to_dict() for item in review_symbol_payload(source)], "read_only": source.get("read_only"), "orders_submitted": source.get("orders_submitted", 0), "approval_created": False}
        elif args.command == "batch":
            sources = [_request(f"/api/daily-trader/symbol/{quote(str(symbol).strip().upper(), safe='')}") for symbol in dict.fromkeys(args.symbols)]
            result = {"requested": len(args.symbols), "reviews": [item.to_dict() for item in review_batch_payload(sources)], "read_only": all(item.get("read_only") is True for item in sources), "orders_submitted": sum(int(item.get("orders_submitted") or 0) for item in sources), "approval_created": False}
        elif args.command == "candidate":
            if args.candidate_ref:
                reference = parse_candidate_reference(args.candidate_ref)
                if any(value is not None for value in (args.scan_id, args.symbol, args.section, args.signal_ordinal)):
                    raise ValueError("Use either --candidate-ref or the full candidate identifier arguments, not both")
                args.scan_id = reference["scan_id"]
                args.symbol = reference["symbol"]
                args.section = reference["section"]
                args.signal_ordinal = reference["signal_ordinal"]
            if any(value is None for value in (args.scan_id, args.symbol, args.section, args.signal_ordinal)):
                raise ValueError("Candidate review requires --candidate-ref or --scan-id, --symbol, --section, and --signal-ordinal")
            result = review_candidate(
                scan_id=args.scan_id,
                symbol=args.symbol,
                section=args.section,
                signal_ordinal=args.signal_ordinal,
            )
        elif args.command == "latest":
            result = review_latest(symbol=args.symbol)
        elif args.command == "scan":
            result = review_scan(
                scan_id=args.scan_id,
                scope=args.scope,
                include_terminal=args.include_terminal,
                read_only=True,
            )
        else:
            result = _request("/api/daily-trader/health")
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.command == "latest" and args.format == "report":
        print(_render_latest_report(result))
    elif args.command == "scan" and args.format == "report":
        print(_render_scan_report(result))
    else:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
