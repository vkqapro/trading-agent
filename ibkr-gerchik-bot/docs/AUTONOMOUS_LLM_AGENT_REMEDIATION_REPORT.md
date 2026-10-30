# Autonomous LLM Gerchik Agent — Safety Remediation Report

Date: 2026-09-26  
Scope: findings F-CRIT-01..03, F-HIGH-01..06, and F-MED-01..06 from `AUTONOMOUS_LLM_AGENT_ADVERSARIAL_REVIEW.md`.

## Executive result

The autonomous path now has durable execution reservations, explicit pre-submit intent, deterministic order fingerprints, broker order references, immediate Live broker/quote refresh, bounded provider work, stable setup identity requirements, fail-closed audit branches, explicit News isolation, Paper recovery, and an independent Paper protection cycle. The default remains disabled: `LLM_AGENT_MODE=off` and `ALLOW_LLM_LIVE_TRADING=false`.

No Live mode was enabled and no broker order was submitted during this remediation. The Live path is hardened structurally but remains disabled and requires separately authorized operational validation.

## Findings and disposition

### F-CRIT-01 — No atomic candidate claim or execution reservation

- Finding: A read-only active-execution check allowed concurrent callers to pass before either execution was recorded.
- Fix: Added `DecisionAudit.execution_reservations`, a mode/agent/candidate/account logical identity with a SQLite primary key, `BEGIN IMMEDIATE`, and a durable state machine. Paper and Live reserve before model/risk execution and return an idempotent skip on a duplicate claim.
- Files changed: `src/decision/audit.py`, `src/decision/agent.py`, `src/decision/identity.py`.
- Tests: `test_sqlite_reservation_allows_exactly_one_concurrent_claim`, `test_reservation_namespace_does_not_cross_shadow_paper_live`, existing `test_same_candidate_cannot_open_twice`.
- Residual risk: SQLite protects the shared audit database; external callers that bypass the autonomous reservation or use a different database are outside this control.
- Status: **FIXED**.

### F-CRIT-02 — Broker acceptance can precede durable execution identity

- Finding: Broker acceptance could occur before a durable local execution identity, leaving a crash window that could permit replay.
- Fix: The autonomous Live path persists a reservation, candidate, decision ID, order fingerprint, and `READY_TO_SUBMIT`/`SUBMITTING` state before calling `OrderManager`. It carries a deterministic broker `orderRef`; uncertain responses become `RECONCILIATION_REQUIRED` and are never automatically retried.
- Files changed: `src/decision/audit.py`, `src/decision/agent.py`, `src/decision/identity.py`, `src/execution/order_manager.py`, `src/brokers/ibkr.py`.
- Tests: `test_live_reconciliation_marks_uncertain_submission_without_retry`, `test_order_manager_requires_fresh_autonomous_quote_and_preserves_reference`.
- Residual risk: The broker and SQLite cannot participate in one distributed transaction. Recovery therefore depends on broker identity/order/execution visibility and explicit reconciliation before any future submission.
- Status: **PARTIALLY FIXED** — the crash window is made recoverable and non-retrying, not transactionally eliminated.

### F-CRIT-03 — No authoritative broker reconciliation at the autonomous execution boundary

- Finding: The prior context could assert `account_synced=True` without a final authoritative broker read.
- Fix: Live startup reconciles incomplete reservations. Immediately before an autonomous candidate is risk-authorized, the agent fetches broker account summary, positions, open orders, and a fresh quote; unknown account, existing symbol position, existing symbol order, disconnected broker, or unknown quote age vetoes the action.
- Files changed: `src/decision/agent.py`, `src/execution/order_manager.py`, `src/data/market_data.py`.
- Tests: `test_live_reconciliation_marks_uncertain_submission_without_retry`, `test_live_configuration_requires_verified_allowlisted_account`, quote guard tests.
- Residual risk: A broker can change after the final read and before acceptance; the broker order reference and no-retry reconciliation state are the remaining containment controls.
- Status: **FIXED** for the autonomous authorization boundary.

### F-HIGH-01 — No post-LLM quote and risk revalidation

- Finding: The provider could respond after the original quote/risk context became stale.
- Fix: Live entry refreshes broker state and quote after the provider response. `OrderManager` performs a second autonomous quote guard for known age, chase distance, spread, and valid price before the broker call. Missing quote timestamp is unknown and vetoes.
- Files changed: `src/decision/agent.py`, `src/decision/market.py`, `src/execution/order_manager.py`, `src/data/market_data.py`.
- Tests: `test_missing_quote_age_is_unknown`, `test_order_manager_requires_fresh_autonomous_quote_and_preserves_reference`, `test_autonomous_guard_rejects_unknown_stale_and_wide_quotes`.
- Residual risk: There is no atomic quote/exchange transaction; fast market movement remains an inherent execution risk.
- Status: **FIXED**.

### F-HIGH-02 — Unknown account identity can bypass the configured allowlist

