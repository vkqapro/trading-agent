# Read-Only POC Results

## Scope and timestamp

Tests were performed on 2026-10-20 around 21:00 UTC. All local API calls were GET-only. No scan was started, no approval was created, no order instruction was created, no order was submitted, and no service was restarted.

## Results

| Test | Evidence | Result |
|---|---|---|
| Repository access | Source and graphify output readable; graph query reported 8,432 nodes | VERIFIED |
| Trading Bot MCP health | `GET http://127.0.0.1:8765/health` returned 200, `status=ok`, `mode=data_read_only_approval_state_machine` | VERIFIED |
| Strategy Scanner MCP health | `GET http://127.0.0.1:8766/health` returned 200, `status=ok`, `mode=deterministic_analysis` | VERIFIED |
| Production scanner health | Dashboard endpoint returned 200, scanner available, four production strategies, latest artifact readable, zero orders | VERIFIED with data-quality warning |
| Radar health/data | Endpoint returned 200, Radar as-of `2026-10-20`, 250 current rows; top result showed a Radar breakout with scanner `NO_SETUP` | VERIFIED |
| Daily Trader health | Returned 200, read-only, scanner and Radar available, 250 current Radar rows, zero orders | VERIFIED |
| Pre-Trade Reviewer health | Returned 200, read-only, no approval created, zero orders | VERIFIED |
| Latest completed persisted scan | Returned a completed scan from `2026-10-15`, 30 processed/persisted results, zero orders/approvals/intents | VERIFIED; historical relative to audit date |
| Persisted setup summary | Returned 28 persisted scanner results, 28 unique symbols, 2 active candidates, 2 entry-triggered, 1 invalidated, 26 no-setup, 0 errors | VERIFIED |
| Operations status | Paper broker verified, local MCP/dashboard/Radar/scanner/market data healthy, execute worker missing/stopped, zero actions | VERIFIED; overall `ACTION_REQUIRED` |
| IBKR connector account read | Two same-named linked accounts were exposed; no account was selected, so no positions/balances/trades/orders were fetched | BLOCKED by account ambiguity and data-scope authorization |

## Concrete source-backed observations

1. `MSCI` demonstrated that Radar context is not an execution signal: Radar was `GREEN` with a bullish breakout, while the scanner result was `NO_SETUP` and had no signals.
2. The persisted setup response kept `scanner_status` and `lifecycle_status` distinct. A setup can carry scanner candidate metadata while its lifecycle is `ENTRY_TRIGGERED`; this is not equivalent to a broker fill.
3. The production scanner's latest artifact was `COMPLETED_WITH_ERRORS`, with one skipped symbol due to missing persisted daily data. ChatGPT must report this warning rather than present a clean scan.
4. Local paper safety was enabled and live trading was disabled. The execute worker was not running, so the platform was not fully ready for paper execution despite the broker and data services being healthy.

## Capability limitations observed

The available IBKR connector tools include account reads, quote/history reads, contract search, and order-instruction creation. The order-instruction contract states that an instruction is not a live order and requires review/submission in IBKR. No connector submit, cancel, or modify tool was available. Account-specific reads were intentionally not called because two links had the same display name and the brief did not identify which account to use.

## Negative evidence

- No live order or paper order submission was attempted.
- No order instruction was created.
- No Telegram approval was created.
- No execution intent was created.
- No fresh scan, Radar refresh, batch scan, or dashboard mutation was invoked.
- No credentials, API keys, tokens, or exact account identifiers are included in this document.

## Blockers and follow-up evidence needed

- Select the intended IBKR linked account explicitly before any connector account read.
- Resolve the stopped execute-worker readiness issue through the existing approved lifecycle owner, outside this read-only phase.
- For full end-to-end acceptance, independently verify paper execution and recovery in a separately authorized test window; this audit did not do so.
