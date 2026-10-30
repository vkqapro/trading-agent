# Gaussian Channel

## Runtime status

Gaussian Channel is registered by the default Decision Lab source registry. Its source adapter reads the existing dashboard monitor over persisted data. In Decision Lab it is explicitly analysis-only: no deterministic entry, stop, target, or execution eligibility is produced.

## Confirmed monitor contract

The dashboard monitor uses a recursive Gaussian filter over HLC3 and a filtered true-range channel.

- `GAUSSIAN_LONG_ENTRY` is a recent bullish close cross above the upper channel while the filtered mid is rising and the monitor is not already long.
- `GAUSSIAN_NEAR_LONG` is a watch state below/at the upper channel while the filtered mid is rising and price is within the configured near threshold.
- `GAUSSIAN_LONG_EXIT` and `GAUSSIAN_NEAR_EXIT` are position-management conditions for the monitor's simulated long position.

The source adapter maps the long-entry state to `ENTRY_SIGNAL`, other non-exit monitor states to `WATCH_CANDIDATE`, and exit states to `POSITION_MANAGEMENT_SIGNAL`; it does not grant execution capability.

## Analyst evidence

Report source signal state, filter/channel evidence returned by the monitor, bar timestamps, and source freshness. The simulated monitor position is not broker-position evidence, and no order plan may be inferred.

## Code and test authority

- `dashboard_react/server.py:_gaussian_scan_symbol`
- `dashboard_react/server.py:_gaussian_filter`
- `src/decision/strategy_sources.py:_monitor_snapshots`
- `src/decision/strategy_sources.py:_monitor_candidate_class`
- `src/decision/strategy_definitions.py`
- `tests/test_strategy_sources.py`
