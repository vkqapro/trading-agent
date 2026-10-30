"""Paths owned by the read-only Trading Research Analyst.

The production application configuration module is intentionally not imported
here: importing it initializes the application file logger under the
production repository. Research configuration is limited to paths and never
loads, changes, or relies on ``.env``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESEARCH_ROOT = Path(r"C:\AI\TradingResearch")


def configured_research_root() -> Path:
    """Return the single runtime workspace without creating it.

    The research analyst is allowed to create artifacts only below this fixed,
    external location. An environment override would make a Hermes invocation
    dependent on mutable process state and could redirect a runtime write
    toward the production checkout.
    """
    return DEFAULT_RESEARCH_ROOT


@dataclass(frozen=True)
class ProductionDataPaths:
    """Read-only locations consumed from the existing Orders Journal stores."""

    repository_root: Path = REPOSITORY_ROOT

    @property
    def memory_dir(self) -> Path:
        return self.repository_root / "memory"

    @property
    def order_requests(self) -> Path:
        return self.memory_dir / "order_requests.json"

    @property
    def tracked_positions(self) -> Path:
        return self.memory_dir / "runtime" / "state.json"

    @property
    def closed_positions(self) -> Path:
        return self.memory_dir / "runtime" / "closed_positions.json"

    @property
    def tradingview_stock(self) -> Path:
        return self.memory_dir / "runtime" / "tradingview_stock_state.json"

    @property
    def tradingview_forex(self) -> Path:
        return self.memory_dir / "runtime" / "tradingview_forex_state.json"

    @property
    def crypto_state(self) -> Path:
        return self.memory_dir / "crypto" / "runtime" / "tradingview_okx_state.json"

    @property
    def bars_dir(self) -> Path:
        return self.memory_dir / "bars"

