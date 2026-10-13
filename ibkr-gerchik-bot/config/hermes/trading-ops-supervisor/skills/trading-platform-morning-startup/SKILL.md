---
name: trading-platform-morning-startup
description: Run the deterministic repeat-safe Trading Morning Startup workflow and report readiness.
---

Run exactly this approved local client from the repository:

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

Report in this concise shape:

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
