# Autonomous LLM Agent — Remediation Re-Review

Review date: 2026-09-26  
Review type: targeted, read-only adversarial re-review  
Allowed repository change: this report only

This review rechecked the original F-CRIT-01..03, F-HIGH-01..06, and
F-MED-01..06 findings against the implementation. It did not repeat the full
architecture audit. No application code, `.env` file, trading configuration,
broker connection, worker, or order-capable process was enabled or modified.
No order was submitted.

## 1. Executive Verdict

The remediation closes the most important Paper/Shadow execution hazards:

- Paper and Live execution identities now have an SQLite reservation state
  machine and a unique logical key.
- Paper does not call `OrderManager` for autonomous entry.
- Shadow AI work is queued without a broker handoff, while the legacy Shadow
  execution path remains separate and order-capable.
- Paper protection is called from the real `intraday` and `open` job paths and
  does not require a provider.
- Pre-submit intent and broker order-reference provenance are persisted before
  the autonomous Live handoff.

The implementation is not a clean closure of every original finding. The
remaining gaps are important for Live but do not prevent a supervised Shadow
experiment or a controlled Paper Autonomous validation if the conditions in
sections 19 and 20 are followed:

1. post-provider Live refresh reads account, positions, open orders, and quote,
   but not executions; execution reconciliation occurs earlier in
   `startup_guard`, not immediately before the final submission;
2. the final `OrderManager` quote guard does not independently reject a
   `quote_status="missing"` record if it contains a plausible price and fresh
   timestamp, although the earlier Live refresh does reject that status;
3. the stock adapter rejects unstable candidates, but `mapping_to_candidate`
   treats any non-empty timestamp string as stable even when it is malformed;
4. News errors and high-risk results no longer veto the autonomous branch, but
   `run_entry_scan` still performs synchronous legacy News calls before the
   autonomous candidate is created;
5. provider-latency isolation is implemented at the entry-scan boundary, but
   there is no timing test proving scheduler behavior under a long-running
   production loop or proving that queued Live startup reconciliation cannot be
   delayed by worker saturation.

## 2. Critical Findings Recheck

| Finding | Status | Code evidence and conclusion |
|---|---|---|
| F-CRIT-01 — no atomic candidate claim / execution reservation | **FIXED** | `src/decision/audit.py::reserve_execution` uses `BEGIN IMMEDIATE`; `execution_reservations.execution_key` is the primary key. The key contains mode, agent, candidate, and account. The new concurrency test produced exactly one successful claim. |
| F-CRIT-02 — broker acceptance before durable execution identity | **PARTIAL** | `src/decision/agent.py::process_candidate` persists the candidate, reservation, fingerprint, and `READY_TO_SUBMIT`/`SUBMITTING` states before calling `OrderManager`. Exceptions and uncertain responses enter `RECONCILIATION_REQUIRED`, and no retry is performed. The broker and SQLite still cannot commit atomically, so the distributed crash window is contained rather than eliminated. |
| F-CRIT-03 — no authoritative broker reconciliation at execution boundary | **PARTIAL** | `startup_guard` calls `reconcile_live_reservations`, including broker executions. After the provider, `_refresh_live_context_for_candidate` fetches account summary, positions, open orders, and quote, but it does not fetch executions. The final boundary therefore has strong position/order evidence but not the complete requested fresh execution-state read. |

## 3. High Findings Recheck

### F-HIGH-01 — post-LLM quote/risk revalidation: PARTIAL

`process_candidate` calls `_refresh_live_context_for_candidate` after the model
returns. That refresh obtains a new account identity, position set, open-order
set, and quote; the deterministic `AutonomousRiskGate` then evaluates the
refreshed context. `OrderManager.execute_trade` performs another quote-age,
price-chase, spread, and positive-price check.

This closes the principal stale-context path. It is not a complete final
revalidation because:

- the second `OrderManager` quote is not passed through a second full
  `AutonomousRiskGate` evaluation;
- a fresh quote with `quote_status="missing"` and a plausible `last` value can
  pass the `OrderManager` guard because that guard checks age and price but not
  the status field;
- there remains an unavoidable race between the last read and broker acceptance.

