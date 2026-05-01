"""End-of-day reporting with explainable per-ticker decisions."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from src.alerts.slack import SlackAlerter
from src.config import SETTINGS, append_markdown_log
from src.risk.risk_manager import RiskManager
from src.workflow_log import append_workflow_snapshot


DAILY_DECISIONS_PATH = Path(__file__).resolve().parents[2] / "memory" / "daily_decisions.json"


def _load_daily_decisions() -> Dict[str, object]:
    today = datetime.now().date().isoformat()
    default_payload: Dict[str, object] = {"date": today, "decisions": {}}
    if not DAILY_DECISIONS_PATH.exists():
        return default_payload
    try:
        with DAILY_DECISIONS_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return default_payload
    if str(payload.get("date")) != today:
        return default_payload
    return payload if isinstance(payload, dict) else default_payload


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


def run_eod(
    risk_manager: RiskManager,
    account_snapshot: Dict[str, object],
    open_positions: List[Dict[str, object]],
    daily_pnl: float,
    alerter: SlackAlerter,
) -> Dict[str, object]:
    """Persist end-of-day account, positions, and explainable decision summary."""
    risk_manager.update_daily_pnl(daily_pnl)
    daily_decisions = _load_daily_decisions()
    decision_summary = _build_summary(
        daily_decisions.get("decisions", {}) if isinstance(daily_decisions.get("decisions"), dict) else {},
        open_positions,
    )

    report_text = "\n".join(decision_summary["ticker_sections"]) if decision_summary["ticker_sections"] else "No ticker decisions recorded today."
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

