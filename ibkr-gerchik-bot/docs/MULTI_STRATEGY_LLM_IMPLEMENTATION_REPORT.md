# Multi-Strategy LLM Implementation Report

## Scope and safety result

This implementation adds a registry-backed Decision Lab strategy selector,
manual/AUTO control, source-neutral completed-scan snapshots, durable run
state, and worker-owned analysis scheduling. It does not change the dashboard
execution mode, add a dashboard broker connection, or place an order.

## Architecture

`Autonomous Stock Worker -> existing completed intraday scan ->
strategy_sources registry -> decision_lab_snapshots.json -> strategy controller
-> existing AutonomousGerchikAgent -> existing risk/execution boundary`.

The React dashboard only reads status/catalog/run state and writes control or a
manual run request. It is not an IBKR client and does not call the standalone
preflight CLI.

## Implemented components

1. `src/decision/strategy_sources.py` defines the extensible source registry,
   canonical snapshot model, source adapters, and executable/analysis-only
   classification.
2. `src/decision/strategy_control.py` persists control, snapshots, and runs
   using atomic replacement plus a sidecar lock.
3. `src/decision/strategy_controller.py` consumes completed scans, performs
   MANUAL/AUTO scheduling, groups `All` by symbol, and enforces run
   idempotency.
4. `src/jobs/intraday.py`, `src/jobs/open.py`, and `src/main.py` pass worker
   ownership into the existing scan path. Worker-controlled scans suppress the
   old inline autonomous LLM submission so there is one decision owner.
5. `dashboard_react/server.py` exposes source/control/run endpoints without
   connecting to IBKR.
6. `dashboard_react/index.html` exposes the source selector, MANUAL/AUTO
   controls, bounded RUN ANALYSIS action, latest scan/run state, and a clear
   distinction between the LLM diagnostic connection and worker provider
   health.

## Classification matrix

| Source | Current classification | Deterministic entry + stop + target + risk | LLM may ENTER |
|---|---|---:|---:|
| Stock Screener | `EXECUTABLE_ENTRY_SIGNAL` when READY and complete | Yes, from existing screener output | Yes, through existing gates |
| BMSB | `WATCH_CANDIDATE` / `POSITION_MANAGEMENT_SIGNAL` | No entry/stop/target bundle currently emitted | No |
| Gaussian | `WATCH_CANDIDATE` / `POSITION_MANAGEMENT_SIGNAL` | No entry/stop/target bundle currently emitted | No |
| Gerchik Router | `EXECUTABLE_ENTRY_SIGNAL` when complete | Yes, from existing accepted signal details | Yes, through existing gates |
| All | Per-symbol grouped evidence | Uses an executable source when available | Only if selected snapshot is executable |

The actual runtime classification is carried on each persisted snapshot as
`candidate_class`, `execution_eligible`, and source provenance. No math is
invented by the controller.

## Manual and AUTO semantics

MANUAL creates one `QUEUED` run when the operator presses `RUN ANALYSIS`; the
worker captures the current persisted `scan_id` and consumes that cached
snapshot without triggering a candle fetch or scanner. If no snapshot exists,
the first normal completed scan supplies it. AUTO creates at most one run per
`(source, scan_id)` and is scheduled only by
the persistent worker callback after `run_entry_scan` returns. The existing
session scan cadence is preserved.

## Failure and safety behavior

- unknown source or mode: API rejects the change;
- second active manual run: API returns conflict;
- duplicate AUTO source/scan: existing run is returned;
- stale or stopped worker: existing sanitized heartbeat projection marks it
  stale/stopped and does not claim current broker verification;
- analysis-only source: allowed actions exclude `ENTER`, and no trade signal
  is constructed;
- broker/provider/risk failures: existing agent/audit/reconciliation path
  remains authoritative and fail-closed.

## Validation performed

Focused tests cover source registry/classification, executable signal mapping,
manual/AUTO control validation, atomic run idempotency, and the worker-owned
controller path using a fake agent with no broker calls. Python compilation is
run for all changed Python modules. The full test command and results are
reported with the implementation handoff.

## Current source with a complete deterministic plan

At this stage, Stock Screener and Gerchik Router are the sources that can
provide deterministic entry, stop, target, reward/risk, and risk inputs when
their existing output is complete. BMSB and Gaussian remain analysis-only
until their existing calculators emit a complete deterministic trade plan.

## Requirement-by-requirement implementation report

1. Existing strategy paths discovered: the stock screener, BMSB/Gaussian
   monitor functions, and `run_entry_scan`/Gerchik signal details were traced.
2. Exact source modules reused: `dashboard_react.market_screener`, the
   existing dashboard monitor calculators, `src.strategy.strategy_router`, and
   `src.jobs.session_utils`.
3. Strategy Source Registry: `StrategySourceRegistry` provides four registered
   sources plus the synthetic `all` selection.
4. Normalized snapshot model: `StrategySnapshot` stores source, scan identity,
   signal state, plan values, timestamps, direction, and provenance metadata.
5. Manual controller: the API persists one bounded run and the worker drains
   the latest completed cached snapshot.
6. Auto controller: the worker callback schedules the selected source after a
   completed scan and uses the existing `AutonomousGerchikAgent`.
7. Exact trigger: there is no new 30-minute timer; the trigger is the return
   from the existing `run_entry_scan` iteration. Current cadence is the
   existing 5/10/15-minute `get_scan_interval` schedule.
8. Stock Screener mapping: existing screener fields map to a complete
   executable snapshot only when entry, stop, and target are present.
9. BMSB mapping: existing BMSB output is reused and classified analysis-only
   unless a future deterministic plan is added to that source.
10. Gaussian mapping: existing Gaussian output is reused and classified
    analysis-only under the same rule.
11. Gerchik Router mapping: accepted `signal_details` values are normalized
    without recalculating the router’s entry/stop/target math.
12. ALL behavior: source evidence is grouped by symbol, with one selected
    decision snapshot and all source records retained as provenance.
13. Executable versus analysis-only: allowed action menus and
    `execution_eligible` prevent analysis-only sources from entering.
14. Decision Lab UI: registry-backed source select, MANUAL/AUTO controls,
    `RUN ANALYSIS`, latest scan/run state, and separate diagnostic/worker
    provider labels are present.
15. Persistence: control, completed snapshots, and run history use runtime JSON
    files with atomic replacement and sidecar locking.
16. Idempotency: AUTO is keyed by `(strategy_selection, scan_id)`; existing
    candidate and execution reservation safeguards remain in the agent path.
17. Audit/provenance: source, scan, bar, signal, candidate class, and source
    evidence travel in the snapshot/candidate metadata and existing audit rows.
18. Tests: strategy source/control/controller/API tests pass, along with the
    worker/preflight, intraday synchronization, job-flow, provider diagnostic,
    and unified-stack lifecycle tests listed in the handoff.
19. Remaining limitations: BMSB/Gaussian do not currently emit complete trade
    plans; browser UAT was not run because services were not started; the
    repository-wide suite has an unrelated existing Flex statement failure
    when a local TWS report is discovered despite disabled Flex configuration.