Evidence: `src/decision/agent.py::_refresh_live_context_for_candidate`,
`src/execution/order_manager.py::execute_trade`, and tests
`test_missing_quote_age_is_unknown` and
`test_autonomous_guard_rejects_unknown_stale_and_wide_quotes`.

### F-HIGH-02 — unknown account identity cannot bypass allowlist: FIXED

`DecisionAgentConfig.validate` requires a verified account ID, a non-empty
allowlist, and an exact allowlist match for `live_autonomous`. The agent's
`_discover_account_id` queries broker account summary and rejects a configured
ID that is not present. The missing and wrong-account cases are covered by
`test_live_configuration_requires_verified_allowlisted_account`.

### F-HIGH-03 — provider latency and protective management: PARTIAL

The old synchronous entry call was replaced in `run_entry_scan` with
`decision_agent.submit_signal`, which queues enabled-mode work. The intraday
job then calls `run_paper_safety_cycle` before `manage_positions`, and the open
job calls the Paper safety cycle after the scan. Thus a slow provider does not
block the caller while waiting for an entry decision, and deterministic Paper
protection does not call the provider.

The isolation is not fully operationally proven:

- the executor is process-local and its queue slots are shared by entry and
  position-review work;
- Live startup reconciliation runs inside the autonomous worker and can wait
  behind queued work;
- no long-running scheduler test measures kill-switch, legacy broker
  management, or reconciliation latency under provider saturation;
- legacy Shadow execution remains synchronous by design, although that is the
  pre-existing deterministic path rather than the AI provider call.

Evidence: `src/decision/agent.py::_submit_queued`,
`src/jobs/intraday.py`, `src/jobs/open.py`, and
`test_decision_queue_is_bounded_and_shadow_deduplicates_pending_work`.

### F-HIGH-04 — stable candidate identity: PARTIAL

For the stock `TradeSignal` path, `_signal_timestamp` uses signal/setup/source
bar/candle timestamps and no current wall-clock value participates in the
candidate hash. A Paper/Live candidate marked unstable is rejected. The tests
`test_candidate_identity_has_no_clock_fallback` and
`test_candidate_identity_uses_source_bar_timestamp` verify the main path.

The mapping adapter has a remaining edge case: `mapping_to_candidate` sets
`identity_status="stable"` whenever `setup_timestamp` is non-empty, even if
`datetime.fromisoformat` rejects that value. A payload such as
`{"timestamp": "not-a-timestamp"}` can therefore be treated as stable and
accepted by an autonomous caller that uses this adapter. This is not a current
wall-clock fallback, but it is not valid setup provenance.

### F-HIGH-05 — audit/storage fail-closed behavior: PARTIAL

Before autonomous mutation, candidate persistence, reservation, snapshot,
decision, risk, and Live intent writes are attempted before Paper mutation or
the broker call. Failures return a no-action/audit-error result, and the new
audit-failure test confirms that a Paper position is not opened.

After a broker exception or post-submit audit failure, the reservation is
marked `RECONCILIATION_REQUIRED` where possible and is never automatically
retried. The residual limitation is inherent to the external broker boundary:
if the database fails both during post-submit recording and during the
reconciliation-state update, local evidence can remain unavailable until an
operator or later recovery process repairs it. The code contains the right
fail-closed intent, but the required decision-persistence, risk-persistence,
reservation-persistence, and post-submit failure matrix is not fully tested.

### F-HIGH-06 — broker order provenance: FIXED structurally

`broker_order_ref` creates a stable compact reference from agent, candidate,
and decision IDs. `OrderManager` passes it to
`IBKRClient.place_market_bracket_order`; the IBKR adapter applies the base
reference to the parent and `-SL`/`-TP` suffixes to protective children. The
mocked test verifies propagation through the manager.

Actual TWS/IBKR visibility was not tested, by design. Operational broker
validation remains a Live-only residual risk.

## 4. Medium Findings Recheck

