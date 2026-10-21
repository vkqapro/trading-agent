# Target Architecture

## Recommendation

Adopt a thin ChatGPT analytical and operator layer around the existing deterministic platform. Do not make ChatGPT the trading engine. The simplest reliable target is:

`ChatGPT Work/Codex -> read-only local adapter -> persisted scanner/Radar/reports -> deterministic Python Risk Gate and OrderManager -> IBKR Gateway -> local monitoring/reconciliation`

The existing Hermes profiles can become optional presentation and notification clients after equivalent outputs are available through the local adapter. They should not be removed as part of Phase 1.

## Component boundaries

### ChatGPT Work / Codex

Responsibilities:

- explain current health, scan identity, Radar context, and candidate evidence;
- compare persisted results and generate Daily Trader, Pre-Trade, and post-trade narratives;
- request an explicit scan or review only through a separately authorized workflow;
- preserve exact tickers, scan IDs, timestamps, planned versus actual entry, lifecycle versus scanner status, and provenance;
- state incomplete or blocked results rather than filling gaps with model inference.

Non-responsibilities:

- strategy computation;
- risk-limit calculation or override;
- order construction or submission;
- protective-stop maintenance;
- continuous monitoring;
- broker reconciliation.

### Read-only local adapter

Expose a small, versioned API or MCP namespace with GET-equivalent operations:

- `health` and `readiness`;
- `latest_completed_scan` and `scan_status(scan_id)`;
- `persisted_setups(side)`;
- `radar(top, symbol, flips)`;
- `candidate(symbol, source_scan_id)`;
- `pre_trade_review(candidate_id)` as a report, not an approval;
- `trades`, `positions`, `orders`, and `reports` through read-only local projections;
- `operations_evidence` with `actions_taken`, zero-order counters, and blocker codes.

The adapter must reject POST-like mutations, unknown scan IDs, stale substitutions, and requests that mix Radar direction with scanner execution status. It should redact account identifiers and secrets at the boundary.

### Deterministic local core

Keep the current Python modules authoritative:

- market data collector and IBKR connection;
- LP1, LP2, PRB1, PRB2 strategy logic;
- Radar computation and persistence;
- Risk Gate, stop/target, position-size, spread, and session checks;
- `OrderManager` and existing IBKR bracket logic;
- broker execution journals and reconciliation;
- continuous worker and protective-stop loops;
- operational supervisor and Windows task ownership.

### Optional human approval channel

Telegram may remain a notification and approval transport. A future ChatGPT approval workflow must write into the same canonical approval state machine and must not create a parallel order-intent store. Any approval must be revalidated against current broker quote, setup identity, risk, funds, stop, target, and account state immediately before local execution.

## Data-flow contract

1. Local collector writes bars and health evidence.
2. Scanner computes the authoritative setup and persists scan identity.
3. Radar adds context and conflict metadata; it never upgrades `NO_SETUP` to an executable signal.
4. ChatGPT reads completed persisted artifacts and produces a sourced explanation.
5. Local pre-trade review and Risk Gate validate the candidate.
6. Only the existing local execution boundary can prepare or submit a paper/live bracket.
7. Local monitoring confirms broker state and protective stops without an LLM dependency.
8. Reconciliation and reports provide the post-trade source of truth.

## Safety controls

- Read-only adapter by default; separate write-capable namespace if ever needed.
- Explicit account/environment identity check; paper and live are never inferred from a friendly label.
- Fail closed on stale data, missing source scan ID, mismatched candidate identity, incomplete risk fields, ambiguous intrabar state, broker uncertainty, or missing protective stop.
- ChatGPT responses must show `orders_submitted`, `approvals_created`, and `execution_intents_created`.
- Keep live trading disabled until independent paper validation and explicit live prerequisites pass.
- Preserve Telegram/Hermes during migration so rollback is a routing change, not an execution rewrite.
- Log prompt/request identity, source artifact IDs, timestamps, returned fields, and redaction decisions without storing secrets.

## Operating model

Interactive ChatGPT sessions can perform operator checks and analysis. They do not replace a resident Windows service. Scheduled reports can be initiated by Codex automations, but continuous collection, execution, stop management, and recovery must remain local. The local supervisor remains the authority for service readiness and startup ownership.
