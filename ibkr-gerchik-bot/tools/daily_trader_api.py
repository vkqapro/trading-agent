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
import time
from uuid import UUID
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:8550"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
DEFAULT_SCAN_MAX_WAIT_SECONDS = 240.0
DEFAULT_SCAN_POLL_INTERVAL_SECONDS = 5.0
RENDERED_SYMBOL_PATTERN = re.compile(r"\bsymbol=([A-Za-z0-9][A-Za-z0-9.\-]*)")


def _base_url() -> str:
    value = str(os.environ.get("DAILY_TRADER_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Daily Trader API must use an HTTP loopback URL")
    if parsed.path not in {"", "/"}:
        raise ValueError("DAILY_TRADER_API_BASE_URL must contain only the loopback origin")
    return value


def _request(
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
) -> dict:
    url = f"{_base_url()}{path}"
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except TimeoutError as exc:
        raise RuntimeError("Daily Trader API request timed out before a response was received.") from exc
    except HTTPError as exc:
        raise RuntimeError(f"Daily Trader API returned HTTP {exc.code}. No market report generated.") from exc
    except URLError as exc:
        raise RuntimeError("Daily Trader API unavailable. No market report generated.") from exc
    except OSError as exc:
        raise RuntimeError("Daily Trader API transport failed. No market report generated.") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Daily Trader API returned invalid JSON.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Daily Trader API returned an invalid response object.")
    if result.get("read_only") is not True or result.get("orders_submitted") != 0:
        raise RuntimeError("Daily Trader API safety contract failed; no report generated.")
    return result


def _scan_status_request(scan_id: str) -> dict:
    safe_scan_id = quote(str(scan_id or "").strip(), safe="")
    if not safe_scan_id:
        raise ValueError("scan-status requires --scan-id")
    url = f"{_base_url()}/api/daily-trader/scans/{safe_scan_id}"
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except TimeoutError as exc:
        raise RuntimeError("Daily Trader API scan-status request timed out.") from exc
    except HTTPError as exc:
        if exc.code == 404:
            return {
                "scan_id": str(scan_id),
                "status": "NOT_FOUND",
                "read_only": True,
                "orders_submitted": 0,
                "approvals_created": 0,
                "execution_intents_created": 0,
                "error": {
                    "reason_code": "DAILY_TRADER_SCAN_NOT_FOUND",
                    "message": f"No Daily Trader scan found for {scan_id}",
                },
            }
        raise RuntimeError(f"Daily Trader API returned HTTP {exc.code} for scan-status.") from exc
    except URLError as exc:
        raise RuntimeError("Daily Trader API unavailable. No scan status retrieved.") from exc
    except OSError as exc:
        raise RuntimeError("Daily Trader API transport failed. No scan status retrieved.") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Daily Trader API returned invalid JSON.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Daily Trader API returned an invalid scan status object.")
    if result.get("read_only") is not True or result.get("orders_submitted") != 0:
        raise RuntimeError("Daily Trader API safety contract failed; no scan status returned.")
    return result


def _latest_scan_request() -> dict:
    """Retrieve the newest completed persisted scan through the read-only API."""
    return _request("/api/daily-trader/scans/latest")


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


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _radar(flips: bool, limit: int) -> dict:
    safe_limit = max(1, min(int(limit), 100))
    path = f"/api/radar/flips?limit={safe_limit}" if flips else f"/api/radar?limit={safe_limit}"
    result = _raw_request(path)
    rows = [row for row in (result.get("results") or []) if isinstance(row, dict)]
    radar_as_of = max((str(row.get("as_of") or "") for row in rows), default=None)
    return {
        "mode": "fresh_flips" if flips else "radar",
        "source_table": "radar_snapshots",
        "read_only": True,
        "orders_submitted": 0,
        "count": len(rows),
        "limit": safe_limit,
        "radar_as_of": radar_as_of or None,
        "source_updated_at": result.get("updated_at"),
        "results": rows,
    }


def _setups(side: str | None) -> dict:
    side_filter = str(side or "").strip().upper()
    path = "/api/daily-trader/setups"
    if side_filter:
        path += f"?side={quote(side_filter, safe='')}"
    return _request(path)


def _submit_scan(args: argparse.Namespace) -> dict:
    idempotency_key = str(args.idempotency_key or "").strip() or None
    return _request(
        "/api/daily-trader/scan",
        method="POST",
        timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS,
        payload={
            "mode": args.mode,
            "top_n": args.top_n,
            "include_breakouts": not args.no_breakouts,
            "include_fresh_flips": not args.no_fresh_flips,
            "max_symbols": args.max_symbols,
            "idempotency_key": idempotency_key,
            "trigger_source": args.trigger_source,
        },
    )


def _scan_status(scan_id: str) -> dict:
    return _scan_status_request(scan_id)


def _poll_scan(initial: dict, *, max_wait: float, poll_interval: float) -> dict:
    scan_id = str(initial.get("scan_id") or "").strip()
    if not scan_id:
        raise RuntimeError("Daily Trader API accepted a scan without returning scan_id.")
    status = str(initial.get("status") or "").upper()
    if status in {"COMPLETED", "FAILED"}:
        return initial
    deadline = time.monotonic() + max(0.0, max_wait)
    current = initial
    while time.monotonic() < deadline:
        time.sleep(max(0.2, poll_interval))
        current = _scan_status(scan_id)
        status = str(current.get("status") or "").upper()
        if status in {"COMPLETED", "FAILED"}:
            return current
    current = _scan_status(scan_id)
    status = str(current.get("status") or "").upper()
    if status in {"COMPLETED", "FAILED"}:
        return current
    current["polling_exhausted"] = True
    current["message"] = (
        "Scan is still running; do not launch a duplicate scan. "
        f"Recover it later with: python tools/daily_trader_api.py scan-status --scan-id {scan_id}"
    )
    return current


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


def _direction_conflict_warning(scanner: dict, radar: dict) -> str | None:
    if scanner.get("direction_conflict") is not True:
        return None
    radar_trend = _value(radar.get("trend"))
    scanner_side = _value(scanner.get("side"))
    return f"WARNING: Radar {radar_trend} / Scanner {scanner_side} - direction conflict (informational only)"


def _setup_line(item: dict, *, signal_ordinal: int | None = None) -> str:
    symbol = _setup_symbol(item)
    scanner = item.get("scanner")
    scanner = scanner if isinstance(scanner, dict) else {}
    radar = item.get("radar")
    radar = radar if isinstance(radar, dict) else {}
    lifecycle_status = scanner.get("lifecycle_status")
    scanner_status = scanner.get("scanner_status")
    scanner_trade_status = scanner.get("status")
    lifecycle_value = str(lifecycle_status or "").strip().upper()
    fields = [
        f"symbol={symbol}",
        f"strategy={_value(scanner.get('strategy'))}",
        f"side={_value(scanner.get('side'))}",
    ]
    if signal_ordinal is not None:
        fields.append(f"signal_ordinal={signal_ordinal}")
    fields.extend(
        [
            f"level_price={_value(scanner.get('level_price', scanner.get('level')))}",
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
    if lifecycle_value == "INVALIDATED":
        fields.append(f"invalidation_reason={_value(scanner.get('lifecycle_reason') or 'not_provided')}")
    warning = _direction_conflict_warning(scanner, radar)
    if warning:
        fields.append(f"warning={warning}")
    return " | ".join(fields)


def _candidate_reference(scan_id: object, section: str, symbol: str, signal_ordinal: int) -> str | None:
    try:
        normalized_scan_id = str(UUID(str(scan_id or "").strip()))
    except (ValueError, TypeError):
        return None
    return f"{normalized_scan_id}:{section}:{symbol}:{signal_ordinal}"


def _sanitize_reason_code(value: object, default: str = "DAILY_TRADER_SCAN_FAILED") -> str:
    text = str(value or default).strip().upper()
    text = re.sub(r"[^A-Z0-9_]+", "_", text).strip("_")
    return text or default


def _sanitize_error_detail(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text[:500] if text else "-"


def _historical_error_details(artifact: dict) -> str:
    errors = artifact.get("errors")
    if not isinstance(errors, list):
        return "-"
    rendered: list[str] = []
    for item in errors:
        if isinstance(item, dict):
            symbol = _sanitize_error_detail(item.get("symbol"))
            reason = _sanitize_error_detail(
                item.get("reason") or item.get("reason_code") or item.get("message") or item.get("detail")
            )
            if symbol == "-" and reason == "-":
                continue
            rendered.append(f"{symbol}:{reason}" if symbol != "-" else reason)
        elif item not in (None, ""):
            rendered.append(_sanitize_error_detail(item))
    return ";".join(rendered) if rendered else "-"


def _source_setup_symbols(result: dict) -> list[str]:
    symbols: list[str] = []
    for section in ("active", "terminal"):
        for item in result.get(section) or []:
            if isinstance(item, dict):
                symbols.append(_setup_symbol(item))
    return symbols


def _source_radar_symbols(result: dict) -> list[str]:
    symbols: list[str] = []
    for item in result.get("results") or []:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise RuntimeError("Daily Trader API returned a Radar row without an authoritative symbol.")
        symbols.append(symbol.strip())
    return symbols


def _validate_rendered_symbols(rendered: str, source_symbols: list[str]) -> None:
    rendered_symbols = RENDERED_SYMBOL_PATTERN.findall(rendered)
    if rendered_symbols != source_symbols:
        raise RuntimeError(
            "Rendered ticker integrity check failed; no report generated. "
            f"source={source_symbols!r} rendered={rendered_symbols!r}"
        )


def _validate_rendered_symbol_set(rendered: str, source_symbols: list[str]) -> None:
    rendered_symbols = RENDERED_SYMBOL_PATTERN.findall(rendered)
    if sorted(rendered_symbols) != sorted(source_symbols):
        raise RuntimeError(
            "Rendered ticker integrity check failed; no report generated. "
            f"source={source_symbols!r} rendered={rendered_symbols!r}"
        )


def _render_setups_report(result: dict) -> str:
    source_symbols = _source_setup_symbols(result)
    generated_at = result.get("generated_at") or result.get("scan_timestamp")
    scan_id = str(result.get("scan_id") or "").strip()
    lines = [
        "Daily Trader persisted setups",
        f"mode={_value(result.get('mode'))}",
        f"side={_value(result.get('side'))}",
        f"radar_as_of={_value(result.get('radar_as_of'))}",
        f"generated_at={_value(generated_at)}",
        f"read_only={_value(result.get('read_only'))}",
        f"orders_submitted={_value(result.get('orders_submitted'))}",
        "symbol_source=authoritative JSON fields only",
        f"ticker_integrity_source_count={len(source_symbols)}",
    ]
    if _candidate_reference(scan_id, "active", "PLACEHOLDER", 1) is None:
        lines.append("candidate_reference_note=copyable candidate IDs require a completed scan UUID; persisted_setups has no reviewable scan job")
    summary = result.get("summary")
    if isinstance(summary, dict):
        lines.append(
            "summary="
            + ", ".join(
                [
                    f"active_candidates={_value(summary.get('active_candidates'))}",
                    f"entry_triggered={_value(summary.get('entry_triggered'))}",
                    f"pending_entry={_value(summary.get('pending_entry'))}",
                    f"invalidated={_value(summary.get('invalidated'))}",
                    f"expired={_value(summary.get('expired'))}",
                    f"ambiguous={_value(summary.get('ambiguous'))}",
                    f"no_setup={_value(summary.get('no_setup'))}",
                    f"errors={_value(summary.get('errors'))}",
                ]
            )
        )
    lines.append("active:")
    active = [item for item in (result.get("active") or []) if isinstance(item, dict)]
    if active:
        lines.append("lifecycle_note=ENTRY_TRIGGERED and READY describe scanner lifecycle states, not broker execution. This report does not submit orders or create approvals.")
        lines.extend(
            _setup_line(item, signal_ordinal=index)
            + f" | candidate_id={_candidate_reference(scan_id, 'active', _setup_symbol(item), index) or 'unavailable_persisted_setups'}"
            for index, item in enumerate(active, start=1)
        )
    else:
        lines.append("-")
    lines.append("terminal:")
    terminal = [item for item in (result.get("terminal") or []) if isinstance(item, dict)]
    if terminal:
        lines.extend(
            _setup_line(item, signal_ordinal=index)
            + f" | candidate_id={_candidate_reference(scan_id, 'terminal', _setup_symbol(item), index) or 'unavailable_persisted_setups'}"
            for index, item in enumerate(terminal, start=1)
        )
    else:
        lines.append("-")
    lines.append("ticker_integrity=validated_against_source_json")
    rendered = "\n".join(lines)
    _validate_rendered_symbols(rendered, source_symbols)
    return rendered


def _render_scan_report(status: dict) -> str:
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    scanner = result.get("scanner") if isinstance(result.get("scanner"), dict) else {}
    scan_id = str(status.get("scan_id") or "").strip()
    scan_id_display = scan_id or "not_available"
    lines = [
        "Daily Trader scan",
        f"scan_id={scan_id_display}",
        f"scan_status_command=/scan-status scan_id={scan_id}" if scan_id else "scan_status_command=not_available",
        f"status={_value(status.get('status'))}",
        f"mode={_value(status.get('mode'))}",
        f"top_n={_value(status.get('top_n'))}",
        f"max_symbols={_value(status.get('max_symbols'))}",
        f"radar_as_of={_value(status.get('radar_as_of') or result.get('radar_as_of'))}",
        f"started_at={_value(status.get('started_at'))}",
        f"completed_at={_value(status.get('completed_at'))}",
        f"symbols_processed={_value(status.get('processed_count'))}",
        f"persisted_results={_value(status.get('persisted_result_count'))}",
        f"read_only={_value(status.get('read_only'))}",
        f"orders_submitted={_value(status.get('orders_submitted'))}",
        f"approvals_created={_value(status.get('approvals_created'))}",
        f"execution_intents_created={_value(status.get('execution_intents_created'))}",
    ]
    if status.get("polling_exhausted"):
        lines.append(
            "next_step="
            f"recover later with /scan-status scan_id={scan_id_display} "
            f"(CLI: python tools/daily_trader_api.py scan-status --scan-id {scan_id_display}); "
            "do not launch a duplicate scan"
        )
    normalized_status = str(status.get("status") or "").upper()
    if normalized_status in {"FAILED", "NOT_FOUND"}:
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        lines.append(f"reason_code={_sanitize_reason_code(error.get('reason_code'), default='DAILY_TRADER_SCAN_NOT_FOUND' if normalized_status == 'NOT_FOUND' else 'DAILY_TRADER_SCAN_FAILED')}")
        lines.append(f"error_type={_sanitize_error_detail(error.get('type'))}")
        lines.append(f"error_details={_sanitize_error_detail(error.get('message') or error.get('detail'))}")
        return "\n".join(lines)
    if normalized_status != "COMPLETED":
        lines.append("result=not available yet")
        return "\n".join(lines)

    lines.append(
        "scanner="
        + ", ".join(
            [
                f"requested={_value(scanner.get('requested'))}",
                f"symbols_processed={_value(scanner.get('completed'))}",
                f"symbols_failed={_value(scanner.get('failed'))}",
            ]
        )
    )
    lines.append(
        "summary="
        + ", ".join(
            [
                f"active_signal_candidates={_value(summary.get('active_candidates'))}",
                f"entry_triggered_signals={_value(summary.get('entry_triggered'))}",
                f"pending_entry_signals={_value(summary.get('pending_entry'))}",
                f"invalidated_signals={_value(summary.get('invalidated'))}",
                f"expired_signals={_value(summary.get('expired'))}",
                f"ambiguous_signals={_value(summary.get('ambiguous'))}",
                f"symbols_without_setups={_value(summary.get('no_setup'))}",
                f"scanner_errors={_value(summary.get('errors'))}",
            ]
        )
    )
    active = [item for item in (result.get("active") or []) if isinstance(item, dict)]
    terminal = [item for item in (result.get("terminal") or []) if isinstance(item, dict)]
    no_setup = [item for item in (result.get("no_setup") or []) if isinstance(item, dict)]
    errors = [item for item in (result.get("errors") or []) if isinstance(item, dict)]
    source_symbols = [_setup_symbol(item) for section in (active, terminal, no_setup, errors) for item in section]
    lines.append("active:")
    if active:
        lines.append("lifecycle_note=ENTRY_TRIGGERED and READY describe scanner lifecycle states, not broker execution. This report does not submit orders or create approvals.")
        lines.extend(
            _setup_line(item, signal_ordinal=index)
            + f" | candidate_id={_candidate_reference(scan_id, 'active', _setup_symbol(item), index)}"
            for index, item in enumerate(active, start=1)
        )
    else:
        lines.append("-")
    lines.append("terminal:")
    if terminal:
        lines.extend(
            _setup_line(item, signal_ordinal=index)
            + f" | candidate_id={_candidate_reference(scan_id, 'terminal', _setup_symbol(item), index)}"
            for index, item in enumerate(terminal, start=1)
        )
    else:
        lines.append("-")
    lines.append("no_setup:")
    if no_setup:
        for item in no_setup:
            scanner = item.get("scanner") if isinstance(item.get("scanner"), dict) else {}
            status_value = item.get("status") or item.get("scanner_status") or scanner.get("scanner_status") or "NO_SETUP"
            lines.append(f"symbol={_setup_symbol(item)} | scanner_status={_value(status_value)}")
    else:
        lines.append("-")
    lines.append("errors:")
    if errors:
        for item in errors:
            scanner = item.get("scanner") if isinstance(item.get("scanner"), dict) else {}
            status_value = item.get("status") or item.get("scanner_status") or scanner.get("scanner_status") or "ERROR"
            lines.append(
                f"symbol={_setup_symbol(item)} | scanner_status={_value(status_value)} | error={_value(item.get('error'))}"
            )
    else:
        lines.append("-")
    lines.append("candidate_note=CANDIDATE means scanner candidate only; it is not trade-ready approval or execution.")
    lines.append("ticker_integrity=validated_against_source_json")
    rendered = "\n".join(lines)
    _validate_rendered_symbols(rendered, source_symbols)
    return rendered


def _trend_group_sort_key(trend: str) -> tuple[int, str]:
    order = {"GREEN": 0, "GREY": 1, "RED": 2}
    normalized = str(trend or "").strip().upper()
    return (order.get(normalized, 99), normalized)


def _render_flip_line(item: dict) -> str:
    symbol = item.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise RuntimeError("Daily Trader API returned a flip row without an authoritative symbol.")
    current_trend = item.get("trend")
    return " | ".join(
        [
            f"symbol={symbol.strip()}",
            f"current_trend={_value(current_trend)}",
            f"previous_trend={_value(item.get('trend_flip_from'))}",
            f"flip_to={_value(item.get('trend_flip_to'))}",
            f"flip_date={_value(item.get('trend_flip_date'))}",
            f"radar_as_of={_value(item.get('as_of'))}",
            f"radar_score={_value(item.get('radar_score'))}",
        ]
    )


def _render_flips_report(result: dict) -> str:
    source_symbols = _source_radar_symbols(result)
    rows = [item for item in (result.get("results") or []) if isinstance(item, dict)]
    groups: dict[str, list[dict]] = {}
    for item in rows:
        trend = str(item.get("trend") or "UNKNOWN").strip().upper() or "UNKNOWN"
        groups.setdefault(trend, []).append(item)
    rendered_count = sum(len(items) for items in groups.values())
    lines = [
        "Daily Trader fresh Radar flips",
        f"mode={_value(result.get('mode'))}",
        f"source_table={_value(result.get('source_table') or 'radar_snapshots')}",
        f"radar_as_of={_value(result.get('radar_as_of'))}",
        f"source_updated_at={_value(result.get('source_updated_at'))}",
        f"limit={_value(result.get('limit'))}",
        f"rendered_rows={rendered_count}",
        f"returned_rows={_value(result.get('count'))}",
        f"read_only={_value(result.get('read_only'))}",
        f"orders_submitted={_value(result.get('orders_submitted'))}",
        "symbol_source=authoritative JSON fields only",
        f"ticker_integrity_source_count={len(source_symbols)}",
    ]
    for trend in sorted(groups, key=_trend_group_sort_key):
        items = groups[trend]
        lines.append(f"{trend} ({len(items)}):")
        lines.extend(_render_flip_line(item) for item in items)
    if not groups:
        lines.append("flips:")
        lines.append("-")
    if rendered_count != len(source_symbols) or rendered_count != result.get("count"):
        raise RuntimeError(
            "Rendered flip count integrity check failed; no report generated. "
            f"source={len(source_symbols)!r} rendered={rendered_count!r} count={result.get('count')!r}"
        )
    lines.append("ticker_integrity=validated_against_source_json")
    rendered = "\n".join(lines)
    _validate_rendered_symbol_set(rendered, source_symbols)
    return rendered


def _render_market_report(result: dict) -> str:
    radar = result.get("radar") if isinstance(result.get("radar"), dict) else {}
    scanner = result.get("production_scanner") if isinstance(result.get("production_scanner"), dict) else {}
    persisted = (
        result.get("persisted_scanner_results")
        if isinstance(result.get("persisted_scanner_results"), dict)
        else {}
    )
    radar_setups = result.get("radar_setups") if isinstance(result.get("radar_setups"), dict) else {}
    screener = result.get("market_screener") if isinstance(result.get("market_screener"), dict) else {}
    latest_artifact = scanner.get("latest_scan") if isinstance(scanner.get("latest_scan"), dict) else {}
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    lines = [
        "Daily Trader market health",
        f"status={_value(result.get('status'))}",
        f"generated_at={_value(result.get('generated_at'))}",
        f"market_session={_value(result.get('market_session'))}",
        f"dashboard_api={_value((result.get('dashboard_api') or {}).get('status') if isinstance(result.get('dashboard_api'), dict) else None)}",
        f"radar_as_of={_value(radar.get('as_of'))}",
        f"radar_current_rows={_value(radar.get('current_row_count'))}",
        "current_daily_trader_scanner_results="
        + ", ".join(
            [
                "source_table=radar_scanner_results",
                f"snapshot_as_of={_value(persisted.get('radar_as_of'))}",
                f"result_count={_value(persisted.get('current_result_count'))}",
            ]
        ),
        f"production_scanner_available={_value(scanner.get('scanner_available'))}",
        f"production_scanner_status={_value(scanner.get('status'))}",
        "historical_production_scanner_artifact="
        + ", ".join(
            [
                f"run_id={_value(latest_artifact.get('run_id'))}",
                f"completed_at={_value(latest_artifact.get('last_scan_at') or scanner.get('last_scan_at'))}",
                f"status={_value(latest_artifact.get('last_scan_status') or scanner.get('last_scan_status'))}",
                f"symbols_scanned={_value(latest_artifact.get('symbols_scanned'))}",
                f"symbols_requested={_value(latest_artifact.get('symbols_requested'))}",
                f"symbols_skipped={_value(latest_artifact.get('symbols_skipped'))}",
                f"errors={_value(latest_artifact.get('error_count'))}",
                f"error_details={_historical_error_details(latest_artifact)}",
            ]
        ),
        f"persisted_scanner_results_available={_value(persisted.get('available'))}",
        f"persisted_scanner_results_count={_value(persisted.get('current_result_count'))}",
        f"radar_setups_valid={_value(radar_setups.get('valid'))}",
        f"radar_setups_eligible_count={_value(radar_setups.get('eligible_count'))}",
        f"market_screener_status={_value(screener.get('status'))}",
        f"market_screener_last_scan_status={_value(screener.get('last_scan_status'))}",
        f"warnings={','.join(str(item) for item in warnings) if warnings else '-'}",
        f"read_only={_value(result.get('read_only'))}",
        f"orders_submitted={_value(result.get('orders_submitted'))}",
        f"approvals_created={_value(result.get('approvals_created'))}",
        f"execution_intents_created={_value(result.get('execution_intents_created'))}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Call the read-only Daily Trader loopback API")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan")
    scan.add_argument("--mode", choices=("scheduled", "top", "breakouts", "fresh_flips"), default="scheduled")
    scan.add_argument("--top-n", type=int, default=10)
    scan.add_argument("--max-symbols", type=int, default=30)
    scan.add_argument("--no-breakouts", action="store_true")
    scan.add_argument("--no-fresh-flips", action="store_true")
    scan.add_argument("--idempotency-key", default=None)
    scan.add_argument("--trigger-source", choices=("manual", "scheduled"), default="manual")
    scan.add_argument(
        "--max-wait-seconds",
        type=float,
        default=_float_env("DAILY_TRADER_SCAN_MAX_WAIT_SECONDS", DEFAULT_SCAN_MAX_WAIT_SECONDS),
    )
    scan.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=_float_env("DAILY_TRADER_SCAN_POLL_INTERVAL_SECONDS", DEFAULT_SCAN_POLL_INTERVAL_SECONDS),
    )
    scan.add_argument("--format", choices=("json", "report"), default="report")

    scan_status = subparsers.add_parser("scan-status")
    scan_status.add_argument("--scan-id", required=True)
    scan_status.add_argument("--format", choices=("json", "report"), default="report")

    latest = subparsers.add_parser("latest", help="retrieve the latest completed persisted scan")
    latest.add_argument("--format", choices=("json", "report"), default="report")

    symbol = subparsers.add_parser("symbol")
    symbol.add_argument("symbol")
    radar = subparsers.add_parser("radar")
    radar.add_argument("--flips", action="store_true")
    radar.add_argument("--limit", type=int, default=20)
    radar.add_argument(
        "--format",
        choices=("json", "report"),
        default="json",
        help="Use deterministic report formatting for ticker-bearing Radar output.",
    )
    setups = subparsers.add_parser("setups")
    setups.add_argument("--side", choices=("LONG", "SHORT"), default=None)
    setups.add_argument(
        "--format",
        choices=("json", "report"),
        default="json",
        help="Use deterministic report formatting for ticker-bearing Daily Trader output.",
    )
    market = subparsers.add_parser("market")
    market.add_argument(
        "--format",
        choices=("json", "report"),
        default="json",
        help="Use deterministic report formatting for platform-health output.",
    )
    subparsers.add_parser("health")
    args = parser.parse_args(argv)

    try:
        if args.command == "scan":
            accepted = _submit_scan(args)
            result = _poll_scan(
                accepted,
                max_wait=args.max_wait_seconds,
                poll_interval=args.poll_interval_seconds,
            )
        elif args.command == "scan-status":
            result = _scan_status(args.scan_id)
        elif args.command == "latest":
            result = _latest_scan_request()
        elif args.command == "symbol":
            normalized = str(args.symbol or "").strip().upper()
            if not normalized or normalized == "ALL" or not normalized.replace(".", "").replace("-", "").isalnum():
                raise ValueError("A single stock symbol is required")
            result = _request(f"/api/daily-trader/symbol/{quote(normalized, safe='')}")
        elif args.command == "radar":
            result = _radar(args.flips, args.limit)
        elif args.command == "setups":
            result = _setups(args.side)
        elif args.command == "market":
            result = _request("/api/daily-trader/market")
        else:
            result = _request("/api/daily-trader/health")
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.command in {"scan", "scan-status", "latest"} and args.format == "report":
        print(_render_scan_report(result))
    elif args.command == "radar" and args.flips and args.format == "report":
        print(_render_flips_report(result))
    elif args.command == "setups" and args.format == "report":
        print(_render_setups_report(result))
    elif args.command == "market" and args.format == "report":
        print(_render_market_report(result))
    else:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
