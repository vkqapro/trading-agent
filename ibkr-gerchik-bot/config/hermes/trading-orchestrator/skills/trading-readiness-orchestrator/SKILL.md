---
name: trading-readiness-orchestrator
description: Coordinate a read-only Trading Ops, Daily Trader, and exact-scan Pre-Trade Reviewer readiness workflow through Hermes A2A.
---

# Trading Readiness Orchestrator

Use Hermes A2A tools only. Before calling a specialist, use `a2a_list` to
confirm that the required named peer is configured, and use `a2a_discover` when
the peer URL or advertised capability is not already known. If the peer is not
configured or reachable, stop with an explicit unavailable-peer result. Never
invent a peer URL, port, token, or profile route.

## Workflow selection

- Market-status-only request: call `trading-ops-supervisor` for observational
  health and call `daily-trader` for persisted market/Radar context. Do not ask
  Daily Trader to start a scan.
- Latest persisted-scan request: call `daily-trader` with an explicit request
  to load `daily-trader-latest-scan`. That skill invokes only
  `cmd.exe /d /c C:\\Users\\Vitaly\\Documents\\GitHub\\trading-agent\\.venv\\Scripts\\python.exe C:\\Users\\Vitaly\\Documents\\GitHub\\trading-agent\\ibkr-gerchik-bot\\tools\\daily_trader_api.py latest --format report`
  and returns the exact completed `scan_id`. Do not route this request through
  `daily-trader-scan-status` because it requires a caller-supplied ID, and do
  not ask the peer to inspect repository files or search for scan storage.
- Explicit scan/opportunity request: call `trading-ops-supervisor` first,
  require a successful observational preflight, then call `daily-trader` for
  the authorized scan workflow. Do not issue a second scan if one is running
  or if the first response is delayed.

If Ops reports a critical infrastructure failure, stop before scanning or
reviewing and report the blocker.

Issue local A2A calls sequentially, one `a2a_call` invocation per tool call.
Do not batch multiple local tools through the single-entry `tool_call` bridge.

## Deferred A2A tool invocation contract

Hermes may expose A2A tools through the deferred local-tool bridge. When
`a2a_call` is not already directly listed, use this exact sequence for each
specialist call:

1. Call `tool_search` with a query such as `a2a_call send task agent`.
2. Use the exact deferred name returned by the search: `a2a_call`.
3. Call `tool_describe` with `{"names":["a2a_call"]}` before invoking it.
4. Call `tool_call` with exactly one local entry, using the peer as an argument:

```json
{"calls":[{"name":"a2a_call","arguments":{"agent":"trading-ops-supervisor","message":"<read-only task>"}}]}
```

Wait for that response before issuing the next specialist call. Replace only
the `agent` and `message` values for Daily Trader and Pre-Trade Reviewer.
Never invoke `trading-ops-supervisor`, `daily-trader`, or
`pre-trade-reviewer` as the `tool_call` entry name: those are A2A argument
values, not deferred tool names. Never place two `a2a_call` entries in one
local `tool_call`; only connector tools may use the bridge batch form.
If the bridge rejects a call, stop and report the failure rather than
advancing the workflow or substituting a direct API call.

## A2A handoff contracts

Ask each peer to return a structured JSON object when possible. Natural-language
text is accepted only when all required fields can be verified; otherwise the
workflow is incomplete.

Ops -> Orchestrator must contain:

```json
{"status":"HEALTHY","component_health":{},"broker_readiness":{},"warnings":[],"timestamp":"..."}
```

Daily Trader -> Orchestrator must contain:

```json
{"scan_id":"<full UUID>","scan_status":"COMPLETED","radar_as_of":"...","scan_timestamp":"...","scanner":{"requested":0,"completed":0,"failed":0},"active":[],"terminal":[],"warnings":[]}
```

Call the configured `pre-trade-reviewer` peer for the exact-scan review.
Orchestrator -> Reviewer must contain:

```json
{"scan_id":"<same full UUID>","review_scope":"all_active","include_terminal":true,"read_only":true}
```

Reviewer -> Orchestrator must contain:

```json
{"scan_id":"<same full UUID>","active_reviews":[],"terminal_reviews":[],"warnings":[],"orders_submitted":0,"approvals_created":0,"execution_intents_created":0}
```

The Reviewer request must name the exact scan UUID and must explicitly say not
to use latest-scan lookup, symbol review, a scanner, Radar refresh, or POST
route. Preserve every candidate identity as
`scan_id + section + symbol + signal_ordinal`; preserve same-symbol tiers and
report terminal/invalidated candidates separately.

## Reconciliation

Before producing a readiness report, verify:

1. Ops is healthy and Paper broker readiness is observationally confirmed.
2. Daily Trader returned one full scan UUID with `scan_status=COMPLETED`.
3. The scan is persisted, its result sections are present, and freshness is
   explicit rather than inferred from current time.
4. Reviewer `scan_id` exactly equals Daily Trader `scan_id`.
5. Every active candidate is represented once, terminal candidates are separate,
   and counts reconcile. Do not deduplicate same-symbol tiers.
6. Lifecycle, entry, stop, target, risk, Radar context, warnings, and direction
   conflicts are copied from authoritative replies without recalculation.
7. All side-effect counters are zero.

Any mismatch yields `INCOMPLETE` or `BLOCKED`; never fabricate success or omit
the conflicting record. Explain the distinction between system readiness,
review eligibility, and execution authorization.

## Final report

Include Ops health, Paper broker readiness, Radar context, scan ID and
timestamps, requested/completed/failed counts, active LONG/SHORT candidates,
terminal candidates, Reviewer classifications, data-quality/direction-conflict
warnings, reconciliation status, and:

```text
Execution status: read-only; no orders, approvals, or execution intents created.
```