| Finding | Status | Conclusion |
|---|---|---|
| F-MED-01 — legacy News coupled to AI path | **PARTIAL** | Autonomous risk/snapshot News use is disabled by default and News exceptions/high results do not veto the AI branch. However, `run_entry_scan` still calls macro and symbol News synchronously before candidate creation, so News availability can still delay candidate evaluation. |
| F-MED-02 — Paper protection/review not proven in real job path | **PARTIAL** | Real callers exist in `run_intraday` and `run_open`; deterministic protection is independent of the provider. No long-running scheduler observation proves interval behavior under production conditions. |
| F-MED-03 — Paper portfolio and audit are not one atomic transition | **PARTIAL** | `PAPER_MUTATING` plus restart recovery compensates for a crash between JSON and SQLite writes. The two stores remain non-transactional. |
| F-MED-04 — shared Paper/Live namespace | **FIXED** for execution authorization | Reservation keys include mode, agent, candidate, and account. Paper does not count as a Live reservation. Candidate audit rows remain shared for provenance only. |
| F-MED-05 — Shadow duplicate work and backlog | **FIXED** for one process | Shadow has state deduplication, bounded queue slots, candidate expiry, and overload no-action results. There is no cross-process queue or deployment-wide deduplication. |
| F-MED-06 — missing real-scan integration coverage | **PARTIAL** | Wiring is present and component tests pass, but no real end-to-end `run_entry_scan` test proves all modes, News errors, provider latency, and legacy Shadow behavior together. |

## 5. Atomic Reservation Verification

`src/decision/audit.py` defines:

```sql
execution_key TEXT PRIMARY KEY
```

The key is built from lowercase mode, agent ID, candidate ID, and account ID.
`reserve_execution` opens a new SQLite connection, executes `BEGIN IMMEDIATE`,
inserts the reservation, commits, and treats a uniqueness conflict as an
idempotent claim failure.

The remediation test launches two threads against the same audit database.
Each `reserve_execution` call creates its own SQLite connection, so this is a
two-independent-connection test within one process. It produced exactly one
successful claim. The test is adequate for SQLite transaction behavior, but it
does not prove a Windows multi-process deployment with separate interpreters.

The mode namespace test confirms that the same candidate/account can have
distinct Paper and Live reservations while a second Paper claim is rejected.

## 6. Crash / Restart Verification

### Case 1 — reservation and Paper position both exist

`_recover_paper_reservations` indexes Paper positions by candidate ID. When the
reservation is incomplete and the position exists, it records the missing
Paper execution link and transitions the reservation to `PAPER_SIMULATED`.
`test_paper_restart_recovers_portfolio_mutation_to_execution_link` verifies
this path without entering a second position.

### Case 2 — `PAPER_MUTATING` reservation but no Paper position

The recovery method transitions the reservation to
`FAILED_PRE_SUBMIT` with `paper_mutation_not_found_after_restart`. It does not
re-enter the candidate. This behavior is present in code but has no dedicated
test in the 14-test remediation file.

### Case 3 — Paper JSON changed but audit completion did not occur

This is covered by the same position-exists recovery logic: the JSON position
is treated as the durable mutation evidence and the execution link is
recreated. It is compensating recovery, not atomic cross-store commit.

For Live, `SUBMITTING` reservations with no matching order reference or
execution ID become `RECONCILIATION_REQUIRED`. The re-review test verifies no
broker submission is attempted during recovery.

## 7. Broker Reconciliation Boundary

The boundary has two different reconciliation phases:

1. `startup_guard` calls `reconcile_live_reservations`, which reads broker open
   orders and executions and resolves stored references/order IDs without
   resubmission.
2. After an `ENTER` response, `_refresh_live_context_for_candidate` reads the
   verified account, broker positions, broker open orders, and a fresh quote,
   then rejects an existing same-symbol position or open order.

The second phase does not read broker executions. Therefore the accurate
status is **PARTIAL** for the requested “fresh account, positions, open orders,
executions immediately before submission” invariant. No optimistic caller
value of `account_synced=True` is sufficient by itself: the Live path sets it
only after `_discover_account_id` and broker positions/open-orders/quote reads.
The `OrderManager` guard later hard-codes `account_synced=True`, but that value
is derived from the preceding refresh rather than the original scan context.

## 8. Post-LLM Market Revalidation

The implemented flow is:

