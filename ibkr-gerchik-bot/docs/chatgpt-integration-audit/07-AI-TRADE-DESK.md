# AI Trade Desk

## Scope

The AI Trade Desk is a durable candidate-review surface inside the existing dashboard. It imports persisted Daily Trader candidates, accepts AI explanations, recalculates proposal parameters deterministically, and records human review history. It does not replace the scanner, Radar, Risk Gate, approval store, or execution worker.

The feature is paper-only. The UI reports zero orders submitted and zero execution intents created for Trade Desk actions. Live trading remains disabled by the existing runtime controls.

## Lifecycle

`DISCOVERED` is an imported or AI-ingested candidate. `UNDER_REVIEW` means a human is checking source integrity and parameters. `READY` means the candidate is ready for the existing human approval boundary. `INVALIDATED` and `REJECTED` are terminal review outcomes. Later execution states are retained for reconciliation and audit, but are not created by AI ingestion.

The source fields are copied from persisted scanner output. AI may add reasoning, confidence, and model metadata, but cannot change symbol, strategy, direction, prices, scanner status, or lifecycle status during ingestion. A candidate with a terminal lifecycle or a no-scan status is rejected.

## API contract

All responses include the proposal record and read-only counters. The dashboard uses `X-AI-Trade-Desk-Role: human` and `X-AI-Trade-Desk-Actor`. AI ingestion additionally requires `X-AI-Trade-Desk-Role: ai`, `X-AI-Trade-Desk-Actor`, and a server-side `AI_TRADE_DESK_AI_TOKEN` matched by `X-AI-Trade-Desk-Token`.

| Method | Path | Actor | Purpose |
|---|---|---|---|
| GET | `/api/ai-trade-desk/proposals` | read | list proposals and status counts |
| GET | `/api/ai-trade-desk/proposals/{id}` | read | proposal, events, revisions |
| POST | `/api/ai-trade-desk/proposals/import-persisted` | human | idempotently import persisted candidates |
| POST | `/api/ai-trade-desk/proposals/ingest` | AI | publish an explanation against an exact persisted candidate |
| PATCH | `/api/ai-trade-desk/proposals/{id}` | human | recalculate and audit manual parameter edits |
| POST | `/api/ai-trade-desk/proposals/{id}/status` | human | transition the review state |
| POST | `/api/ai-trade-desk/proposals/{id}/reject` | human | record a rejection reason |

The dashboard scan pipeline uses the authoritative `RADAR_SETUPS` selection from
`dashboard_react.server._radar_setup_universe()`. It restricts rows to the latest
Radar `as_of`, then accepts only `CANDIDATE`, `READY`, `PENDING_ENTRY`, or
`ENTRY_TRIGGERED` scanner statuses. Those symbols are passed explicitly to the
Daily Trader adapter; breakout and fresh-flip counts are diagnostic Radar stage
results and are not themselves production strategy candidates. A zero-candidate
GPT review is terminal: it records `SKIPPED_NO_CANDIDATES`, `reviewed_count=0`,
and `published_count=0`, without requiring an AI model call or ingestion token.

## Secure AI ingestion provisioning

`AI_TRADE_DESK_AI_TOKEN` is a server-side shared-secret setting. The existing
configuration loader reads the repository-local `.env` through `src.config` with
`override=False`; the token must be provisioned through the approved local secret
management process into the dashboard process environment or its protected `.env`
source. Do not commit `.env`, put the value in `dashboard_react/index.html`,
browser storage, query strings, or logs. The browser sends no token for normal
human actions. Only the isolated AI review-import request sends the token in the
`X-AI-Trade-Desk-Token` header, and the server rejects missing or mismatched
values. Human review and approval permissions remain separate.

After provisioning, refresh only the dashboard Uvicorn process using the approved
dashboard-only procedure so `src.config` reloads the environment. Do not invoke
`run_react_dashboard.cmd` and do not restart IBKR Gateway, market data, scanners,
Risk Gate, execution workers, or autonomous workers. Confirm the token is present
with a redacted configuration check; never print or record its value.

The store is SQLite-backed in the existing Decision Agent database and uses an idempotency key. Every edit creates a revision row and every create, edit, approval attachment, or state change creates an event row. Updates use the stored revision number to detect stale edits.

## ChatGPT Work operator

The interactive Work/Codex session is the analytical operator. It invokes the
loopback-only `tools/trading_work_operator.py` client for preflight, explicitly
authorized scan submission, persistent status polling, payload retrieval, and
authenticated review publication. It does not start a resident AI service or
call an externally billed model API.

For nonempty review payloads, each candidate includes the exact persisted
`proposal_id`, `proposal_status`, and AI-review status. This eliminates any
model-side proposal-ID inference: the Work session must preserve the supplied
proposal ID together with source scan ID, symbol, and strategy. The local client
validates that identity before sending the token-backed review import and then
checks for the `AI_REVIEW_PUBLISHED` audit event.

The reusable skill is installed in the local Codex skill directory as
`trading-work-operator`. It is an interactive workflow only. A market scan still
requires a separate per-run human authorization, and service recovery requires
separate authorization after the existing operations supervisor identifies the
documented owner and readiness evidence.

## Deterministic calculations

For LONG: `risk/share = entry - stop`, `reward/share = target - entry`. For SHORT the signs are reversed. The store validates positive finite prices and direction-aware stop/target relationships. Whole-share quantity is bounded by cash allocation and maximum risk budget when those controls are provided. It records risk/share, actual risk, potential profit, and notional exposure.

The brief sample `81.08 / 79.92 / 84.27`, 12 shares, and 5% max risk produces `1.16` risk/share, `$13.92` actual risk, and `$38.28` potential profit.

## Approval and rollback

The intended human approval action is guarded by execution-worker freshness and exact persisted source eligibility. It must call the existing `ApprovalStore` and existing approval/execution worker; it must never submit an order directly from the dashboard or AI route. If the worker heartbeat is missing or stale, approval is blocked with a 409 response. No worker restart is performed by this feature.

In the current installed runtime the execution worker heartbeat is stale, so the dashboard deliberately presents approval as gated and does not send an approval request. This keeps the Phase 2 review workflow usable without silently creating a new execution path.

Rollback is limited to removing the AI Trade Desk navigation and routes, leaving the existing scanner, Radar, Decision Lab, approval, and execution code untouched. The proposal tables are additive and can remain for audit; do not delete them while investigating a live or paper execution discrepancy.

## Verification

Run `python -m pytest tests/test_ai_trade_desk.py -q` for deterministic calculations, idempotent persistence, audit revisions, and invalid transitions. Run `python -m py_compile src/decision/ai_trade_desk.py dashboard_react/server.py` for syntax verification. Do not use the Trade Desk test suite to place broker orders.
