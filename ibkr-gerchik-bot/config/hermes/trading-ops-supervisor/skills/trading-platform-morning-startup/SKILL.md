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
