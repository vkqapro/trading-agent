---
name: pre-trade-review-batch
description: Compare existing production candidates through the read-only Daily Trader API.
---

# Batch pre-trade review

For `Review GSHD XYZ ABC`, call the approved client once:

```powershell
$repo = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\ibkr-gerchik-bot'
$python = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\.venv\Scripts\python.exe'
Push-Location -LiteralPath $repo
try { & $python -B (Join-Path $repo 'tools\pre_trade_reviewer_api.py') batch GSHD XYZ ABC } finally { Pop-Location }
```

Use the returned structured reviews to produce a compact comparison table with
symbol, strategy, side, Radar, scanner score, R:R, risk, conflict, data
quality, and review state. Do not automatically add NO_SETUP or terminal rows
unless explicitly requested. Ranking may describe the cleanest structured
setup, never a trade instruction. No approval, rejection, order, broker call,
or notification is allowed.