- Finding: A missing account identifier could avoid the allowlist comparison.
- Fix: Live configuration validation requires a non-empty verified broker account ID and a non-empty allowlist; missing or non-allowlisted identities fail closed. The agent derives/validates the account through broker account summary before Live processing.
- Files changed: `src/config.py`, `src/decision/agent.py`.
- Tests: `test_live_configuration_requires_verified_allowlisted_account`.
- Residual risk: Correctness depends on the broker adapter returning trustworthy account summary records; adapter failure results in a veto.
- Status: **FIXED**.

### F-HIGH-03 — Synchronous provider latency can delay protective management

- Finding: Provider work in the entry scan could delay application-level position management.
- Fix: Enabled autonomous entry is submitted to a per-agent bounded executor with worker/queue limits, deduplication, and candidate expiry. Intraday and open-session jobs invoke deterministic Paper protection independently of the provider. Position reviews are scheduled separately and remain optional.
- Files changed: `src/decision/agent.py`, `src/config.py`, `src/jobs/session_utils.py`, `src/jobs/intraday.py`, `src/jobs/open.py`, `.env.template`.
- Tests: `test_decision_queue_is_bounded_and_shadow_deduplicates_pending_work`, `test_paper_protection_runs_without_provider`.
- Residual risk: The existing legacy Shadow execution path remains separate compatibility behavior and still has its own runtime dependencies; real scheduler timing under production load is not proven here.
- Status: **PARTIALLY FIXED**.

### F-HIGH-04 — Candidate identity changes when setup timestamp provenance is absent

- Finding: Current-time fallback could produce a new candidate ID on every scan.
- Fix: Candidate identity now derives from durable source signal/bar/candle timestamps or source IDs. No current-time value participates in the identity hash. Paper/Live reject explicitly unstable candidates; `created_at` is display/audit metadata only.
- Files changed: `src/decision/candidate_adapter.py`, `src/decision/identity.py`, `src/jobs/session_utils.py`.
- Tests: `test_candidate_identity_has_no_clock_fallback`, `test_candidate_identity_uses_source_bar_timestamp`.
- Residual risk: Upstream producers that do not provide stable provenance are intentionally ineligible for autonomous Paper/Live execution until they do.
- Status: **FIXED**.

### F-HIGH-05 — Audit/storage failures are not uniformly fail-closed

- Finding: A failure while recording evidence could leave execution retry safety ambiguous.
- Fix: Candidate/reservation/snapshot/risk/intent writes occur before mutation or broker submission. Pre-submit persistence errors return `NO_ACTION`; post-submit uncertainty enters reconciliation. Provider error handling separately records provider health and fails closed if the error audit itself cannot be written.
- Files changed: `src/decision/agent.py`, `src/decision/audit.py`.
- Tests: `test_audit_failure_fails_closed_before_paper_mutation`, provider-failure tests in `tests/test_decision_agent.py`.
- Residual risk: A storage failure after an external broker acceptance still requires operator reconciliation; it cannot be reconstructed solely from local state.
- Status: **FIXED** for pre-submit execution authorization; **PARTIALLY FIXED** for post-submit distributed failure.

### F-HIGH-06 — Live ownership provenance is not visibly carried into broker identity

- Finding: Application metadata was not proven to survive into the final broker order state.
- Fix: Added deterministic compact `LLM-...` order references derived from agent, candidate, and decision IDs. The parent, stop, and target IBKR bracket orders receive the reference family, and payload/audit records preserve the same reference.
- Files changed: `src/decision/identity.py`, `src/decision/agent.py`, `src/execution/order_manager.py`, `src/brokers/ibkr.py`.
- Tests: `test_order_manager_requires_fresh_autonomous_quote_and_preserves_reference`.
- Residual risk: Actual IBKR/TWS field visibility still requires a separately authorized non-ordering or paper broker verification.
- Status: **FIXED** structurally; broker-environment visibility remains unverified.

### F-MED-01 — Legacy News remains coupled to the AI path

- Finding: News was outside the requested autonomous dependency boundary but legacy News calls could still affect the overall scan.
- Fix: Added explicit `LLM_AGENT_USE_NEWS=false`. Autonomous provider snapshots omit News fields, autonomous risk receives neutral News values when disabled, and missing/high/exceptional News no longer blocks the autonomous decision submission. Legacy News behavior remains intact for the legacy execution path.
- Files changed: `src/config.py`, `.env.template`, `src/jobs/session_utils.py`, `src/decision/agent.py`.
- Tests: `test_news_is_omitted_from_autonomous_snapshot_when_disabled`, existing News filter tests.
- Residual risk: Shadow intentionally retains legacy execution compatibility; its legacy order path may still apply legacy News rules. This is documented and must not be confused with AI authorization.
- Status: **PARTIALLY FIXED** — autonomous boundary isolated, overall legacy scan behavior retained.

### F-MED-02 — Paper protection/review invocation is not proven in the real periodic job path

