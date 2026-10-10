"""Read-only trade research tools for the Trading Bot.

This package deliberately has no dependency on execution, broker, MCP, or
strategy modules.  Its public entry point is :class:`TradingResearchService`.
"""

from .analytics_service import ReviewRequest, TradingResearchService

__all__ = ("ReviewRequest", "TradingResearchService")

