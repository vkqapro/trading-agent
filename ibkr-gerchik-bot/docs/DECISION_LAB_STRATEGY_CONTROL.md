# Decision Lab Strategy Control

Decision Lab is a read-only control surface for the persistent Autonomous Stock
Worker. The dashboard writes only small control/run records under the runtime
directory; it does not create an IBKR client, fetch candles, scan symbols, or
submit orders.

## Source selector

The registry is implemented in `src/decision/strategy_sources.py` and exposes:

- `All`: one source-neutral evidence record per symbol, preferring an
  executable plan when one exists and retaining all source evidence in
  metadata.
- `Stock Screener`: reuses `dashboard_react.market_screener.run_market_screener`
  over the bars already present in the completed scan context.
- `BMSB`: calls the existing BMSB monitor calculator and is currently
  analysis-only because it does not provide a deterministic entry, stop, and
  target together.
- `Gaussian`: calls the existing Gaussian monitor calculator and is currently
  analysis-only for the same reason.
- `Gerchik Router`: consumes the accepted signal details emitted by the
  existing `run_entry_scan` strategy router.

The adapters normalize into `StrategySnapshot`. Strategy formulas are not
duplicated in the controller. Executable snapshots are adapted to the existing
`DecisionCandidate` and `TradeSignal` boundary; analysis-only snapshots expose
only `WAIT` and `REJECT`.

All adapters execute inside the same completed-scan boundary. If an adapter
fails, its source is persisted as `SOURCE_UNAVAILABLE` with an empty result;
the controller never substitutes a previous scan’s source data. An empty
successful adapter is recorded as `NO_CANDIDATES`.

## MANUAL and AUTO

The durable control file is `memory/runtime/decision_lab_control.json`.
Defaults are `MANUAL` and `Gerchik Router`. The API is:

- `GET /api/decision-lab/strategies`: registry-backed source catalog;
- `GET /api/decision-lab/control`: current control, latest completed snapshot,
  and recent runs;
- `POST /api/decision-lab/control`: changes `analysis_mode` and
  `strategy_source` only;
- `POST /api/decision-lab/run`: queues one bounded manual run;
- `GET /api/decision-lab/runs`: read-only run history.

`RUN ANALYSIS` never fetches a candle or starts an independent scanner. The
manual run captures the latest persisted `scan_id` and is consumed by the
worker from that cached snapshot. If the worker is between scans, it drains
the cached snapshot before replacing it with the next normal scan. If no
completed snapshot exists yet, the first normal completed scan supplies the
initial snapshot and the request remains queued until then.

`AUTO` is owned by the persistent worker. After each completed intraday
`run_entry_scan` iteration, the worker publishes one coherent source snapshot
and schedules the selected source. The existing scan cadence remains the
authority (`get_scan_interval`): it is approximately 5 minutes early in the
session, then 10/15 minute windows later in the session. AUTO is not a blind
30-minute timer.

Changing MANUAL to AUTO affects the next completed scan only; it does not
replay the previous snapshot. Changing AUTO to MANUAL prevents future AUTO
runs; a run already `QUEUED` or `RUNNING` is not silently cancelled. Changing
the source applies to the next AUTO scan, while an already queued MANUAL run
keeps the source selected when it was requested.

Runs are idempotent by `(analysis_mode, strategy_selection, scan_id)`. Durable
JSON writes are atomic and protected by a sidecar lock so API and worker
processes cannot partially overwrite control, snapshot, or run state.

## Safety boundary

The controller calls the existing `AutonomousGerchikAgent.process_candidate`
path. It does not add a broker execution path. The worker supplies the same
order manager and runtime context used by the existing job. Stock Screener and
Gerchik Router may be classified `EXECUTABLE_ENTRY_SIGNAL` only when their
source evidence already contains positive entry, stop, and target values.
BMSB/Gaussian candidates cannot become entries merely because an LLM is asked
to analyze them.

The dashboard status remains a read-only projection of the sanitized worker
heartbeat. Broker identity is never persisted in the Decision Lab snapshot,
run record, API response, or UI.
