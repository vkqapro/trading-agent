---
name: trading-platform-health
description: Read-only health check for the local trading platform; never starts or restarts services.
---

Run the approved client:

```powershell
$repo = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\ibkr-gerchik-bot'
$python = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\.venv\Scripts\python.exe'
Push-Location -LiteralPath $repo
try { & $python -B (Join-Path $repo 'tools\trading_platform_ops.py') health } finally { Pop-Location }
```

Report the structured result. This skill is strictly read-only: do not invoke
the morning command, individual launchers, broker order APIs, approval APIs, or
scanner/Radar refresh endpoints.