```text
candidate
  -> provider decision
  -> broker account/positions/open-orders refresh
  -> fresh quote refresh
  -> AutonomousRiskGate
  -> OrderManager quote age/chase/spread guard
  -> broker handoff
```

The following are blocked in the main Live path:

- quote age missing during `_refresh_live_context`: veto;
- quote status `missing` during `_refresh_live_context`: veto;
- stale quote: risk/OrderManager rejection;
- excessive price movement: risk/OrderManager rejection;
- wide spread: OrderManager rejection;
- same-symbol broker position: reconciliation veto;
- same-symbol broker open order: reconciliation veto.

The final guard has a narrow status-field gap: it does not independently test
`quote_status == "missing"` after its own second `get_quote` call. A malformed
adapter response containing a fresh timestamp and positive price could pass
that final guard if the earlier refresh had passed. This is why F-HIGH-01 is
classified PARTIAL rather than FIXED.

## 9. Provider Latency Isolation

`submit_signal` uses a per-agent `ThreadPoolExecutor`, a semaphore sized to
workers plus queue depth, pending-key deduplication, and candidate deadlines.
`run_entry_scan` does not wait for Paper/Live provider work; it reports the
decision as scheduled. This means a slow provider does not hold the
`run_entry_scan` caller before the real job reaches its next management step.

The actual intraday sequence is:

```text
broker position/open-order sync
  -> run_entry_scan (queues autonomous work)
  -> run_paper_safety_cycle
  -> manage_positions / kill-switch / protective management
```

The open sequence calls `run_paper_safety_cycle` immediately after the scan.
Paper protection itself only reads quotes and calls `PaperPortfolio`.

This is sufficient for controlled Paper operation under the current process
model. It is not proof of production timing: the same queue slots are shared
with optional position review, and Live reservation reconciliation is performed
inside an autonomous worker. No timing test measures management latency under
provider saturation.

## 10. Paper Protection Scheduling

The implementation has real callers, not only a method definition:

- `src/jobs/intraday.py` calls `default_agent(order_manager).run_paper_safety_cycle(market_data)` before `manage_positions`.
- `src/jobs/open.py` calls the same cycle after `run_entry_scan`.

`run_paper_safety_cycle` is gated by a configurable interval, fetches a usable
fresh quote, and calls `PaperPortfolio.enforce_protection` without a provider.
The test `test_paper_protection_runs_without_provider` creates a Paper position,
provides a stop-hit quote, leaves the provider absent, and verifies the position
closes.

The scheduler invocation is therefore structurally verified. A long-running
market-session observation and failure-injection test for quote-service stalls
remain outstanding.

## 11. Paper Recovery

Paper recovery is conservative:

- existing position plus incomplete reservation: recover the execution link;
- `PAPER_MUTATING` without a position: mark failed, never replay;
- JSON mutation followed by audit interruption: recognize the persisted
  position and complete the audit link on restart.

The recovery code is safe for controlled validation, but separate JSON/SQLite
stores mean operators must retain the database and portfolio file together and
inspect `RECONCILIATION_REQUIRED` or failed recovery states.

## 12. Account Identity

The Live path requires all three properties:

1. non-empty account ID;
2. broker account-summary verification;
3. explicit allowlist membership.

`DecisionAgentConfig.validate` rejects missing and wrong IDs. The agent rejects
broker/account-summary failure before it creates a Live execution reservation.
The remediation test covers missing, wrong, and correct account cases. This
finding is FIXED for the reviewed code path.

## 13. Audit Fail-Closed Behavior

The pre-submit ordering is safe:

```text
candidate persistence
  -> reservation
  -> snapshot
  -> provider decision record
  -> risk record
  -> READY_TO_SUBMIT
  -> SUBMITTING
  -> OrderManager
```

The Paper audit-failure test proves a snapshot failure does not mutate the
Paper portfolio. Provider and risk recording failures return no-action and do
not call the broker. A broker exception transitions the reservation to
`RECONCILIATION_REQUIRED`; there is no automatic retry.

Coverage is incomplete for independently failing candidate, decision, risk,
reservation, pre-submit, and post-submit writes. The implementation is
fail-closed in the important paths, but the evidence supports PARTIAL rather
than a fully verified matrix.

