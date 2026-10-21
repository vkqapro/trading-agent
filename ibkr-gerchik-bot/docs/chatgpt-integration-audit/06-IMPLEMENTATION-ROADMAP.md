# Implementation Roadmap

This roadmap is a proposal only. No implementation began as part of the Phase 1 audit.

## Priority 0: Contract and safety foundation

**Effort:** 2-4 engineering days.

Define a versioned read-only adapter over existing APIs/MCP. Add explicit schemas for scan identity, Radar context, lifecycle status, planned versus actual prices, freshness, and operation counters. Add redaction and route/tool allowlists.

**Acceptance:** GET-only adapter tests pass; mutation routes are rejected; unknown or stale scan IDs fail closed; account identifiers and secrets are absent from responses.

**Rollback:** Disable the adapter and return to current Hermes/API consumers.

## Priority 1: Parallel Daily Trader and Analyst reports

**Effort:** 3-6 engineering days.

Generate ChatGPT-readable reports from persisted scans, Radar, journals, and reconciliation records. Run them beside Hermes without changing scheduling or execution.

**Acceptance:** Exact scan IDs and timestamps reconcile; candidate counts and status counts match; missing data is surfaced; no fabricated P&L, fill, price, or strategy attribution.

**Rollback:** Stop publishing the parallel reports; preserve source artifacts.

## Priority 2: Pre-Trade review shadow mode

**Effort:** 3-5 engineering days.

Send the same persisted candidate to the existing reviewer and the ChatGPT report generator. Compare risk fields, invalidation conditions, data quality, direction conflict, and recommendation wording. Do not create approvals.

**Acceptance:** ChatGPT cannot change candidate identity, direction, stop, target, quantity, Risk Gate result, or approval status. Review differences are logged for human comparison.

**Rollback:** Disable ChatGPT review output; retain the existing reviewer.

## Priority 3: Operational reporting and scheduled summaries

**Effort:** 2-4 engineering days.

Add scheduled, quiet-unless-actionable health summaries using the existing supervisor evidence. Keep local continuous monitoring and recovery.

**Acceptance:** Reports distinguish healthy, stale, missing, broker-waiting, and action-required states; they include actions taken and zero-order evidence; no scheduler or service restart is initiated by the report path.

## Priority 4: Paper validation

**Effort:** 1-2 weeks of controlled observation, plus test work.

Validate the complete local path with the existing paper account, including worker startup, scanner identity, risk gate, bracket/stop handling, broker reconciliation, restart recovery, duplicate prevention, and stale-data blocking.

**Acceptance:** A pre-approved paper test plan demonstrates deterministic stop protection, no duplicate submissions, correct recovery after interruption, matching local/broker state, and complete audit records. ChatGPT is not a required dependency for safety.

**Rollback:** Route all analysis and human decisions back to Hermes/Telegram; leave execution code unchanged.

## Priority 5: Optional approval-channel adapter

**Effort:** 1-2 weeks after paper evidence.

Implement only if a human wants ChatGPT as an approval surface. It must write to the existing approval state machine, require explicit account/environment selection, revalidate immediately before execution, and preserve TTL and delivery reconciliation.

**Acceptance:** Review is never approval; approval is never execution; rejected, expired, stale, ambiguous, and broker-uncertain states are terminal and auditable.

## Live trading prerequisites

Live trading is outside this audit. Before any live consideration, require explicit account/environment separation, independent paper evidence, complete monitoring and recovery, tested protective stops, operational ownership, secret handling, kill-switch validation, broker reconciliation, and a human-approved change window. No ChatGPT capability should weaken those prerequisites.

## Simplest reliable target

Keep one local deterministic trading platform and add one read-only ChatGPT adapter. Let ChatGPT explain and coordinate evidence. Keep the resident collector, scanner, Radar persistence, Risk Gate, OrderManager, IBKR Gateway, protective-stop monitoring, reconciliation, supervisor, and rollback path local. Reduce Hermes to notifications and specialist fallback only after parallel evidence proves equivalence.
