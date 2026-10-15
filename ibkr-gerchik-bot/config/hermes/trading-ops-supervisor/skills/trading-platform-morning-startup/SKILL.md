---
name: trading-platform-morning-startup
description: Run the deterministic repeat-safe Trading Morning Startup workflow and report readiness.
---

Use a two-phase workflow. The default phase is read-only and must run the
existing operational health check first:

```powershell
$repo = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\ibkr-gerchik-bot'
$python = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\.venv\Scripts\python.exe'
Push-Location -LiteralPath $repo
try { & $python -B (Join-Path $repo 'tools\trading_platform_ops.py') health } finally { Pop-Location }
```

Inspect the structured JSON, especially
`services.execute_worker.operational_state`. If Execute Worker is `MISSING` or
`STOPPED`, run this read-only preflight before making any startup recommendation:

```powershell
$repo = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\ibkr-gerchik-bot'
$python = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\.venv\Scripts\python.exe'
Push-Location -LiteralPath $repo
try { & $python -B (Join-Path $repo 'tools\trading_platform_ops.py') execute-worker-preflight } finally { Pop-Location }
```

Report all of these preflight fields without paraphrasing them away:
`verdict`, `reason_code`, `counts.approved_unconsumed`,
`counts.approval_jobs_pending`, `counts.approval_jobs_processing`,
`counts.pending_actionable_requests`,
`counts.processing_actionable_requests`,
`counts.actionable_execution_requests`, and
`evidence.processes`.

Treat `UNKNOWN` and `BLOCKED` as hard stops. Recommend startup only when the
preflight verdict is exactly `SAFE_TO_START`; do not start on a missing verdict,
a successful process command, `SAFE_FOR_PAPER_EXECUTION`, or a zero exit code.
The preflight's `could_submit_ibkr_paper_orders_on_startup` field must remain
visible. It is distinct from platform Paper readiness.

Do not invoke the startup phase or any launcher until the user explicitly
authorizes starting Execute Worker. A general health request, a morning-startup
request, or a statement that IBKR is ready is not sufficient authorization.
After explicit authorization, run the health check and the preflight again
immediately. If the worker is no longer `MISSING`/`STOPPED`, do not start it.
If the second preflight is not exactly `SAFE_TO_START`, stop. Only then invoke
the existing approved startup workflow below. Preserve its existing singleton,
bounded verification, and no-order behavior.

Run exactly this approved local client from the repository for the authorized
startup phase:

```powershell
$repo = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\ibkr-gerchik-bot'
$python = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\.venv\Scripts\python.exe'
Push-Location -LiteralPath $repo
try { & $python -B (Join-Path $repo 'tools\trading_platform_ops.py') morning } finally { Pop-Location }
```

The client detects logical singleton processes before starting anything, starts
only missing approved services, uses bounded verification and one startup retry
for a clearly failed startup, and checks APIs/heartbeats,
detects duplicates, verifies IBKR using the existing adapter, and returns JSON
followed by a human-readable report. Repeat runs must not launch healthy
services again. Preserve every `ACTION_REQUIRED`, `DUPLICATE_PROCESS`, and
`SAFETY_CONFIGURATION_MISMATCH` result. Never call an order, approval, scanner
refresh, Radar refresh, or notification integration. If TWS needs authentication,
stop at `WAITING_FOR_IBKR`; after manual login use the readiness-recheck skill.

Use `operational_state` for exact service classification and preserve `status`
only as the backward-compatible label. A zero exit code is not readiness; only
the authoritative `overall_status` can support `READY_FOR_MARKET_OPEN`.

Never invoke an order, approval, scanner refresh, Radar refresh, or notification
integration from this skill. Preserve the startup action counters and report in
this concise shape:

```text
TRADING PLATFORM - MORNING STARTUP

Overall: [overall_status]
Broker: [status/account readiness]
Market Data: [status/freshness]
Scanner MCP: [status]
Trading Bot MCP: [status]
Dashboard: [status]
Other Services: [summary]
Safety: [paper/live/safety status]
Trading Actions: orders_submitted=0, approvals_created=0, execution_intents_created=0

Issues:
[Only unresolved conditions]

Required Action:
[Specific operator action or Codex escalation]
```

When the preflight was required, include a separate `Execute Worker Preflight`
section containing the exact verdict, reason code, counts, process state, and
whether startup could immediately submit IBKR Paper orders.
