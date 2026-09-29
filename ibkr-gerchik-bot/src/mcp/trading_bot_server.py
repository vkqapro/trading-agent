"""Standalone read-only Trading Bot Data MCP over Streamable HTTP."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP, Image
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .trading_data_service import (
    DataError,
    get_level_context as _get_level_context,
    get_symbol_data_coverage as _get_symbol_data_coverage,
    get_symbol_history as _get_symbol_history,
    get_symbol_levels as _get_symbol_levels,
    get_symbol_snapshot as _get_symbol_snapshot,
    render_symbol_chart as _render_symbol_chart,
)

LOGGER = logging.getLogger("trading_bot_data")
ENABLED = os.getenv("TRADING_BOT_MCP_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
HOST = os.getenv("TRADING_BOT_MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("TRADING_BOT_MCP_PORT", "8765"))

mcp = FastMCP("trading_bot_data", stateless_http=True, json_response=True)


def _safe(call: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    try:
        return call(**kwargs)
    except DataError as exc:
        return {"error": {"code": exc.code, "message": exc.message}}
    except Exception:
        LOGGER.exception("MCP data request failed")
        return {"error": {"code": "DATA_SOURCE_UNAVAILABLE", "message": "Persisted Trading Bot data is unavailable"}}


@mcp.tool()
def get_symbol_snapshot(symbol: str) -> dict[str, Any]:
    """Return a compact snapshot from persisted Trading Bot data.

    `current_price` is the latest persisted current-price bar;
    `latest_closed_price` is the most recent completed daily candle close.
    ATR, coverage, level counts, and freshness are included. This tool never
    fetches market data or recalculates levels.
    """
    return _safe(_get_symbol_snapshot, symbol=symbol)


@mcp.tool()
def get_symbol_data_coverage(symbol: str, timeframe: str | None = None) -> dict[str, Any]:
    """Return stored candle coverage for a symbol and optional timeframe."""
    return _safe(_get_symbol_data_coverage, symbol=symbol, timeframe=timeframe)


@mcp.tool()
def get_symbol_history(
    symbol: str,
    timeframe: str = "1D",
    lookback_days: int | None = None,
    limit: int | None = None,
    start: str | None = None,
    end: str | None = None,
    include_incomplete: bool = False,
) -> dict[str, Any]:
    """Return stored OHLCV candles; incomplete candles are excluded by default.

    `latest_closed_price` is always derived from the returned closed candle
    dataset and is never replaced with current snapshot price.
    """
    return _safe(_get_symbol_history, symbol=symbol, timeframe=timeframe, lookback_days=lookback_days, limit=limit, start=start, end=end, include_incomplete=include_incomplete)


@mcp.tool()
def get_symbol_levels(
    symbol: str,
    level_set: str = "consolidated",
    min_strength: float | None = None,
    level_type: str | None = None,
    side: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return existing raw or consolidated levels; no new levels are calculated.

    Distances and above/below filters use `current_price`. The separate
    `level_reference_price` describes the persisted level-generation context.
    """
    return _safe(_get_symbol_levels, symbol=symbol, level_set=level_set, min_strength=min_strength, level_type=level_type, side=side, limit=limit)


@mcp.tool()
def render_symbol_chart(
    symbol: str,
    timeframe: str = "1D",
    lookback_days: int = 60,
    start: str | None = None,
    end: str | None = None,
    show_levels: bool = False,
    level_set: str = "consolidated",
    min_strength: float | None = None,
    level_type: str | None = None,
    side: str | None = None,
    level_labels: str = "compact",
    show_current_price: bool = True,
    include_volume: bool = True,
    width: int | None = None,
    height: int | None = None,
) -> list[Any]:
    """Render a PNG candlestick chart from stored Trading Bot data.

    Uses closed candles by default and can overlay the existing raw or
    consolidated level zones. It does not fetch market data, calculate new
    levels, create signals, or place orders. The response includes JSON
    provenance metadata followed by the PNG image.
    """
    try:
        result = _render_symbol_chart(
            symbol=symbol, timeframe=timeframe, lookback_days=lookback_days,
            start=start, end=end, show_levels=show_levels, level_set=level_set,
            min_strength=min_strength, level_type=level_type, side=side,
            level_labels=level_labels, show_current_price=show_current_price,
            include_volume=include_volume, width=width, height=height,
        )
        return [json.dumps(result["metadata"], indent=2), Image(data=result["image_bytes"], format="png")]
    except DataError as exc:
        return [json.dumps({"error": {"code": exc.code, "message": exc.message}})]
    except Exception:
        LOGGER.exception("MCP chart rendering failed")
        return [json.dumps({"error": {"code": "DATA_SOURCE_UNAVAILABLE", "message": "Chart rendering is unavailable"}})]


@mcp.tool()
def get_level_context(symbol: str, level_price: float) -> dict[str, Any]:
    """Return persisted level metadata within 0.005 price units.

    Separates current-price distance from level-reference-price distance.
    """
    return _safe(_get_level_context, symbol=symbol, level_price=level_price)


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "Trading Bot Data MCP", "mode": "read_only"})


def _app():
    app = mcp.streamable_http_app()
    app.routes.append(Route("/health", health, methods=["GET"]))
    return app


def main() -> None:
    import uvicorn

    if not ENABLED:
        raise SystemExit("Trading Bot Data MCP is disabled (TRADING_BOT_MCP_ENABLED=false)")
    logging.basicConfig(level=os.getenv("TRADING_BOT_MCP_LOG_LEVEL", "INFO"))
    LOGGER.info("Trading Bot Data MCP server started transport=streamable-http host=%s port=%s tools=6", HOST, PORT)
    uvicorn.run(_app(), host=HOST, port=PORT, log_level=os.getenv("TRADING_BOT_MCP_LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    main()
