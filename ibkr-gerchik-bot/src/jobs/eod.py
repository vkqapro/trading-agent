"""End-of-day reporting with explainable per-ticker decisions."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.alerts.slack import SlackAlerter
from src.config import SETTINGS, append_markdown_log
from src.risk.risk_manager import RiskManager
from src.strategy import decision_log
from src.workflow_log import append_workflow_snapshot, read_latest_workflow_snapshot


RUNTIME_LOG_PATH = Path(__file__).resolve().parents[2] / "memory" / "runtime" / "application.log"


def _payload_matches_today(payload: Dict[str, object]) -> bool:
    today = datetime.now().date().isoformat()
    timestamp = str(payload.get("timestamp", "")).strip()
    if timestamp.startswith(today):
        return True
    scans = payload.get("scans", [])
    if isinstance(scans, list):
        for scan in scans:
            if isinstance(scan, dict) and str(scan.get("timestamp", "")).startswith(today):
                return True
    return False


def _load_today_workflow_snapshot(stage: str) -> Optional[Dict[str, object]]:
    payload = read_latest_workflow_snapshot(SETTINGS.paths.research_log, stage)
    if not isinstance(payload, dict):
        return None
    return payload if _payload_matches_today(payload) else None


def _normalize_reasons(reason: object) -> List[str]:
    if isinstance(reason, list):
        return [str(item) for item in reason if str(item).strip()]
    if reason is None:
        return []
    text = str(reason).strip()
    return [text] if text else []


def _load_today_job_blockers() -> List[str]:
    if not RUNTIME_LOG_PATH.exists():
        return []
    today = datetime.now().date().isoformat()
    blockers: List[str] = []
    try:
        lines = RUNTIME_LOG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return blockers

    for line in lines:
        if today not in line or "session_already_running" not in line:
            continue
        if "'job': 'open'" in line:
            blockers.append("open blocked by an active session lock")
        elif "'job': 'intraday'" in line:
            blockers.append("intraday blocked by an active session lock")
    return blockers


def _format_reason_lines(reasons: List[str]) -> List[str]:
    return [f"- {reason}" for reason in reasons] if reasons else ["- no recorded reasons"]


def _build_ticker_section(symbol: str, decision: Dict[str, object], open_symbols: set[str]) -> str:
    context = decision.get("context", {}) if isinstance(decision.get("context"), dict) else {}
    reasons = decision.get("reason", [])
    reasons_list = reasons if isinstance(reasons, list) else [str(reasons)]
    signal = str(decision.get("signal", "NONE"))
    pattern = str(context.get("pattern", "NONE"))
    level = decision.get("level")
    zone = context.get("zone", ["-", "-"])
    trade_taken = symbol in open_symbols and signal in {"BUY", "SELL"}

    lines = [
        "-" * 50,
        f"Ticker: {symbol}",
        f"Level: {level if level is not None else '-'}",
        f"Pattern: {pattern}",
        "",
        "Context:",
        f"- Trend: {context.get('trend', 'RANGE')}",
        f"- ATR: {context.get('atr_status', 'LOW')}",
        f"- News: {context.get('news_risk', 'LOW')}",
        f"- Zone: {zone[0]} - {zone[1]}" if isinstance(zone, list) and len(zone) == 2 else "- Zone: -",
        "",
        "Decision:",
        "TRADE TAKEN" if trade_taken else ("VALID SETUP" if signal in {"BUY", "SELL"} else "NO TRADE"),
    ]

    if signal in {"BUY", "SELL"}:
        entry = decision.get("entry")
        stop = decision.get("stop")
        target = decision.get("target")
        rr = 0.0
        if entry is not None and stop is not None and target is not None:
            risk = abs(float(entry) - float(stop))
            reward = abs(float(target) - float(entry))
            rr = 0.0 if risk <= 0 else reward / risk
        lines.extend(
            [
                f"Trade: {signal}",
                f"Entry: {entry}",
                f"Stop: {stop}",
                f"Target: {target}",
                f"R:R: {round(rr, 2)}",
                f"Confidence: {decision.get('confidence', 0.0)}",
                f"Position Modifier: {decision.get('position_modifier', 1.0)}",
            ]
        )

    lines.extend(["", "Reasons:", *_format_reason_lines(reasons_list), ""])
    return "\n".join(lines)


def _build_summary(decisions: Dict[str, object], open_positions: List[Dict[str, object]]) -> Dict[str, object]:
    open_symbols = {
        str(position.get("symbol", "")).strip().upper()
        for position in open_positions
        if str(position.get("symbol", "")).strip()
    }
    decision_items = []
    rejection_counter: Counter[str] = Counter()
    valid_setups = 0
    trades_taken = 0

    for symbol, payload in sorted(decisions.items()):
        if not isinstance(payload, dict):
            continue
        best = payload.get("best_decision")
        if not isinstance(best, dict):
            continue
        decision_items.append((symbol, best))
        signal = str(best.get("signal", "NONE"))
        reasons = best.get("reason", [])
        reason_list = reasons if isinstance(reasons, list) else [str(reasons)]
        if signal in {"BUY", "SELL"}:
            valid_setups += 1
            if symbol in open_symbols:
                trades_taken += 1
        else:
            rejection_counter.update(reason_list)

    summary = {
        "total_tickers_scanned": len(decision_items),
        "valid_setups": valid_setups,
        "trades_taken": trades_taken,
        "skipped": max(len(decision_items) - trades_taken, 0),
        "top_rejection_reasons": rejection_counter.most_common(5),
        "open_symbols": sorted(open_symbols),
        "ticker_sections": [_build_ticker_section(symbol, decision, open_symbols) for symbol, decision in decision_items],
    }
    return summary


def _build_workflow_fallback_summary(open_positions: List[Dict[str, object]]) -> Optional[Dict[str, object]]:
    open_snapshot = _load_today_workflow_snapshot("Open")
    intraday_snapshot = _load_today_workflow_snapshot("Intraday")
    job_blockers = _load_today_job_blockers()

    scans: List[Dict[str, object]] = []
    executed: List[Dict[str, object]] = []
    skipped: List[Dict[str, object]] = []

    for payload in [open_snapshot, intraday_snapshot]:
        if not isinstance(payload, dict):
            continue
        payload_scans = payload.get("scans", [])
        payload_executed = payload.get("executed", [])
        payload_skipped = payload.get("skipped", [])
        if isinstance(payload_scans, list):
            scans.extend(item for item in payload_scans if isinstance(item, dict))
        if isinstance(payload_executed, list):
            executed.extend(item for item in payload_executed if isinstance(item, dict))
        if isinstance(payload_skipped, list):
            skipped.extend(item for item in payload_skipped if isinstance(item, dict))

    if not scans and not executed and not skipped and not job_blockers:
        return None

    skip_reasons: Counter[str] = Counter()
    for item in skipped:
        skip_reasons.update(_normalize_reasons(item.get("reason")))

    scan_iterations = len(scans)
    scanned_estimate = max((int(scan.get("symbols_scanned", 0) or 0) for scan in scans), default=0)
    raw_signals = sum(int(scan.get("signals_detected", 0) or 0) for scan in scans)
    status_notes = [
        "Daily decision log was empty, so this summary used workflow snapshots."
    ]
    if scan_iterations:
        status_notes.append(
            f"Observed {scan_iterations} scan iterations, up to {scanned_estimate} tickers per pass, and {raw_signals} raw signals."
        )
    if job_blockers:
        status_notes.append(f"Session jobs did not run normally: {', '.join(job_blockers)}.")

    return {
        "total_tickers_scanned": scanned_estimate,
        "valid_setups": len(executed),
        "trades_taken": len(executed),
        "skipped": len(skipped),
        "top_rejection_reasons": skip_reasons.most_common(5),
        "open_symbols": sorted(
            {
                str(position.get("symbol", "")).strip().upper()
                for position in open_positions
                if str(position.get("symbol", "")).strip()
            }
        ),
        "ticker_sections": [],
        "source": "workflow_fallback",
        "status_notes": status_notes,
        "raw_signals_detected": raw_signals,
    }


def run_eod(
    risk_manager: RiskManager,
    account_snapshot: Dict[str, object],
    open_positions: List[Dict[str, object]],
    daily_pnl: float,
    alerter: SlackAlerter,
) -> Dict[str, object]:
    """Persist end-of-day account, positions, and explainable decision summary."""
    risk_manager.update_daily_pnl(daily_pnl)
    daily_decisions = decision_log.load()
    decision_summary = _build_summary(
        daily_decisions.get("decisions", {}) if isinstance(daily_decisions.get("decisions"), dict) else {},
        open_positions,
    )
    if decision_summary["total_tickers_scanned"] == 0:
        workflow_fallback = _build_workflow_fallback_summary(open_positions)
        if workflow_fallback is not None:
            decision_summary = workflow_fallback

    if decision_summary["ticker_sections"]:
        report_text = "\n".join(decision_summary["ticker_sections"])
    else:
        notes = decision_summary.get("status_notes", [])
        if isinstance(notes, list) and notes:
            report_text = "\n".join(str(note) for note in notes)
        else:
            report_text = "No ticker decisions recorded today."
    summary = {
        "date": datetime.now().date().isoformat(),
        "daily_pnl": round(daily_pnl, 2),
        "daily_pnl_pct": round((daily_pnl / max(risk_manager.account_equity, 1.0)) * 100.0, 2),
        "positions": open_positions,
        "account_snapshot": account_snapshot,
        "blocked": risk_manager.get_state()["blocked"],
        "reasons": risk_manager.get_state()["reasons"],
        "decision_summary": decision_summary,
        "report_text": report_text,
    }

    append_markdown_log(
        SETTINGS.paths.trade_log,
        "End Of Day",
        {
            "summary": {
                "date": summary["date"],
                "daily_pnl": summary["daily_pnl"],
                "daily_pnl_pct": summary["daily_pnl_pct"],
                "total_tickers_scanned": decision_summary["total_tickers_scanned"],
                "valid_setups": decision_summary["valid_setups"],
                "trades_taken": decision_summary["trades_taken"],
                "skipped": decision_summary["skipped"],
                "top_rejection_reasons": decision_summary["top_rejection_reasons"],
            },
            "report": f"\n{report_text}",
        },
    )
    append_workflow_snapshot(SETTINGS.paths.trade_log, "EOD", {"summary": summary})
    alerter.send_daily_summary(summary)
    return summary
