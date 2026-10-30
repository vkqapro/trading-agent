---
name: pre-trade-review-candidate
description: Route latest-scan, symbol-filtered, all-candidate, and exact candidate read-only reviews from persisted Daily Trader scans.
---

# Candidate Review Routing

Apply this intent routing before choosing a client invocation:

- A request containing `scan_id=<UUID>`, `review_scope=all_active`,
  `include_terminal=true`, and `read_only=true` -> exact persisted scan batch
  review. This route has priority over candidate-only and latest-scan routes.
- `Review latest scan` -> latest batch review with no symbol filter.
- `Review all candidates` -> latest batch review with no symbol filter.
- `Review SYMBOL` -> latest batch review filtered by that symbol; review every active matching candidate and report terminal matching candidates separately.
- `Review candidate <full-reference>` -> exact historical candidate review.

Symbol-only requests are never ambiguous candidate selections. Do not ask for an
ordinal merely because a symbol has multiple signals. Do not use the legacy
symbol-review client for any of the first three forms.

Use this skill from a natural-language Telegram request such as:

`Review candidate 84a89696-f592-47d2-aad8-e1a7fffae9ba:active:KDP:1`

For normal daily review, accept:

`Review latest scan`

`Review all candidates`

`Review KDP`

For an explicit scan-scoped request, preserve the exact UUID and invoke only the
exact persisted scan operation. Never use latest-scan lookup for scan-scoped
requests, even when the requested scan is also the latest scan.

Expected scan-scoped request fields:

```text
scan_id=<UUID> review_scope=all_active include_terminal=true read_only=true
```

Invoke:

```powershell
& $python -B (Join-Path $repo 'tools\\pre_trade_reviewer_api.py') scan `
  --scan-id '<UUID>' `
  --scope all_active `
  --include-terminal `
  --format json
```

The JSON response schema is:

```json
{
  "scan_id": "<same full UUID>",
  "scan_status": "COMPLETED",
  "scan_timestamp": "...",
  "radar_as_of": "...",
  "review_scope": "all_active",
  "include_terminal": true,
  "active_reviews": [],
  "terminal_reviews": [],
  "warnings": [],
  "read_only": true,
  "orders_submitted": 0,
  "approvals_created": 0,
  "execution_intents_created": 0
}
```

The exact-scan operation performs only `GET /api/daily-trader/scans/{scan_id}`
and reuses the deterministic Reviewer classifier for every persisted active
candidate plus the separate terminal section. It must not inspect repository
source, create scratch scripts, call symbol or batch endpoints, invoke a
scanner, refresh Radar, or submit any POST request.

The full-identifier form remains supported:

`Review candidate scan_id=<UUID> symbol=<SYMBOL> section=<active|terminal> signal_ordinal=<N>`

Require either the full four-field identifier set or one complete candidate reference. Never shorten the UUID and never substitute the latest scan. `/review-candidate` is not registered as a Telegram slash-command by this change.

For `Review latest scan` or `Review all candidates`, invoke:

```powershell
& $python -B (Join-Path $repo 'tools\pre_trade_reviewer_api.py') latest --format report
```

For `Review KDP`, invoke the symbol-filtered latest batch path:

```powershell
& $python -B (Join-Path $repo 'tools\pre_trade_reviewer_api.py') latest --symbol KDP --format report
```

For a candidate reference, invoke:

```powershell
$repo = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\ibkr-gerchik-bot'
$python = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\.venv\Scripts\python.exe'
& $python -B (Join-Path $repo 'tools\pre_trade_reviewer_api.py') candidate --candidate-ref '<full-UUID>:<active|terminal>:<SYMBOL>:<N>'
```

For explicit identifiers, invoke the repository client as follows:

```powershell
$repo = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\ibkr-gerchik-bot'
$python = 'C:\Users\Vitaly\Documents\GitHub\trading-agent\.venv\Scripts\python.exe'
& $python -B (Join-Path $repo 'tools\pre_trade_reviewer_api.py') candidate `
  --scan-id '<UUID>' `
  --symbol '<SYMBOL>' `
  --section '<active|terminal>' `
  --signal-ordinal <N>
```

The client performs only `GET /api/daily-trader/scans/{scan_id}` and resolves the candidate from that persisted completed payload. Never call the symbol-review endpoint, batch-review endpoint, scanner, Radar symbol scan, or any POST route as a fallback. Never refresh or persist scanner data.

Return the authoritative candidate identity, Reviewer classification, lifecycle status, warnings, Radar context, and read-only safety counters. Preserve the supplied `scan_id`, `section`, `symbol`, and `signal_ordinal`; do not select by rounded prices. Report missing, stale, incomplete, non-completed, not-found, and ambiguous selections explicitly.

`ELIGIBLE_FOR_APPROVAL` is a Reviewer classification only. It is not an approval and does not submit or imply a broker order or execution intent. `ENTRY_TRIGGERED` and `READY` are scanner lifecycle states, not broker execution.