## 14. Broker Order Provenance

Static inspection confirms:

- `AutonomousGerchikAgent` creates the stable reference;
- `OrderManager` passes it as `order_ref`;
- `IBKRClient.place_market_bracket_order` assigns it to the parent order;
- the stop child receives `<ref>-SL`;
- the target child receives `<ref>-TP`;
- the reservation stores the reference for restart reconciliation.

The mocked remediation test verifies the manager-to-broker call. No IBKR/TWS
connection was made, so actual broker-field persistence remains unverified and
is not a reason to approve Live.

## 15. Mode Isolation

### Shadow

The autonomous Shadow branch records decisions and does not call
`OrderManager`. The legacy scan still calls `OrderManager` after scheduling
Shadow work. Therefore AI-to-broker isolation is present, but whole-scan
no-order isolation is intentionally absent.

### Paper

Autonomous Paper uses `PaperPortfolio` and the Paper reservation namespace. It
does not call `OrderManager` for entry. Paper execution is not treated as a
Live completion.

### Live

Live uses a separate reservation key and requires explicit configuration,
verified account identity, startup checks, and an injected order manager. A
Paper reservation does not authorize or suppress a Live reservation, although
candidate audit rows remain shared as historical provenance.

## 16. News Isolation

With `LLM_AGENT_USE_NEWS=false`:

- `AutonomousGerchikAgent._request` removes News fields from the provider
  snapshot;
- autonomous risk receives neutral News flags;
- a News exception becomes `UNKNOWN` and does not trigger the autonomous
  high-risk veto;
- a high-risk macro or symbol result is bypassed for the autonomous branch;
- legacy News behavior remains in the legacy path.

The architectural coupling is not fully removed. `run_entry_scan` still calls
`get_macro_risk_context` before it has a candidate and
`get_symbol_risk_context(symbol)` before routing the strategy. The calls are
exception-safe and their high-risk results no longer block the autonomous
branch, but a slow or hanging News service can still delay candidate creation.
This is **PARTIAL** isolation, not complete News independence.

## 17. Test Coverage

Safe re-review execution reran 59 tests with no order-capable worker or broker
connection. Result: **59 passed, 2 deprecation warnings**.

| Requirement | Test/evidence | Adequate? |
|---|---|---|
| atomic reservation | `test_sqlite_reservation_allows_exactly_one_concurrent_claim` | Partial: independent SQLite connections in one process; no multi-process test |
| concurrent DB claims | same test; `BEGIN IMMEDIATE` plus primary key | Partial but adequate for the database primitive |
| mode namespace | `test_reservation_namespace_does_not_cross_shadow_paper_live` | Yes for reservation namespace |
| stable candidate ID | `test_candidate_identity_has_no_clock_fallback`; source-bar test | Partial: malformed mapping timestamp not covered |
| Paper recovery | `test_paper_restart_recovers_portfolio_mutation_to_execution_link` | Partial: no dedicated no-position mutation test |
| Paper protection | `test_paper_protection_runs_without_provider` plus real job callers | Yes for deterministic unit behavior; scheduler timing remains unproven |
| Live account verification | `test_live_configuration_requires_verified_allowlisted_account` | Yes for config/account cases |
| quote freshness | `test_missing_quote_age_is_unknown`; guard test | Partial: final `quote_status="missing"` edge not tested |
| quote chase | `test_autonomous_guard_rejects_unknown_stale_and_wide_quotes` and risk gate | Yes for tested guard cases |
| spread revalidation | same guard test | Yes for the OrderManager guard |
| uncertain submission | `test_live_reconciliation_marks_uncertain_submission_without_retry` | Yes for no-retry state transition |
| broker orderRef | `test_order_manager_requires_fresh_autonomous_quote_and_preserves_reference` | Partial: mocked only, no IBKR/TWS observation |
| News isolation | `test_news_is_omitted_from_autonomous_snapshot_when_disabled`; existing News tests | Partial: no full `run_entry_scan` News-error integration test |
| audit failure | `test_audit_failure_fails_closed_before_paper_mutation` | Partial: one injected write failure, not the full failure matrix |
| bounded Shadow queue | `test_decision_queue_is_bounded_and_shadow_deduplicates_pending_work` | Yes for process-local queue behavior |
| provider latency isolation | executor code and call-flow inspection | No dedicated timing test |

