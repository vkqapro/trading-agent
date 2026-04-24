"""Application entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.alerts.slack import SlackAlerter
from src.brokers.ibkr import IBKRClient
from src.config import LOGGER, SETTINGS, append_markdown_log, ensure_directories
from src.data.market_data import MarketDataService
from src.data.news import NewsService
from src.data.news_filter import NewsRiskFilter
from src.execution.order_manager import OrderManager
from src.jobs.eod import run_eod
from src.jobs.intraday import run_intraday
from src.jobs.open import run_open
from src.jobs.premarket import run_premarket
from src.jobs.weekly import run_weekly
from src.risk.kill_switch import should_trigger_kill_switch
from src.risk.risk_manager import RiskManager


def _account_equity_from_summary(summary: List[Dict[str, object]]) -> float:
    for item in summary:
        if item.get("tag") == "NetLiquidation":
            return float(item.get("value", 0.0))
    return 100_000.0


def _load_state(state_path: Path) -> Dict[str, object]:
    if not state_path.exists():
        return {"watchlist": {}, "tracked_positions": [], "weekly_results": []}
    with state_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_state(state_path: Path, payload: Dict[str, object]) -> None:
    with state_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def run_job(job_name: str, dry_run_override: Optional[bool] = None) -> Dict[str, object]:
    ensure_directories()
    state_path = SETTINGS.paths.state_file
    state = _load_state(state_path)
    alerter = SlackAlerter()
    news_service = NewsService()
    news_filter = NewsRiskFilter(news_service)
    dry_run = SETTINGS.dry_run_mode if dry_run_override is None else dry_run_override

    if job_name == "weekly":
        metrics = run_weekly(state.get("weekly_results", []))
        return {"job": job_name, "metrics": metrics, "dry_run": dry_run}

    broker = IBKRClient()
    try:
        broker.connect()
        market_data = MarketDataService(broker)
        order_manager = OrderManager(broker, alerter, news_filter, dry_run=dry_run)
        account_summary = broker.get_account_summary()
        account_equity = _account_equity_from_summary(account_summary)
        positions = broker.get_positions()
        risk_manager = RiskManager(account_equity)

        internal_positions = state.get("tracked_positions", [])
        kill_switch, reasons = should_trigger_kill_switch(
            account_equity=account_equity,
            daily_realized_pnl=0.0,
            connection_healthy=broker.is_connected,
            broker_positions=positions,
            internal_positions=internal_positions,
            macro_risk=news_filter.is_macro_risk(),
        )
        if kill_switch and job_name in {"open", "intraday"}:
            alerter.send_error(f"Kill switch engaged: {', '.join(reasons)}")
            return {"job": job_name, "blocked": True, "reasons": reasons, "dry_run": dry_run}

        if job_name == "premarket":
            watchlist = run_premarket(market_data, news_service, news_filter)
            state["watchlist"] = watchlist
            _save_state(state_path, state)
            return {"job": job_name, "watchlist_count": len(watchlist), "dry_run": dry_run}

        if job_name == "open":
            tracked_positions = state.get("tracked_positions", [])
            risk_manager.update_open_risk(tracked_positions)
            executed = run_open(
                market_data=market_data,
                order_manager=order_manager,
                news_filter=news_filter,
                watchlist=state.get("watchlist", {}),
                account_equity=account_equity,
                current_positions=positions,
                open_risk_amount=float(risk_manager.get_state()["open_risk_amount"]),
            )
            tracked_positions.extend(executed)
            state["tracked_positions"] = tracked_positions
            _save_state(state_path, state)
            return {"job": job_name, "executed": executed, "dry_run": dry_run}

        if job_name == "intraday":
            tracked_positions = state.get("tracked_positions", [])
            actions = run_intraday(broker, alerter, news_filter, tracked_positions)
            state["tracked_positions"] = tracked_positions
            _save_state(state_path, state)
            return {"job": job_name, "actions": actions, "dry_run": dry_run}

        if job_name == "eod":
            summary = run_eod(risk_manager, state.get("tracked_positions", []), daily_pnl=0.0, alerter=alerter)
            append_markdown_log(SETTINGS.paths.trade_log, "Position Snapshot", {"positions": positions or "none"})
            return {"job": job_name, "summary": summary, "dry_run": dry_run}

        raise ValueError(f"Unsupported job '{job_name}'")
    finally:
        broker.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IBKR Gerchik bot job runner.")
    parser.add_argument("--job", required=True, choices=["premarket", "open", "intraday", "eod", "weekly"])
    parser.add_argument("--dry-run", action="store_true", help="Simulate trades without placing broker orders.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_job(args.job, dry_run_override=True if args.dry_run else None)
        LOGGER.info("Job completed: %s", result)
        return 0
    except Exception as exc:  # pragma: no cover - top-level safety net.
        LOGGER.exception("Fatal error while running job '%s'.", args.job)
        SlackAlerter().send_error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
