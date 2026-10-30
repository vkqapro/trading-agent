# Decision Lab backend decoupling

## Boundary

Decision Lab is an application-layer analysis consumer. The independent
Windows legacy scheduler remains the owner of the stock session lifecycle:
premarket, open, intraday, market-data collection, end-of-day work, and the
associated session locks. Decision Lab does not start, stop, or supervise
those jobs.

The persistent `AutonomousStockWorker` owns Decision Lab run consumption,
provider/preflight evidence, and a sanitized heartbeat. It may continue
processing persisted source data while the market is closed. A market-closed
state blocks new entries but does not erase a valid Paper verification state.

## Existing source data

Decision Lab reads the same current strategy data already used by the existing
dashboard/backend. It does not create a parallel strategy implementation:

- Stock Screener uses the existing persisted-bars screener path (`/api/market-
  screener`, `run_market_screener`, and the shared screener calculations).
- BMSB uses the existing monitor calculator exposed through `/api/strategy`,
  including `_bmsb_scan_symbol` and `_strategy_payload`.
- Gaussian uses the existing monitor calculator exposed through
  `/api/strategy?mode=gaussian`, including `_gaussian_scan_symbol` and
  `_strategy_payload`.
- Gerchik Router reads accepted persisted daily-decision/intraday evidence via
  `dashboard.data_access.load_daily_decisions`. It does not invoke
  `run_entry_scan` to manufacture a result for Decision Lab.

The source adapter layer in `src/decision/strategy_sources.py` normalizes
these results into immutable `StrategySnapshot` records. A current analysis
snapshot is a copy of the data Decision Lab analyzed; its existence does not
claim that Decision Lab owned or executed a legacy scan.

Each persisted current snapshot carries source availability, candidate count,
source timestamp, latest bar timestamp, age, freshness, and a source
fingerprint. A source can therefore be available with zero candidates, while
missing or unusable persisted data is reported as `SOURCE_UNAVAILABLE`.

## Manual flow

`POST /api/decision-lab/run` validates the selected source and materializes a
current immutable source snapshot before creating a `QUEUED` durable run. The
FastAPI request performs no LLM call, broker connection, order operation,
session job, or session-lock acquisition. The persistent worker atomically
claims the run, analyzes it, records the lifecycle, and applies the existing
source-specific safety controls.

BMSB and Gaussian are analysis-only: their LLM analysis may produce WAIT or
REJECT decisions, but ENTER is not execution-eligible and no broker execution
path is added. Stock Screener remains the only source that can be
execution-capable, and only through the existing complete-plan and safety
gates; no direct screener-to-broker path was introduced.

## AUTO flow

AUTO is application-owned. The default interval is 30 minutes and is
configurable with `LLM_AUTO_ANALYSIS_INTERVAL_MINUTES`. The worker reads the
current source snapshot, skips an unchanged source fingerprint with a
documented no-new-data outcome, and schedules analysis only when the cadence
and source data gates permit it. AUTO never launches a legacy session job.

## Runtime scripts and locks

`run_react_dashboard.cmd` starts the React/API and Decision Lab application
components. It is not required to start the legacy scheduler. The controlled
`stop_react_dashboard.cmd` targets the controlled dashboard/application stack;
it does not stop the separately owned legacy scheduler. Scheduled tasks,
client IDs, `market_session.lock`, and `intraday_session.lock` remain outside
this application lifecycle.

The dashboard reads the worker heartbeat and status API only. It does not
connect to IBKR to render Decision Lab, and the heartbeat/API/UI omit the full
broker account ID and secrets.

## Safety and verification boundary

The source snapshot gate remains fail-closed: no current source data means no
queued run. Atomic claim, stale queued recovery, run lifecycle, provider
health, preflight, execution gates, and paper/live protections remain in
their existing layers. This change does not place an order.

The required verification set covers:

- no `run_premarket`, `run_open`, `run_intraday`, or `run_entry_scan` call from
  the Decision Lab worker path;
- no Decision Lab acquisition of `market_session.lock` or
  `intraday_session.lock`;
- current source snapshots for Stock Screener, BMSB, Gaussian, and Gerchik;
- valid zero-candidate source data;
- manual processing while the legacy intraday lock is held;
- AUTO cadence and duplicate-fingerprint suppression;
- fresh, stale, stopped, disconnected, and allowlist-mismatch worker states;
- no account ID exposure and no dashboard IBKR connection.

The final operational status is `READY` only after a positive browser flow
reaches a terminal analysis result beyond `QUEUED`; structural tests alone do
not establish that status.