The safe test command was:

```text
python -m pytest -q tests/test_autonomous_llm_remediation.py tests/test_decision_agent.py tests/test_decision_audit.py tests/test_order_flow.py tests/test_intraday_sync.py tests/test_intraday_report.py tests/test_job_session_utils.py tests/test_news_blocking.py tests/test_broker_reconcile.py
```

The earlier full-suite evidence in the remediation run was 372 passed and one
unrelated pre-existing Flex Statement failure. This re-review did not modify
Flex behavior.

## 18. Remaining Risks

| Risk | Shadow | Controlled Paper | Live |
|---|---|---|---|
| SQLite and broker cannot be one distributed transaction | Acceptable with AI/legacy distinction | Acceptable with supervised recovery | Blocks Live approval; reconciliation is mandatory |
| IBKR/TWS `orderRef` not operationally validated | Not relevant to AI Shadow | Not required for Paper-only entry | Blocks Live approval until separately verified |
| Long-running scheduler not operationally validated | Acceptable for a bounded observation | Acceptable only with supervision, stop conditions, and log review | Blocks Live approval |
| Paper JSON and SQLite remain separate stores | Not relevant | Acceptable for controlled Paper with restart checks and preserved files | Not a Live entry control, but blocks claiming full autonomous recovery |
| Legacy execution remains active in Shadow | Acceptable only with explicit operator awareness | Does not affect isolated Paper mode | Not a Live approval control |
| No real end-to-end `run_entry_scan` validation | Acceptable with a non-ordering/legacy-order warning | Acceptable for staged controlled validation, not unattended operation | Blocks Live approval |

Additional counterexample risks are the malformed mapping timestamp, final
quote-status gap, and worker-local reconciliation timing described above.

## 19. Shadow Readiness

```text
SHADOW: READY WITH CONDITIONS
```

Conditions:

- Treat Shadow as “AI observation; legacy execution remains active,” not as a
  no-order mode.
- Use a watchlist and broker/dry-run configuration appropriate for the legacy
  path, because the legacy `OrderManager` call remains active.
- Monitor the autonomous audit database, queue-full/expiry results, provider
  failures, and legacy execution separately.
- Do not infer broker isolation for the overall scan solely from the AI branch.

The AI Shadow result itself cannot invoke `OrderManager` in the reviewed path.

## 20. Paper Autonomous Readiness

```text
PAPER AUTONOMOUS: READY FOR CONTROLLED VALIDATION
```

This approval is limited to controlled validation, not unattended production
operation. Required controls for the validation run:

- keep `LLM_AGENT_USE_NEWS=false` unless News is intentionally included in a
  separate experiment;
- preserve the Paper JSON and SQLite database together;
- monitor `PAPER_MUTATING`, `RECONCILIATION_REQUIRED`, and failed-recovery
  states;
- confirm the intraday/open scheduler invokes `run_paper_safety_cycle`;
- use explicit stop conditions and review the deterministic protection logs;
- do not provide a broker-capable execution path as part of the Paper test;
- separately test the no-position recovery case before unattended use.

The provider is not required for stop/target protection, and the safe test
confirmed a provider-free stop-hit close.

## 21. Live Autonomous Status

```text
LIVE AUTONOMOUS: NOT APPROVED
```

Live remains disabled. Structural controls are not sufficient for approval
without separately authorized evidence for broker account/state behavior,
actual order-reference persistence, post-provider executions reconciliation,
and operational restart handling. This review did not connect to IBKR and did
not submit an order.

## 22. Final Verdict

```text
SHADOW:
READY WITH CONDITIONS

PAPER AUTONOMOUS:
READY FOR CONTROLLED VALIDATION

LIVE AUTONOMOUS:
NOT APPROVED
```

The remediation is materially effective for beginning safe, supervised Shadow
observation and controlled isolated Paper validation. It should not be
described as closing every Live safety finding, and the remaining partial
findings must stay visible in any operator-facing readiness statement.
