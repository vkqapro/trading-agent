---
name: trading-platform-readiness-recheck
description: Recheck the already-started platform after the user completes TWS or IB Gateway login.
---

Run the repository-scoped deterministic client with `readiness-recheck` from the repository root. This is verification-only and must not restart healthy services or attempt broker authentication.

```powershell
$repo = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\ibkr-gerchik-bot'
$python = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\.venv\Scripts\python.exe'
Push-Location -LiteralPath $repo
try { & $python -B (Join-Path $repo 'tools\trading_platform_ops.py') readiness-recheck } finally { Pop-Location }
```

Report `READY_FOR_MARKET_OPEN` only when the database, required services/APIs, verified Paper account, and safety configuration all pass. Preserve `ACTION_REQUIRED`, `WAITING_FOR_IBKR`, duplicate, or unavailable results. Prove `orders_submitted=0`, `approvals_created=0`, and `execution_intents_created=0`.
