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
from src.config import LOGGER, SETTINGS, ensure_directories
from src.data.market_data import MarketDataService
from src.data.news import NewsService
from src.data.news_filter import NewsRiskFilter
from src.execution.order_manager import OrderManager
from src.git_workflow import maybe_commit_and_push
from src.jobs.eod import run_eod
from src.jobs.intraday import run_intraday
from src.jobs.open import run_open
from src.jobs.premarket import run_premarket
from src.jobs.weekly import run_weekly
from src.risk.kill_switch import should_trigger_kill_switch
from src.risk.risk_manager import RiskManager
from src.slack_commands import SlackCommandProcessor
from src.workflow_log import read_latest_workflow_snapshot


def _build_research_symbols(positions: List[Dict[str, object]]) -> List[str]:
    merged: List[str] = []
    seen: set[str] = set()

    for item in positions:
        sec_type = str(item.get("sec_type", "")).strip().upper()
        if sec_type and sec_type != "STK":
            continue
        symbol = str(item.get("symbol", "")).strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            merged.append(symbol)

    for symbol in SETTINGS.symbols:
        normalized = symbol.strip().upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            merged.append(normalized)

    return merged


def _sync_tracked_positions_with_broker(
    tracked_positions: List[Dict[str, object]],
    broker_positions: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    existing_by_symbol: Dict[str, Dict[str, object]] = {}
    for position in tracked_positions:
        symbol = str(position.get("symbol", "")).strip().upper()
        if symbol:
            existing_by_symbol[symbol] = dict(position)

    synced: List[Dict[str, object]] = []
    seen: set[str] = set()
    for broker_position in broker_positions:
        sec_type = str(broker_position.get("sec_type", "")).strip().upper()
        if sec_type != "STK":
            continue

        symbol = str(broker_position.get("symbol", "")).strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)

        quantity = abs(int(float(broker_position.get("position", 0.0) or 0.0)))
        avg_cost = float(broker_position.get("avg_cost", 0.0) or 0.0)
        direction = "long" if float(broker_position.get("position", 0.0) or 0.0) >= 0 else "short"

        base_position = existing_by_symbol.get(symbol, {})
        merged_position = dict(base_position)
        merged_position.update(
            {
                "symbol": symbol,
                "quantity": quantity,
                "entry": float(base_position.get("entry", avg_cost) or avg_cost),
                "avg_cost": avg_cost,
                "direction": str(base_position.get("direction", direction) or direction),
                "sec_type": sec_type,
            }
        )
        synced.append(merged_position)

    return synced


def _account_equity_from_summary(summary: List[Dict[str, object]]) -> float:
    for item in summary:
        if item.get("tag") == "NetLiquidation":
            return float(item.get("value", 0.0))
    return 100_000.0


def _cash_from_summary(summary: List[Dict[str, object]]) -> float:
    for item in summary:
        if item.get("tag") in {"AvailableFunds", "TotalCashValue"}:
            return float(item.get("value", 0.0))
    return _account_equity_from_summary(summary)


def _load_state(state_path: Path) -> Dict[str, object]:
    if not state_path.exists():
        return {"watchlist": {}, "tracked_positions": [], "weekly_results": []}
    with state_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_state(state_path: Path, payload: Dict[str, object]) -> None:
    with state_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def _hydrate_from_logs(state: Dict[str, object]) -> Dict[str, object]:
    """Recover workflow context from the research log if the state file is incomplete."""
    hydrated = dict(state)
    if not hydrated.get("watchlist"):
        premarket = read_latest_workflow_snapshot(SETTINGS.paths.research_log, "Premarket")
        if premarket and isinstance(premarket.get("watchlist"), dict):
            hydrated["watchlist"] = premarket["watchlist"]
    if not hydrated.get("tracked_positions"):
        intraday = read_latest_workflow_snapshot(SETTINGS.paths.research_log, "Intraday")
        if intraday and isinstance(intraday.get("tracked_positions"), list):
            hydrated["tracked_positions"] = intraday["tracked_positions"]
        else:
            open_snapshot = read_latest_workflow_snapshot(SETTINGS.paths.research_log, "Open")
            if open_snapshot and isinstance(open_snapshot.get("executed"), list):
                hydrated["tracked_positions"] = open_snapshot["executed"]
    return hydrated


