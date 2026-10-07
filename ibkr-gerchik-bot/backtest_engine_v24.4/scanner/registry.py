"""Explicit production strategy allowlist; no dynamic strategy discovery."""
from __future__ import annotations

from .adapters.lp_prb import evaluate_symbol_canonical
from .contracts import StrategySpec

_REQUIRED_FIELDS = ("Open", "High", "Low", "Close", "Volume")
_MINIMUM_BARS = 23

_REGISTRY = {
    strategy_id: StrategySpec(
        strategy_id=strategy_id,
        display_name=display_name,
        version="market-screener-lp-prb-strategy-2",
        directions=("LONG",),
        timeframe="1D",
        required_fields=_REQUIRED_FIELDS,
        minimum_bars=_MINIMUM_BARS,
        adapter=evaluate_symbol_canonical,
        production_enabled=True,
    )
    for strategy_id, display_name in (
        ("lp1", "LP1"),
        ("lp2", "LP2"),
        ("prb1", "PRB1"),
        ("prb2", "PRB2"),
    )
}

PRODUCTION_STRATEGY_IDS = tuple(_REGISTRY)


def get_strategy_spec(strategy_id: str) -> StrategySpec:
    key = str(strategy_id).strip().lower()
    if key not in _REGISTRY:
        raise KeyError(f"Strategy is not production-allowlisted: {strategy_id}")
    return _REGISTRY[key]


def list_strategy_specs(*, production_only: bool = True) -> tuple[StrategySpec, ...]:
    specs = tuple(_REGISTRY.values())
    return tuple(spec for spec in specs if spec.production_enabled) if production_only else specs