- Finding: Deterministic Paper protection existed but was not demonstrably scheduled.
- Fix: Added `run_paper_safety_cycle`, interval gates, fresh quote checks, deterministic stop/target protection, audit position/outcome events, and optional interval-based position-review scheduling. Intraday and open jobs invoke the safety cycle independently of provider work.
- Files changed: `src/decision/agent.py`, `src/decision/portfolio.py`, `src/jobs/intraday.py`, `src/jobs/open.py`, `src/config.py`, `.env.template`.
- Tests: `test_paper_protection_runs_without_provider`.
- Residual risk: A real broker/session scheduler observation and long-running timing test remain operational work.
- Status: **PARTIALLY FIXED**.

### F-MED-03 — Paper portfolio persistence and audit execution recording are not one atomic state transition

- Finding: A crash between Paper portfolio mutation and audit recording could leave two durable stores temporarily inconsistent.
- Fix: Added the same durable reservation state machine used for execution, `PAPER_MUTATING` state, startup recovery, position-to-reservation matching, and safe failure/reconciliation outcomes. Recovery recreates the execution link when the Paper position exists and does not replay the entry.
- Files changed: `src/decision/audit.py`, `src/decision/agent.py`, `src/decision/portfolio.py`.
- Tests: `test_paper_restart_recovers_portfolio_mutation_to_execution_link`.
- Residual risk: JSON portfolio persistence and SQLite audit are still separate storage systems; recovery is compensating, not a single cross-store transaction.
- Status: **PARTIALLY FIXED**.

### F-MED-04 — Candidate/audit namespace is shared across Paper and Live modes

- Finding: Shared candidate/execution identity could suppress or confuse mode transitions.
- Fix: Execution identity is now namespaced by mode, agent, candidate, and account. Execution links and reservations record mode and agent. Paper and Live reservations for the same candidate/account are intentionally distinct.
- Files changed: `src/decision/audit.py`, `src/decision/agent.py`, `src/decision/identity.py`.
- Tests: `test_reservation_namespace_does_not_cross_shadow_paper_live`.
- Residual risk: Candidate rows remain shared for audit provenance; operators must treat execution reservations, not a bare candidate row, as the authorization namespace.
- Status: **FIXED** for execution authorization namespaces.

### F-MED-05 — Shadow requests have no candidate deduplication or bounded backlog policy

- Finding: Repeated scans could create duplicate provider work and unbounded latency pressure.
- Fix: Shadow uses candidate/state deduplication with a configurable cache window; all enabled modes use bounded workers/queue slots, non-blocking overload handling, and candidate expiry. Shadow provider results remain non-controlling.
- Files changed: `src/decision/agent.py`, `src/config.py`, `.env.template`.
- Tests: `test_decision_queue_is_bounded_and_shadow_deduplicates_pending_work`.
- Residual risk: The queue is process-local; multi-process deployments need a deployment-level worker limit and shared observability.
- Status: **FIXED** for a process-local agent.

### F-MED-06 — Complete real-scan integration coverage is missing

- Finding: Component tests did not prove the complete `run_entry_scan` behavior under each mode, News isolation, and legacy Shadow compatibility.
- Fix: Wired the autonomous submission boundary into `run_entry_scan`, preserved deterministic legacy handling for Shadow, added source-bar provenance, and added independent job safety-cycle calls. Component-level remediation tests cover the new boundaries.
- Files changed: `src/jobs/session_utils.py`, `src/jobs/intraday.py`, `src/jobs/open.py`, `tests/test_autonomous_llm_remediation.py`.
- Tests: remediation suite and existing session/order/intraday tests where available; a full real-scan end-to-end proof remains outstanding.
- Residual risk: No claim is made that a live production scheduler, provider, IBKR session, and real watchlist were exercised in this remediation.
- Status: **PARTIALLY FIXED**.

## Files changed for the remediation

- `.env.template`
- `src/config.py`
- `src/brokers/ibkr.py`
- `src/data/market_data.py`
- `src/decision/agent.py`
- `src/decision/audit.py`
- `src/decision/candidate_adapter.py`
- `src/decision/identity.py`
- `src/decision/market.py`
- `src/decision/portfolio.py`
- `src/execution/order_manager.py`
- `src/jobs/session_utils.py`
- `src/jobs/intraday.py`
- `src/jobs/open.py`
- `docs/AUTONOMOUS_LLM_AGENT_ARCHITECTURE.md`
- `docs/AUTONOMOUS_LLM_AGENT_REMEDIATION_REPORT.md`
- `tests/test_autonomous_llm_remediation.py`

## Readiness decision

- Shadow: ready for a controlled non-controlling runtime experiment, with the explicit caveat that the existing legacy Shadow scan can still place legacy orders.
- Paper Autonomous: ready for controlled validation of the isolated Paper decision/portfolio path; long-running scheduler, crash-consistency, and real job-loop evidence remain required before treating it as unattended production operation.
- Live Autonomous: not enabled and not approved for unattended operation. The code has the required reservation/reconciliation/revalidation controls structurally, but no Live order was submitted and broker-environment behavior remains unverified.
