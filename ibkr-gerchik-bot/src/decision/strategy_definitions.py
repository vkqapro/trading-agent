"""Code-owned, versioned strategy semantics for Decision Lab prompts.

Definitions in this module describe the repository's actual calculators.  They
are deliberately separate from the editable prompt presets: operators may
change analytical preferences, never the strategy calculations or safety
policy represented here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from dashboard_react.market_screener import ScreenerParams
from src.config import SETTINGS


@dataclass(frozen=True)
class SignalDefinition:
    signal_id: str
    description: str
    conditions: str
    semantic_meaning: str
    candidate_class: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_source: str
    display_name: str
    definition_version: str
    description: str
    timeframes: tuple[str, ...]
    required_inputs: tuple[str, ...]
    indicators: Mapping[str, object]
    signals: tuple[SignalDefinition, ...]
    candidate_class_mapping: Mapping[str, str]
    execution_capability: str
    limitations: tuple[str, ...]
    implementation_provenance: tuple[str, ...]

    @property
    def source_execution_capability(self) -> str:
        """Execution capability is independent from the signal semantic class."""
        return self.execution_capability

    @property
    def definition_hash(self) -> str:
        encoded = json.dumps(self.to_dict(include_hash=False), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "strategy_source": self.strategy_source,
            "display_name": self.display_name,
            "definition_version": self.definition_version,
            "description": self.description,
            "timeframes": list(self.timeframes),
            "required_inputs": list(self.required_inputs),
            "indicators": dict(self.indicators),
            "signals": [item.to_dict() for item in self.signals],
            "candidate_class_mapping": dict(self.candidate_class_mapping),
            "execution_capability": self.execution_capability,
            "source_execution_capability": self.source_execution_capability,
            "limitations": list(self.limitations),
            "implementation_provenance": list(self.implementation_provenance),
        }
        if include_hash:
            payload["definition_hash"] = self.definition_hash
        return payload


def _signal(
    signal_id: str,
    description: str,
    conditions: str,
    meaning: str,
    candidate_class: str,
) -> SignalDefinition:
    return SignalDefinition(signal_id, description, conditions, meaning, candidate_class)


_DEFINITIONS: dict[str, StrategyDefinition] = {
    "gaussian": StrategyDefinition(
        strategy_source="gaussian",
        display_name="Gaussian Channel",
        definition_version="v1",
        description=(
            "The existing dashboard Gaussian monitor uses a four-pole recursive "
            "Gaussian filter over HLC3 and a filtered true-range channel."
        ),
        timeframes=("daily",),
        required_inputs=("open", "high", "low", "close", "date"),
        indicators={
            "source": "(high + low + close) / 3",
            "true_range": "max(high-low, abs(high-previous_close), abs(low-previous_close))",
            "gaussian_period": 144,
            "gaussian_poles": 4,
            "channel_multiplier": 1.414,
            "near_threshold_pct": 1.0,
            "recent_signal_days": 2,
        },
        signals=(
            _signal(
                "GAUSSIAN_LONG_ENTRY",
                "A recent bullish close cross above the Gaussian upper channel.",
                "filtered mid is rising and close crosses from at/below the prior upper channel to above the current upper channel while the simulated position is flat/non-long",
                "A deterministic bullish entry trigger was detected; it remains analysis-only in Decision Lab.",
                "ENTRY_SIGNAL",
            ),
            _signal(
                "GAUSSIAN_NEAR_LONG",
                "Price is within the configured near threshold of the upper channel while the filtered mid is rising.",
                "no recent cross signal and close is at/below the current upper channel with abs(upper-close)/close <= near_threshold_pct",
                "A watch candidate near a potential long trigger, not a confirmed entry.",
                "WATCH_CANDIDATE",
            ),
            _signal(
                "GAUSSIAN_LONG_EXIT",
                "A recent close cross below the Gaussian upper channel while the monitor's simulated position is long.",
                "simulated position is long and close crosses from at/above the prior upper channel to below the current upper channel",
                "A position-management signal; it only applies when the same-symbol position exists.",
                "POSITION_MANAGEMENT_SIGNAL",
            ),
            _signal(
                "GAUSSIAN_NEAR_EXIT",
                "A long simulated position is near the upper-channel exit condition.",
                "simulated position is long, close is at/above the current upper channel, and abs(close-upper)/close <= near_threshold_pct",
                "A position-management watch signal; it is not applicable without a matching same-symbol position.",
                "POSITION_MANAGEMENT_SIGNAL",
            ),
            _signal("NO_SIGNAL", "No current Gaussian signal.", "no qualifying recent cross or near condition", "No candidate is emitted to Decision Lab.", "NONE"),
        ),
        candidate_class_mapping={
            "GAUSSIAN_LONG_ENTRY": "ENTRY_SIGNAL",
            "GAUSSIAN_NEAR_LONG": "WATCH_CANDIDATE",
            "GAUSSIAN_LONG_EXIT": "POSITION_MANAGEMENT_SIGNAL",
            "GAUSSIAN_NEAR_EXIT": "POSITION_MANAGEMENT_SIGNAL",
        },
        execution_capability="ANALYSIS_ONLY",
        limitations=(
            "The monitor's historical simulated position is not a broker position.",
            "Gaussian candidates do not contain a deterministic entry/stop/target plan.",
            "Decision Lab must not convert Gaussian signals into executable entries.",
        ),
        implementation_provenance=(
            "dashboard_react/server.py:_gaussian_alpha",
            "dashboard_react/server.py:_gaussian_filter",
            "dashboard_react/server.py:_gaussian_scan_symbol",
        ),
    ),
    "bmsb": StrategyDefinition(
        strategy_source="bmsb",
        display_name="BMSB",
        definition_version="v1",
        description="The existing BMSB monitor evaluates the weekly 21 EMA / 20 SMA band.",
        timeframes=("weekly", "daily session date"),
        required_inputs=("weekly close", "daily session date"),
        indicators={
            "ema_period": 21,
            "sma_period": 20,
            "near_cross_threshold_pct": 0.75,
            "recent_cross_days": 2,
        },
        signals=(
            _signal("CROSSED_LONG", "Recent bullish EMA/SMA cross.", "previous EMA-SMA <= 0 and current EMA-SMA > 0 within recent_cross_days", "A deterministic bullish BMSB entry trigger; the source remains analysis-only in Decision Lab.", "ENTRY_SIGNAL"),
            _signal("NEAR_LONG", "The band is close with EMA at/below SMA.", "abs(EMA-SMA)/close <= near_cross_threshold_pct and EMA <= SMA", "A near bullish cross watch signal, not confirmation.", "WATCH_CANDIDATE"),
            _signal("CROSSED_EXIT", "Recent bearish EMA/SMA cross.", "previous EMA-SMA >= 0 and current EMA-SMA < 0 within recent_cross_days", "A position-management signal requiring a matching position.", "POSITION_MANAGEMENT_SIGNAL"),
            _signal("NEAR_EXIT", "The band is close with EMA above SMA.", "abs(EMA-SMA)/close <= near_cross_threshold_pct and EMA > SMA", "A position-management watch signal requiring a matching position.", "POSITION_MANAGEMENT_SIGNAL"),
            _signal("NO_SIGNAL", "No current BMSB signal.", "no qualifying cross or near condition", "No candidate is emitted.", "NONE"),
        ),
        candidate_class_mapping={
            "CROSSED_LONG": "ENTRY_SIGNAL",
            "NEAR_LONG": "WATCH_CANDIDATE",
            "CROSSED_EXIT": "POSITION_MANAGEMENT_SIGNAL",
            "NEAR_EXIT": "POSITION_MANAGEMENT_SIGNAL",
        },
        execution_capability="ANALYSIS_ONLY",
        limitations=("The Decision Lab BMSB adapter does not provide a deterministic trade plan.",),
        implementation_provenance=(
            "dashboard_react/server.py:_bmsb_scan_symbol",
            "strategies/bmsb_strategy_1.py:add_bmsb_signals (related backtest implementation)",
        ),
    ),
    "stock_screener": StrategyDefinition(
        strategy_source="stock_screener",
        display_name="Stock Screener",
        definition_version="v1",
        description="The deterministic market screener identifies LP1, LP2, PRB1, and PRB2 level patterns.",
        timeframes=("daily",),
        required_inputs=("OHLCV", "pivot levels", "ATR(14)", "volume", "corporate-action/event quality inputs"),
        indicators={
            "atr": "cleaned Wilder ATR(14)",
            "pivot_lookback": 5,
            "minimum_level_touches": 2,
            "supported_patterns": ["LP1", "LP2", "PRB1", "PRB2"],
        },
        signals=(
            _signal("LP1", "One-bar false break and reclaim/rejection at a level.", "see _detect_signals LP1 conditions", "A deterministic pattern with a calculated plan when READY.", "EXECUTABLE_ENTRY_SIGNAL"),
            _signal("LP2", "Two-bar false break and reclaim/rejection.", "see _detect_signals LP2 conditions", "A deterministic pattern with a calculated plan when READY.", "EXECUTABLE_ENTRY_SIGNAL"),
            _signal("PRB1", "One-bar breakout/retest/hold at a level.", "see _detect_signals PRB1 conditions", "A deterministic pattern with a calculated plan when READY.", "EXECUTABLE_ENTRY_SIGNAL"),
            _signal("PRB2", "Two-bar breakout/retest/hold sequence.", "see _detect_signals PRB2 conditions", "A deterministic pattern with a calculated plan when READY.", "EXECUTABLE_ENTRY_SIGNAL"),
            _signal("READY", "Calculated entry, stop, target, sizing, and quality gates are valid.", "execution status is READY", "May receive ENTER/WAIT/REJECT under the existing system policy.", "EXECUTABLE_ENTRY_SIGNAL"),
            _signal("pending_entry", "Pattern exists but the calculated entry has not triggered.", "execution status is pending_entry", "A watch candidate; it is not an executable entry.", "WATCH_CANDIDATE"),
        ),
        candidate_class_mapping={"READY": "EXECUTABLE_ENTRY_SIGNAL", "pending_entry": "WATCH_CANDIDATE", "LP1": "EXECUTABLE_ENTRY_SIGNAL", "LP2": "EXECUTABLE_ENTRY_SIGNAL", "PRB1": "EXECUTABLE_ENTRY_SIGNAL", "PRB2": "EXECUTABLE_ENTRY_SIGNAL"},
        execution_capability="DETERMINISTIC_PLAN_REQUIRED",
        limitations=("The LLM cannot change entry, stop, target, quantity, or risk calculations.",),
        implementation_provenance=(
            "dashboard_react/market_screener.py:_passes_filters",
            "dashboard_react/market_screener.py:_detect_signals",
            "dashboard_react/market_screener.py:_make_signal",
        ),
    ),
    "gerchik_router": StrategyDefinition(
        strategy_source="gerchik_router",
        display_name="Gerchik Router",
        definition_version="v1",
        description="Persisted output from the existing strategy router and detectors; Decision Lab does not run the router.",
        timeframes=("source-defined",),
        required_inputs=("persisted router signal details",),
        indicators={"router": "existing strategy_router.py and registered detectors"},
        signals=(
            _signal("rebound", "Persisted accepted/rejected router signal.", "source-defined detector output", "Read-only router evidence.", "EXECUTABLE_ENTRY_SIGNAL"),
            _signal("confirmed_breakout", "Persisted router breakout signal.", "source-defined detector output", "Read-only router evidence.", "EXECUTABLE_ENTRY_SIGNAL"),
            _signal("false_breakout", "Persisted one/two/complex false-breakout signal.", "source-defined detector output", "Read-only router evidence.", "EXECUTABLE_ENTRY_SIGNAL"),
        ),
        candidate_class_mapping={"accepted signal with complete plan": "EXECUTABLE_ENTRY_SIGNAL", "incomplete signal": "WATCH_CANDIDATE"},
        execution_capability="EXISTING_ROUTER_PLAN_ONLY",
        limitations=("Decision Lab consumes persisted router output and never launches entry scans.",),
        implementation_provenance=(
            "src/strategy/strategy_router.py:route_strategies",
            "src/decision/strategy_sources.py:_gerchik_snapshots",
        ),
    ),
}


def get_strategy_definition(strategy_source: str) -> StrategyDefinition:
    source = str(strategy_source or "").strip().lower()
    if source == "all":
        return StrategyDefinition(
            strategy_source="all",
            display_name="All Strategies",
            definition_version="v1",
            description="Combined source-neutral Decision Lab view; each candidate retains its source definition.",
            timeframes=tuple(sorted({item for definition in _DEFINITIONS.values() for item in definition.timeframes})),
            required_inputs=("source-specific persisted strategy evidence",),
            indicators={"source_definitions": list(_DEFINITIONS)},
            signals=tuple(signal for definition in _DEFINITIONS.values() for signal in definition.signals),
            candidate_class_mapping={"source-specific": "source-defined"},
            execution_capability="SOURCE_DEFINED",
            limitations=("The selected source definition remains authoritative for every candidate.",),
            implementation_provenance=("src/decision/strategy_sources.py:StrategySourceRegistry",),
        )
    try:
        return _DEFINITIONS[source]
    except KeyError as exc:
        raise ValueError(f"Unknown strategy definition: {strategy_source}") from exc


def current_strategy_parameters(strategy_source: str) -> dict[str, object]:
    """Resolve parameters from the same runtime constants/calculators used by the app."""
    source = str(strategy_source or "").strip().lower()
    if source == "all":
        return {item: current_strategy_parameters(item) for item in _DEFINITIONS}
    if source == "gaussian":
        from dashboard_react import server

        return {
            "timeframe": "daily",
            "period": server.GAUSSIAN_PERIOD,
            "poles": server.GAUSSIAN_POLES,
            "channel_multiplier": server.GAUSSIAN_MULT,
            "near_threshold_pct": server.GAUSSIAN_NEAR_THRESHOLD_PCT,
            "recent_signal_days": server.GAUSSIAN_RECENT_SIGNAL_DAYS,
        }
    if source == "bmsb":
        from dashboard_react import server

        return {
            "timeframe": "weekly",
            "ema_period": 21,
            "sma_period": 20,
            "near_cross_threshold_pct": server.BMSB_NEAR_CROSS_THRESHOLD_PCT,
            "recent_cross_days": server.BMSB_RECENT_CROSS_DAYS,
        }
    if source == "stock_screener":
        params = ScreenerParams()
        return {key: value for key, value in asdict(params).items() if key not in {"symbols"}}
    if source == "gerchik_router":
        strategy = SETTINGS.strategy
        return {
            "enabled_strategies": list(getattr(strategy, "enabled_strategies", ())),
            "lookback_bars": getattr(strategy, "lookback_bars", None),
            "level_tolerance_pct": getattr(strategy, "level_tolerance_pct", None),
            "timeframe": "source-defined",
        }
    raise ValueError(f"Unknown strategy parameter source: {strategy_source}")


def list_strategy_definitions() -> list[dict[str, object]]:
    return [get_strategy_definition(source).to_dict() for source in (*_DEFINITIONS,)]
