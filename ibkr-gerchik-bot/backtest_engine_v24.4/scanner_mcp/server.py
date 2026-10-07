"""Localhost-only Strategy Scanner MCP over Streamable HTTP."""
from __future__ import annotations

import atexit
import logging
import os
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .schemas import ScannerMCPError
from .service import StrategyScannerService

LOGGER = logging.getLogger("strategy_scanner")
ENABLED = os.getenv("STRATEGY_SCANNER_MCP_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
HOST = os.getenv("STRATEGY_SCANNER_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("STRATEGY_SCANNER_MCP_PORT", "8766"))
PID_FILE = str(os.getenv("STRATEGY_SCANNER_MCP_PID_FILE", "")).strip()

mcp = FastMCP("strategy_scanner", stateless_http=True, json_response=True)
service = StrategyScannerService()


def _safe(tool_name: str, call: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    LOGGER.info("MCP request tool=%s", tool_name)
    try:
        return call(**kwargs)
    except ScannerMCPError as exc:
        LOGGER.warning("MCP structured error tool=%s code=%s details=%s", tool_name, exc.code, exc.details or {})
        return exc.payload()
    except Exception:
        LOGGER.exception("Unhandled Strategy Scanner MCP request failure tool=%s", tool_name)
        return {"error": {"code": "SCAN_FAILED", "message": "Strategy Scanner request failed", "details": {}}}


@mcp.tool()
def list_strategies() -> dict[str, Any]:
    """List production-enabled deterministic scanner strategies and requirements."""
    return _safe("list_strategies", service.list_strategies)


@mcp.tool()
def list_available_symbols() -> dict[str, Any]:
    """List the configured universe, persisted daily coverage, and missing symbols."""
    return _safe("list_available_symbols", service.list_available_symbols)


@mcp.tool()
def run_universe_scan(
    strategies: list[str] | None = None,
    symbols: list[str] | str | None = "ALL",
    direction: str = "LONG",
    as_of: str | None = None,
) -> dict[str, Any]:
    """Run registered deterministic strategies through the existing scanner API.

    Use symbols="ALL" for the configured universe. This writes a new immutable
    scanner artifact and returns only compact run metadata, never all symbol rows.
    """
    return _safe("run_universe_scan", service.run_universe_scan, strategies=strategies, symbols=symbols, direction=direction, as_of=as_of)


@mcp.tool()
def preview_live_setups(
    symbol: str,
    strategies: list[str] | None = None,
    as_of: str | None = None,
    timeframe: str = "5m",
) -> dict[str, Any]:
    """Preview closed-daily canonical setups and current-session triggers.

    This is analysis only: READY is not approval and no order, broker, or scanner
    run is started.
    """
    if str(symbol).strip().upper() == "ALL":
        return _safe("preview_live_setups", service.run_live_universe_scan, strategies=strategies, symbols="ALL", as_of=as_of, timeframe=timeframe)
    return _safe("preview_live_setups", service.preview_live_setups, symbol=symbol, strategies=strategies, as_of=as_of, timeframe=timeframe)


@mcp.tool()
def get_live_candidate_proposals(
    account_capital: float | None = None,
    max_capital_allocation_pct: float | None = None,
    max_loss_risk_pct: float | None = None,
    available_funds: float | None = None,
    max_position_value: float | None = None,
    slippage_per_share: float = 0.0,
    fees_per_share: float = 0.0,
    include_execution_costs: bool = False,
    strategies: list[str] | None = None,
    symbols: list[str] | str | None = "ALL",
    as_of: str | None = None,
    timeframe: str = "5m",
) -> dict[str, Any]:
    """Plan READY canonical live results only; never submits or queues orders."""
    return _safe(
        "get_live_candidate_proposals", service.get_live_candidate_proposals,
        account_capital=account_capital, max_capital_allocation_pct=max_capital_allocation_pct,
        max_loss_risk_pct=max_loss_risk_pct, available_funds=available_funds,
        max_position_value=max_position_value, slippage_per_share=slippage_per_share,
        fees_per_share=fees_per_share, include_execution_costs=include_execution_costs,
        strategies=strategies, symbols=symbols, as_of=as_of, timeframe=timeframe,
    )


@mcp.tool()
def get_scan_summary(run_id: str) -> dict[str, Any]:
    """Return the saved deterministic summary for one validated scan run."""
    return _safe("get_scan_summary", service.get_scan_summary, run_id=run_id)


@mcp.tool()
def get_strategy_candidates(
    run_id: str,
    strategy: str | None = None,
    status: str | None = "ENTRY_SIGNAL",
    symbol: str | None = None,
    limit: int = 50,
    page: int = 1,
) -> dict[str, Any]:
    """Return matched candidates with deterministic audit evidence.

    Each candidate includes exact pattern_bars, trigger_evidence,
    rejection_evidence, and ready-to-draw chart_annotations. Consumers should
    use these saved fields rather than infer pattern identities from candles.
    """
    return _safe("get_strategy_candidates", service.get_strategy_candidates, run_id=run_id, strategy=strategy, status=status, symbol=symbol, limit=limit, page=page)


@mcp.tool()
def get_candidate_proposals(
    run_id: str,
    account_capital: float | None = None,
    max_capital_allocation_pct: float | None = None,
    max_loss_risk_pct: float | None = None,
    available_funds: float | None = None,
    max_position_value: float | None = None,
    slippage_per_share: float = 0.0,
    fees_per_share: float = 0.0,
    include_execution_costs: bool = False,
    strategies: list[str] | None = None,
    symbols: list[str] | str | None = None,
    limit: int = 200,
    page: int = 1,
) -> dict[str, Any]:
    """Size existing LONG ENTRY_SIGNAL artifacts with explicit planner inputs.

    This is a read-only preview operation: it never starts a scan, mutates an
    artifact, accesses a broker, or submits an order.
    """
    return _safe(
        "get_candidate_proposals", service.get_candidate_proposals,
        run_id=run_id, account_capital=account_capital,
        max_capital_allocation_pct=max_capital_allocation_pct,
        max_loss_risk_pct=max_loss_risk_pct, available_funds=available_funds,
        max_position_value=max_position_value,
        slippage_per_share=slippage_per_share, fees_per_share=fees_per_share,
        include_execution_costs=include_execution_costs, strategies=strategies,
        symbols=symbols, limit=limit, page=page,
    )


@mcp.tool()
def get_symbol_scan(run_id: str, symbol: str) -> dict[str, Any]:
    """Return every saved strategy result and its deterministic audit evidence.

    New schema-1.1 runs include pattern_definition, exact pattern_bars,
    trigger_evidence, rejection_evidence, and chart_annotations per strategy.
    """
    return _safe("get_symbol_scan", service.get_symbol_scan, run_id=run_id, symbol=symbol)


@mcp.tool()
def list_scan_runs(limit: int = 20) -> dict[str, Any]:
    """List compact immutable scan-run metadata, newest first."""
    return _safe("list_scan_runs", service.list_scan_runs, limit=limit)


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "Strategy Scanner MCP", "namespace": "strategy_scanner", "mode": "deterministic_analysis", "host": HOST, "port": PORT})


def _app():
    app = mcp.streamable_http_app()
    app.routes.append(Route("/health", health, methods=["GET"]))
    return app


def _publish_pid() -> None:
    if not PID_FILE:
        return
    path = Path(PID_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="ascii")

    def cleanup() -> None:
        try:
            if path.read_text(encoding="ascii").strip() == str(os.getpid()):
                path.unlink(missing_ok=True)
        except OSError:
            pass

    atexit.register(cleanup)


def main() -> None:
    import uvicorn

    if not ENABLED:
        raise SystemExit("Strategy Scanner MCP is disabled (STRATEGY_SCANNER_MCP_ENABLED=false)")
    if HOST not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Strategy Scanner MCP must bind to localhost only")
    logging.basicConfig(level=os.getenv("STRATEGY_SCANNER_MCP_LOG_LEVEL", "INFO"))
    _publish_pid()
    LOGGER.info("Strategy Scanner MCP started transport=streamable-http host=%s port=%s tools=7", HOST, PORT)
    uvicorn.run(_app(), host=HOST, port=PORT, log_level=os.getenv("STRATEGY_SCANNER_MCP_LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    main()
