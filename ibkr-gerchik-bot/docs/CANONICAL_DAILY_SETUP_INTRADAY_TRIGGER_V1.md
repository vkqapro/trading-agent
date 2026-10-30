# Canonical Daily Setup + Intraday Trigger v1

This preview layer is a deterministic, read-only two-stage engine for LONG-only LP1, LP2, PRB1 and PRB2.

```text
closed daily bars -> DailySetup -> current-session adapter -> TriggerEvaluation -> WATCH / READY / REJECTED
```

## Contracts

`DailySetup` contains schema version, stable `setup_id`, symbol, strategy, LONG direction, level, closed pattern bars, frozen entry/stop/target, setup evidence and provenance. The entry day used to freeze the plan is explicitly excluded from `pattern_bars`.

`CurrentSessionState` contains session date, OHLCV, `as_of`, `complete` (derived from completeness), source, timeframe, persisted timestamp, numeric freshness and a `FRESH`/`MISSING` assessment. The adapter first accepts an incomplete persisted daily row for the requested session and otherwise aggregates persisted 5-minute rows; it never uses quotes or external data.

`TriggerEvaluation` contains status, exact reason, deterministic trigger evidence and rejection reasons. `LiveStrategyResult` combines the three objects and historical/current provenance.

## Status semantics

- `WATCH`: valid closed setup, current high has not reached planned entry.
- `READY`: current high reached planned entry and deterministic gates pass. This is not approval, submission or execution.
- `REJECTED`: an explicit gate such as `GAP_EXCEEDED`, `CHASE_EXCEEDED` or `INVALID_STOP` failed.
- `STALE_DATA`: current state exists but exceeds the configured preview freshness threshold (900 seconds).
- `NO_SETUP`: no valid historical setup.
- `INVALID`: contract inconsistency or missing session.

## Strategy split

The evaluator delegates historical rule semantics to the existing Web pure functions (`_pivot_levels`, `_add_metrics`, `_detect_signals`, `_side_prices`, `_execution_details`) without modifying the Web route. LP1 is one-bar false-break/reclaim; LP2 is penetration/reclaim across two completed bars; PRB1 is a one-bar breakout/volume/level-hold; PRB2 is breakout plus hold plus entry day. Current-session evaluation only checks the frozen plan and gates; it never redetects a pattern.

## MCP

The existing localhost-only Strategy Scanner MCP remains the only process and port. `preview_live_setups` is the established focused preview tool; passing `symbol="ALL"` invokes the new read-only universe path (`run_live_universe_scan` service operation) and returns per-symbol setup/triggers, freshness/source, counts and runtime. The legacy `run_universe_scan` path and schema remain untouched.

No broker, IBKR, order queue, planner, Telegram, scheduler, LLM or production Web migration is involved.
