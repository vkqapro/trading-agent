---
name: pre-trade-review
description: Legacy exact single-candidate review through the read-only Daily Trader API; do not use for natural-language latest, all-candidate, or symbol-only requests.
---

# Legacy Single-candidate Review

This is not the natural-language dispatcher for `Review latest scan`,
`Review all candidates`, or `Review SYMBOL`. Those requests must use the
`pre-trade-review-candidate` skill and the persisted latest-scan batch path.
In particular, never resolve a symbol-only request through this skill's
single-symbol endpoint and never ask the user to choose an ordinal for it.

Use this skill only when the caller explicitly requests the legacy exact
single-symbol API behavior.

For `Review GSHD`, call the approved client from the repository using the
loopback API only:

```powershell
$repo = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\ibkr-gerchik-bot'
$python = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\.venv\Scripts\python.exe'
$symbol = 'GSHD'
Push-Location -LiteralPath $repo
try { & $python -B (Join-Path $repo 'tools\pre_trade_reviewer_api.py') symbol $symbol } finally { Pop-Location }
```

Replace only `$symbol`. Do not search source files, import scanner modules, or
call broker/approval endpoints. The client reuses only
`GET /api/daily-trader/symbol/{symbol}` and applies deterministic review rules.

Terminal lifecycle `INVALIDATED`, `EXPIRED`, `AMBIGUOUS_INTRABAR_ORDER`, and
`NO_SETUP`, failed read-only safety, or missing required authoritative setup
fields are `NOT_ELIGIBLE_FOR_APPROVAL`. Active `PENDING_ENTRY`,
`ENTRY_TRIGGERED`, or authoritative `READY` are reviewable. Direction conflict,
partial data quality, and closed/unknown market status are warnings and yield
`REVIEW_WITH_CAUTION` unless a hard rule applies. State that PENDING_ENTRY has
not triggered. Never treat Radar alone as a setup.

Present facts, warnings, review state, and a concise summary. Never say Buy,
Sell, Execute, Approve, or Reject. End with `READ-ONLY`, `No approval created.`,
and `No order submitted.`
