# Unified Dashboard Stack Implementation Report

## 1. Existing and new launcher behavior

`run_react_dashboard.cmd` remains the single operator startup command. It now
launches the existing market-data collector, execute-request worker, crypto
worker, the new `run_autonomous_stock_worker.cmd` wrapper, and then the FastAPI
dashboard. Configuration is still read from the normal `.env` source at Python
startup; changing `.env` requires a stack restart. No `.env` value was changed
by this implementation.

`stop_react_dashboard.cmd` delegates to an ownership-aware PowerShell stop
script. It signals the exact autonomous worker, waits for graceful exit, then
stops execute, market-data, crypto, and dashboard processes, and verifies that
the owned process patterns and port 8550 are gone. It does not target ngrok,
TWS/IB Gateway, browsers, or unrelated Python processes.

## 2. Worker architecture and entry point

The new entry point is `src/jobs/autonomous_stock_worker.py`, launched by
`run_autonomous_stock_worker.cmd`. It is a resident supervisor, not a new
market-scanning loop. It calls `run_job_with_context("premarket")` once per
trading date, then calls the existing open and intraday jobs once at their
session transitions and waits for their existing internal loops to finish.
The existing jobs now accept a stop event and an entry-permission flag so a
provider outage can disable new entries without disabling deterministic
position protection.

## 3. Startup order

The exact startup order in the React launcher is:

1. Market data collector
2. Execute request worker
3. Crypto worker
4. Autonomous Stock / LLM worker
5. FastAPI dashboard on `127.0.0.1:8550`

The autonomous worker is deliberately separate from the file-backed execute
request queue. Existing manual/dashboard requests continue to use the execute
worker; autonomous LLM entries use the existing decision/OrderManager path.

## 4. Heartbeat, state, and duplicate prevention

The worker writes `memory/runtime/autonomous_stock_worker.json` atomically with
`service`, `pid`, `started_at`, `last_heartbeat`, `state`, `current_session`,
`llm_mode`, `provider_status`, `broker_status`,
`autonomous_entry_enabled`, `blocked_reason`, and `last_error`. It never writes
API keys, credentials, full account IDs, or account numbers. States are
`STARTING`, `READY`, `MARKET_CLOSED`, `BLOCKED`, `DEGRADED`, `STOPPING`, and
`STOPPED` as represented by the worker/dashboard.

An exclusive PID-bearing lock plus a PID file allows only one autonomous worker
and recovers only dead owners. The stop script verifies the full command line
before creating the worker stop signal and never kills a stale PID blindly.

## 5. Closed-market behavior

The worker remains resident overnight, on weekends, and after the configured
intraday end. It writes heartbeats and waits for the next weekday phase rather
than exiting. The current shared session helpers are weekday/time based; no
exchange holiday calendar is currently present, so exchange holidays remain an
operator verification limitation.

## 6. Mode-aware preflight and retry behavior

`src/jobs/autonomous_stock_preflight.py` is shared by the worker and the
read-only `scripts/check_llm_ibkr_paper_ready.py` checks. It validates the mode,
Decision Lab schema, provider construction and stored provider health, broker
connectivity, Paper identity/allowlist for `ibkr_paper_autonomous`, readable
positions/open orders/executions, and unresolved reservation state. The Paper
broker path also checks dedicated permission, `PAPER_TRADING=true`,
`DRY_RUN_MODE=false`, and disabled live permissions.

Failure leaves the worker alive in `BLOCKED` or `DEGRADED`; it does not enter a
new autonomous position. A provider-only failure keeps deterministic protection
running with new entries disabled. Recovery is rechecked before resuming.
The worker does not automatically run the provider diagnostic request; the
existing Test LLM Connection action and explicit preflight command remain
non-trading health checks.

## 7. Dashboard status surface

`/api/services` now exposes an `Autonomous Stock Worker` row and a safe
`autonomous_stock_worker` evidence object. The UI distinguishes a live worker
heartbeat from entry permission and displays session (`MARKET CLOSED`,
`PREMARKET`, `OPEN`, or `INTRADAY`), LLM mode, provider status, broker status,
and whether new entries are enabled or blocked. The dashboard remains
read-only; no mode-switch or order endpoint was added.

## 8. Stop, Hard Reset, and open-position restart

Graceful stop requests stop new candidates/LLM work, lets bounded existing job
sleeps observe the stop event, persists `STOPPED`, releases the lock, and
disconnects the broker through existing job cleanup. It does not flatten a
position or cancel valid protective brackets. Hard Reset uses stop then the
normal complete-stack launcher. On restart, the existing connected job path
reads broker positions/open orders and synchronizes tracked state before entry
scanning; autonomous reservation/reconciliation gates prevent a duplicate
entry for an already-owned/open Paper position.

## 9. Verification performed

- New worker lifecycle tests: closed-market residency, phase sequencing,
  duplicate phase prevention, blocked preflight, provider-only degradation,
  stop cleanup, duplicate lock ownership, and all five mode values.
- Existing autonomous/decision model tests: 17 passed.
- New worker tests: 7 passed.
- Python compile checks passed for `src`, `dashboard_react`, and `scripts`.
- Launcher and stop scripts were inspected statically for requested service
  order and ownership patterns.
- The real stack was not started, no `.env` was modified, and no broker order
  or provider request was issued.

## 10. Limitations and controlled-validation status

The worker's live TWS/provider/account preflight and the Windows launcher stop
sequence were not executed against a running stack in this implementation
pass. The weekday/time session calendar has no exchange holiday data. The
current broad repository order-flow baseline also contains unrelated existing
stub/signature failures outside this lifecycle change. Therefore this change
is structurally and unit-tested, but it is not evidence of completed live
Paper validation.

**UNIFIED BOT STACK: READY WITH CONDITIONS**

## 11. Operator workflow

1. Confirm TWS/Gateway is the intended Paper environment and `.env` is already
   configured; do not set ad-hoc `$env:` overrides.
2. Run `run_react_dashboard.cmd` once.
3. In the dashboard, confirm `Autonomous Stock Worker` has a fresh heartbeat,
   the expected session, Paper broker verification, provider health, and
   `new entries enabled` only when all gates are green.
4. Use `scripts/check_llm_ibkr_paper_ready.py` for explicit read-only evidence;
   add `--check-broker --check-provider` when those checks are authorized.
5. Stop with `stop_react_dashboard.cmd --no-pause` or use Hard Reset. Hard Reset
   preserves existing protective brackets and requires restart reconciliation
   before any new autonomous Paper entry.