def run_job(job_name: str, dry_run_override: Optional[bool] = None) -> Dict[str, object]:
    ensure_directories()
    state_path = SETTINGS.paths.state_file
    state = _hydrate_from_logs(_load_state(state_path))
    alerter = SlackAlerter()
    dry_run = SETTINGS.dry_run_mode if dry_run_override is None else dry_run_override

    if job_name == "slack":
        result = SlackCommandProcessor(alerter).process(state, run_job)
        _save_state(state_path, state)
        return {"job": job_name, **result}

    if job_name == "weekly":
        metrics = run_weekly(state.get("weekly_results", []))
        return {"job": job_name, "metrics": metrics, "dry_run": dry_run}

    broker = IBKRClient()
    try:
        broker.connect()
        news_service = NewsService(broker=broker)
        news_filter = NewsRiskFilter(news_service)
        market_data = MarketDataService(broker)
        order_manager = OrderManager(broker, market_data, alerter, news_filter, dry_run=dry_run)
        account_summary = broker.get_account_summary()
        account_equity = _account_equity_from_summary(account_summary)
        cash_available = _cash_from_summary(account_summary)
        positions = broker.get_positions()
        open_orders = broker.get_open_orders()
        state["tracked_positions"] = _sync_tracked_positions_with_broker(
            state.get("tracked_positions", []),
            positions,
        )
        research_symbols = _build_research_symbols(positions)
        risk_manager = RiskManager(account_equity)
        repo_root = SETTINGS.paths.trade_log.parents[1]
        account_snapshot = {"account": account_summary, "positions": positions, "open_orders": open_orders}
        _save_state(state_path, state)

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
            premarket_summary = run_premarket(
                market_data,
                news_service,
                news_filter,
                account_snapshot,
                research_symbols,
            )
            watchlist = premarket_summary.get("watchlist", {})
            state["watchlist"] = watchlist
            _save_state(state_path, state)
            alerter.send_premarket_summary(premarket_summary)
            report_path = premarket_summary.get("report_path")
            if isinstance(report_path, str) and report_path:
                uploaded = alerter.upload_file(
                    Path(report_path),
                    title="Premarket Strong Levels",
                    initial_comment="Daily premarket levels report (strength_score > 7).",
                )
                if not uploaded:
                    alerter.send(f"Premarket levels report generated: {Path(report_path).name}")
            maybe_commit_and_push(
                repo_root,
                [SETTINGS.paths.research_log, SETTINGS.paths.state_file],
                "workflow: premarket research update",
            )
            return {"job": job_name, "watchlist_count": len(watchlist), "dry_run": dry_run}

        if job_name == "open":
            if not state.get("watchlist"):
                premarket_summary = run_premarket(
                    market_data,
                    news_service,
                    news_filter,
                    account_snapshot,
                    research_symbols,
                )
                watchlist = premarket_summary.get("watchlist", {})
                state["watchlist"] = watchlist
            else:
                watchlist = state.get("watchlist", {})
            tracked_positions = state.get("tracked_positions", [])
            risk_manager.update_open_risk(tracked_positions)
            executed = run_open(
                market_data=market_data,
                order_manager=order_manager,
                news_filter=news_filter,
                watchlist=watchlist,
                account_equity=account_equity,
                cash_available=cash_available,
                current_positions=positions,
                open_risk_amount=float(risk_manager.get_state()["open_risk_amount"]),
            )
            tracked_positions.extend(executed)
            state["tracked_positions"] = tracked_positions
            _save_state(state_path, state)
            if executed:
                maybe_commit_and_push(
                    repo_root,
                    [SETTINGS.paths.trade_log, SETTINGS.paths.research_log, SETTINGS.paths.state_file],
                    "workflow: market open trades",
                )
            return {"job": job_name, "executed": executed, "dry_run": dry_run}

        if job_name == "intraday":
            tracked_positions = state.get("tracked_positions", [])
            actions = run_intraday(
                broker,
                alerter,
                news_filter,
                tracked_positions,
                account_equity=account_equity,
                dry_run=dry_run,
            )
            state["tracked_positions"] = tracked_positions
            _save_state(state_path, state)
            if actions:
                maybe_commit_and_push(
                    repo_root,
                    [SETTINGS.paths.trade_log, SETTINGS.paths.research_log, SETTINGS.paths.state_file],
                    "workflow: intraday adjustments",
                )
            return {"job": job_name, "actions": actions, "dry_run": dry_run}

        if job_name == "eod":
            summary = run_eod(risk_manager, account_snapshot, state.get("tracked_positions", []), daily_pnl=0.0, alerter=alerter)
            maybe_commit_and_push(
                repo_root,
                [SETTINGS.paths.trade_log, SETTINGS.paths.state_file],
                "workflow: end of day snapshot",
            )
            return {"job": job_name, "summary": summary, "dry_run": dry_run}

        raise ValueError(f"Unsupported job '{job_name}'")
    finally:
        broker.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IBKR Gerchik bot job runner.")
    parser.add_argument("--job", required=True, choices=["premarket", "open", "intraday", "eod", "weekly", "slack"])
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
        try:
            SlackAlerter().send_error(str(exc))
        except Exception:
            LOGGER.exception("Slack error notification failed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
