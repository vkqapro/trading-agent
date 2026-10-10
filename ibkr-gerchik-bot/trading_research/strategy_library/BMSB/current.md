# BMSB

## Runtime status

BMSB is registered by the default Decision Lab source registry. The source adapter obtains its signal from the existing dashboard monitor over persisted data. In Decision Lab it is explicitly analysis-only: it does not provide a deterministic entry, stop, target, or execution eligibility.

## Confirmed monitor contract

The monitor evaluates the weekly 21 EMA / 20 SMA band.

- `CROSSED_LONG` is a recent bullish EMA/SMA cross.
- `NEAR_LONG` is a near-cross watch state while EMA is at or below SMA.
- `CROSSED_EXIT` and `NEAR_EXIT` are position-management signals, not new-entry plans.

The source adapter maps a BMSB entry signal to an `ENTRY_SIGNAL`, near states to `WATCH_CANDIDATE`, and exits to `POSITION_MANAGEMENT_SIGNAL`; it keeps `execution_eligible` false and does not create a TradeSignal.

## Analyst evidence

Report source signal state, weekly/daily bar timestamps, displayed entry/trigger levels if provided, and source freshness. Do not construct an order plan from a BMSB monitor result.

## Code and test authority

- `dashboard_react/server.py:_bmsb_scan_symbol`
- `src/decision/strategy_sources.py:_monitor_snapshots`
- `src/decision/strategy_sources.py:_monitor_candidate_class`
- `src/decision/strategy_definitions.py`
- `tests/test_strategy_sources.py`
