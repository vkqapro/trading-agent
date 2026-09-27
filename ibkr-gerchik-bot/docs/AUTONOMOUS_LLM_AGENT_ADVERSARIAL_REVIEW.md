# Autonomous LLM Gerchik Agent — Adversarial Safety Review

Review date: 2026-09-26  
Review type: read-only architecture, execution-safety, and failure-mode review  
Scope: the newly implemented Autonomous LLM Gerchik Agent and its integration with the existing Gerchik/IBKR runtime

## Review scope and evidence boundary

This review inspected the actual implementation, not only the architecture document. The reviewed surfaces include:

- `src/decision/agent.py`, `models.py`, `provider.py`, `policy.py`, `audit.py`, `portfolio.py`, and `shadow.py`;
- the `run_entry_scan` and `manage_positions` integration in `src/jobs/session_utils.py`;
- `src/jobs/intraday.py`, `src/jobs/open.py`, and `src/jobs/execute_requests.py`;
- `src/execution/order_manager.py` and the IBKR write methods in `src/brokers/ibkr.py`;
- configuration and environment-template values in `src/config.py` and `.env.template`;
- the existing decision-agent tests and related order-flow tests.

No application code, configuration, `.env` file, runtime state, database, portfolio file, broker connection, or order-capable worker was changed or enabled during this review. No live, paper, or shadow order was submitted by this review.

The review distinguishes four kinds of evidence:

1. **Static evidence** — what the code permits or prevents by construction.
2. **Integration evidence** — what the real job call graph does when the agent is wired into it.
3. **Unit-test evidence** — behavior covered by the existing tests.
4. **Operational evidence** — what was actually observed with a running provider, broker, or worker. This review did not create new operational execution evidence.

## 1. Executive Verdict

**Verdict: NOT APPROVED for Live Autonomous operation. CONDITIONAL for Shadow. NOT APPROVED for Paper Autonomous operation without additional controls.**

The implementation has several good safety boundaries:

- the decision package does not import broker or exchange clients;
- the provider response is strict JSON with a bounded action vocabulary;
- model output does not control quantity, entry, stop, or target values;
- Shadow provider calls do not call `OrderManager`;
- Paper uses a separate durable portfolio rather than the IBKR order manager;
- deterministic risk and protection code remains outside the model;
- provider failures normally become `NO_ACTION` rather than an execution fallback.

Those controls are not sufficient for autonomous Live operation. The most serious weaknesses are in the boundary around the broker call rather than in the LLM response parser:

- candidate execution has a check-then-act race and no durable execution reservation;
- an accepted broker order can exist before the corresponding audit execution record, allowing restart replay;
- the code does not perform a fresh broker/local position and open-order reconciliation immediately before an autonomous entry;
- the quote and risk decision are not revalidated after the LLM response and immediately before order submission;
- `account_synced=True` is supplied by the entry integration rather than proven at the execution boundary;
- a missing broker account identifier can bypass the configured allowlist check because the allowlist is only checked when the identifier is truthy;
- the synchronous Paper/Live provider call occurs in the intraday entry path before application-level position management;
- the paper protection and position-review methods exist, but their periodic integration is not proven by the reviewed job path;
- legacy news calls remain on the AI path even though News is explicitly out of scope for the requested safety boundary.

The agent-level Shadow path is broker-isolated, but the overall Shadow scan still runs the legacy deterministic execution path after submitting the asynchronous Shadow decision. Therefore “Shadow cannot place an AI order” is true, while “the full scan cannot place any order in Shadow mode” is false.

## 2. Architecture Confirmed

The implementation is a layered adapter around the existing strategy and execution system:

```text
market/session job
  -> run_entry_scan
     -> legacy news gates
     -> route_strategies
     -> TradeSignal
     -> decision agent adapter
        -> DecisionCandidate
        -> DecisionAudit snapshot
        -> provider request
        -> strict DecisionResponse
        -> AutonomousRiskGate
        -> PaperPortfolio OR OrderManager
     -> existing legacy OrderManager path (still present in Shadow)
```

The principal components are:

| Component | Responsibility | Safety observation |
|---|---|---|
| `AutonomousGerchikAgent` | Mode dispatch, provider call, audit, risk, paper/live handoff | Owns the new path but does not own an atomic execution claim |
| `DecisionCandidate` | Stable candidate representation and candidate ID | Stable only when the source signal supplies a stable setup timestamp |
| `DecisionAudit` | SQLite candidates, snapshots, decisions, risk records, execution links | Durable records exist, but execution linking is after the broker call |
| `HttpDecisionProvider` / local provider | LLM request and strict response parsing | No direct broker import or broker method call |
| `AutonomousRiskGate` | Deterministic entry/position constraints | Uses the context supplied by the caller; it does not independently refresh broker state or quotes |
| `PaperPortfolio` | Isolated simulated cash and position state | Atomic file replacement is good, but caller scheduling and audit coupling are incomplete |
| `OrderManager` | Existing validation, sizing, payload creation, and broker handoff | Receives the LLM-approved signal but does not perform a final autonomous risk recheck |
| `IBKRClient` | Actual IBKR order methods | The final write surface remains reachable through `OrderManager` in Live mode |

## 3. Real LLM → Execution Call Graph

### Intraday and open-session entry path

The real path is:

1. `run_intraday` or `run_open` invokes `run_entry_scan`.
2. `run_entry_scan` performs the existing macro and symbol news checks.
3. The scan obtains market data and calls `route_strategies`.
4. A `TradeSignal` is produced.
5. The scan constructs an `AgentRuntimeContext`, including quote, spread, data age, current positions, broker connection status, account fields, and risk flags.
6. In `SHADOW`, it calls `decision_agent.submit_signal(...)` asynchronously, then continues into the existing legacy `order_manager.execute_trade(...)` path.
7. In `PAPER_AUTONOMOUS` and `LIVE_AUTONOMOUS`, it calls `decision_agent.process_signal(...)` synchronously.
8. `process_signal` converts the signal to a `DecisionCandidate` and calls `process_candidate`.
9. `process_candidate` checks the audit, invokes the provider, parses the response, records decision/risk evidence, evaluates the deterministic risk gate, and either:
   - records `NO_ACTION`;
   - calls `PaperPortfolio.enter(...)`; or
   - annotates the signal and calls `OrderManager.execute_trade(...)` in Live.
10. `OrderManager.execute_trade` invokes existing validation and eventually `IBKRClient.place_market_bracket_order(...)` for an eligible market entry.

### Important integration distinction

The agent is not the only execution path in the scan. In Shadow, the new agent runs for observation while the old deterministic path can still submit an order. This is an intentional compatibility behavior in the current implementation, but it must be represented accurately in operational controls and operator documentation.

## 4. Broker Write Surface

The decision package itself has no direct import or call to `IBKRClient`, `placeOrder`, `place_market_order`, `place_market_bracket_order`, or exchange clients. Its Live handoff is dependency-injected through an `OrderManager` object.

The actual broker write surface remains in the existing system:

- `src/execution/order_manager.py::execute_trade` for strategy-driven execution;
- `src/execution/order_manager.py::execute_manual_order` for manual/dashboard execution;
- `src/brokers/ibkr.py` methods including market, limit, stop, bracket, and stop-replacement operations;
- the existing request-execution job and other legacy broker-management paths.

The autonomous path reaches the broker only through `OrderManager.execute_trade` in Live. That is a useful structural boundary, but it is not an authorization boundary by itself: the injected manager is order-capable, and the agent does not create a durable, single-use execution authorization immediately before calling it.

## 5. Mode Isolation

### OFF

`OFF` returns before provider calls and before new audit/paper state is created. The existing entry logic remains available. The default-agent construction/import is a small code-path side effect, but no autonomous provider or broker action is taken by the new path.

**Assessment: structurally isolated, subject to existing legacy behavior.**

### SHADOW

The Shadow provider path is asynchronous and does not call the order manager from the agent. Multi-provider Shadow also records provider-specific decisions without an execution handoff.

However, `run_entry_scan` continues to invoke the existing legacy execution path after scheduling the Shadow request. Shadow therefore isolates the LLM from broker execution, not the whole scan from broker execution.

**Assessment: AI order isolation verified; whole-scan order isolation not present.**

### PAPER_AUTONOMOUS

The agent calls `PaperPortfolio` and does not call `OrderManager` for the new entry path. This is a real separation in the agent implementation. The paper state is held in `decision_lab_portfolio.json` and is not interpreted as an IBKR position by the agent.

The SQLite audit database and candidate IDs are shared across modes. Consequently, a candidate executed in Paper may later be considered already executed when the mode is changed to Live. That is conservative against duplicate execution but is not clean mode isolation.

**Assessment: broker isolation present; cross-mode identity and recovery semantics incomplete.**

### LIVE_AUTONOMOUS

Live requires explicit configuration flags and an injected `OrderManager`, then calls the existing broker-capable execution path. The mode is not reachable by ordinary default configuration, but the implementation is order-capable if the explicit Live settings are enabled.

**Assessment: reachable by configuration; not safe to enable based on this review.**

## 6. Shadow Safety

The direct AI Shadow path cannot place an order because `submit_signal` schedules decision processing and the Shadow branch records the result without calling Paper or Live execution. The provider and multi-provider runner have no broker dependency.

The important operational caveats are:

- the legacy deterministic `OrderManager` call remains active in the same scan;
- Shadow submissions are not deduplicated before provider work, so repeated scans can produce duplicate provider requests and audit entries;
- the executor has a bounded worker count but no explicit per-candidate queue limit or cancellation policy;
- asynchronous exceptions are not always surfaced to the caller, and audit failures in the multi-provider failure branch are not uniformly protected;
- no reviewed integration test proves the complete real `run_entry_scan` Shadow behavior with the legacy order path and the new agent together.

**Conclusion: safe for observing the LLM as a non-controlling signal source, but not safe to describe as a no-order Shadow mode for the whole runtime.**

## 7. Paper Isolation

The new Paper entry path does not call IBKR. `PaperPortfolio` maintains simulated cash and positions in its own durable JSON file. Its save method uses a temporary file, flush/fsync, and `os.replace`, which is a strong single-file persistence pattern.

Isolation is incomplete in three ways:

1. The agent receives an `OrderManager` reference even in Paper, although the current Paper branch does not use it. This is not a direct broker leak in the reviewed code, but it makes dependency-level isolation less explicit than it could be.
2. The same audit database and candidate identity are shared by modes. A Paper execution can suppress a later Live attempt for the same candidate, and mode transitions are not represented as an independent execution namespace.
3. The paper portfolio can be persisted before its corresponding execution audit row. A process crash between those writes can leave a simulated position without an execution link; a later replay may be blocked by the portfolio’s symbol ownership while the audit still says the candidate is not active.

**Conclusion: broker isolation is present; Paper is not yet transactionally or operationally isolated enough for autonomous use.**

## 8. Live Startup Safety

The Live startup guard checks important explicit controls: Live allowment, non-paper configuration, dry-run state, the presence of an order manager, and configured account allowlisting when an account ID is available.

The following gaps remain:

- the account allowlist is checked only when `account_id` is truthy; an unknown/missing account ID does not itself produce `account_not_allowlisted`;
- the scan context obtains the account ID from the broker object, but the reviewed path does not prove that the configured IBKR client always exposes the selected account identifier;
- startup does not require a fresh broker connection, successful account reconciliation, current broker positions, current open orders, or protective-stop integrity;
- `config.validate` validates configuration-level requirements but does not perform provider health or broker health verification;
- provider construction is treated as availability; no startup request proves the provider can answer;
- the runtime context supplied by `run_entry_scan` sets `account_synced=True` rather than deriving it from a just-completed, durable reconciliation result.

**Severity: HIGH. Live startup should fail closed when the selected account is unknown or when broker state has not been freshly reconciled.**

## 9. Candidate Idempotency

`trade_signal_to_candidate` derives a candidate ID from a selected stable payload containing the asset class, symbol, strategy, direction, level, entry, stop, target, and setup timestamp. This avoids using the entire mutable signal object and is directionally correct.

The stability guarantee is conditional. If the signal metadata does not contain `signal_timestamp`, `setup_timestamp`, or `timestamp`, the adapter falls back to the current time. The same unchanged setup can then receive a new candidate ID on each scan cycle. The payload also omits some potentially material setup attributes such as ATR, reward/risk, risk-per-share, confidence, and broader market context.

The audit table has a primary key for `candidate_id`, but there is no unique active-execution constraint that makes the full check-and-execute operation atomic.

**Assessment: candidate IDs are deterministic only when upstream timestamp provenance is present; execution idempotency is not guaranteed.**

## 10. Duplicate / Concurrency Analysis

The critical sequence is:

```text
caller A: has_active_execution(candidate_id) -> false
caller B: has_active_execution(candidate_id) -> false
caller A: provider -> ENTER -> risk approved
caller B: provider -> ENTER -> risk approved
caller A: broker call
caller B: broker call
```

`DecisionAudit.has_active_execution` is a read-only query. It does not claim the candidate, insert a pending execution reservation, acquire a per-candidate lock, or participate in the transaction that calls the broker. `execution_links` has an autoincrement key but no unique candidate/state-machine constraint that closes this race.

The same issue exists for multiple processes, multiple threads, overlapping session jobs, and restart replay. The paper portfolio’s atomic file replacement protects individual file replacement, not two callers deciding simultaneously that the symbol is available.

**Severity: CRITICAL for Live autonomous operation; HIGH for Paper.**

## 11. Ownership and Same-Symbol Collisions

The design has useful ownership labels:

- decision records include agent/provider/model/mode;
- risk records include candidate and decision IDs;
- execution links include candidate and decision IDs;
- Paper positions carry `owner=LLM_AGENT` and a candidate ID;
- Live signal metadata receives owner, agent, decision, and candidate provenance before the `OrderManager` call.

The risk policy rejects a visible same-symbol position owned by a legacy source. That protects against a straightforward collision when local context is current and normalized.

It does not make an IBKR net account independently partitionable. A symbol-level local owner tag cannot distinguish independent long/short ownership in a net position, and provenance is not visibly propagated into the broker order reference itself. If the broker position is absent from or stale in the local context, the local owner check can be bypassed.

**Assessment: ownership is represented in application records, but same-symbol safety depends on current authoritative broker state and is not guaranteed at the broker boundary.**

## 12. Broker vs Local Reconciliation

The intraday job obtains broker positions/open orders and invokes the existing synchronization helper. That is useful session-level work, but the autonomous entry path then supplies `account_synced=True` in the agent context and relies on the already-built local snapshot.

Immediately before the autonomous `OrderManager.execute_trade` call, there is no demonstrated fresh sequence of:

1. fetch broker positions;
2. fetch broker open orders;
3. reconcile the candidate symbol and owner state;
4. reject if broker/local state differs;
5. atomically reserve the candidate;
6. submit the order.

`OrderManager.execute_trade` also uses the caller-supplied current positions and passes `account_synced=True` into existing validation. It fetches quote information for execution checks, but not a full broker state reconciliation and not a durable comparison against the autonomous risk decision.

**Severity: CRITICAL/HIGH. A broker position or open order missing from local state can permit a duplicate or colliding autonomous entry.**

## 13. LLM Latency Impact

Shadow uses a separate executor and therefore does not synchronously hold up the entry scan for provider latency.

Paper and Live call `process_signal` synchronously from `run_entry_scan`. `run_intraday` reaches application-level `manage_positions` after the entry-scan work. A slow provider, timeout, retry, or blocked network request can therefore delay the next application-level position-management pass, including kill-switch, trailing, loss-cut, or stop-maintenance logic.

Broker-side bracket protection can still exist independently for a submitted trade, but it does not eliminate the delay to the application’s management loop or protect positions that require local reconciliation/management.

**Severity: HIGH. The provider call must not be allowed to delay protective management or session health work.**

## 14. Provider Failure Behavior

The strict HTTP provider rejects invalid JSON, invalid actions, non-finite confidence, malformed ranked values, and overlong summaries. Provider exceptions are normally caught by `process_candidate` and converted into `NO_ACTION`; there is no automatic fallback provider that can silently execute.

The response parser ignores unrecognized execution fields such as quantity, entry, stop, and target. Those values therefore cannot directly replace deterministic signal values through the parsed decision response.

Failure handling is not uniformly fail-closed around audit persistence:

- provider failure handling records an audit decision and provider health result, but those writes are not always protected if the database is unavailable;
- Shadow multi-provider failure paths also attempt audit writes without a uniform protective wrapper;
- an audit failure after the initial snapshot can propagate or leave the caller without a clear durable state;
- provider construction is not the same as an actual provider health check.

**Assessment: provider response failure is generally no-action; audit/storage failure behavior is not fully deterministic and needs explicit fail-closed handling.**

## 15. Response Validation

The response boundary is one of the stronger parts of the implementation:

- JSON is required;
- action values are constrained to the supported enum;
- confidence must be finite and bounded;
- ranked values and summary lengths are constrained;
- the execution-affecting quantities and prices are not taken from the model response;
- no provider code imports the broker client.

The remaining concern is semantic rather than parser-level: a model can still select `ENTER` or position actions based on a malformed, stale, or prompt-injected context. Deterministic policy limits the result, but the policy must receive authoritative and fresh inputs. The current integration does not guarantee that.

## 16. Risk-Gate Bypass Analysis

The normal Live path reaches the deterministic `AutonomousRiskGate` before `OrderManager.execute_trade`, and the audit records the risk result before the broker call. The model cannot directly bypass the gate by supplying its own quantity or price.

The main bypass risks are at the input and state boundaries:

- `account_synced=True` is supplied by integration rather than verified at the final boundary;
- broker positions/open orders are not re-fetched immediately before autonomous entry;
- the current quote is not revalidated after the LLM response;
- stale/missing quote-age information can be interpreted as fresh because the integration default is `0.0`;
- a missing account identifier can avoid the configured account-allowlist rejection;
- a race can cause two callers to pass the same gate before either execution is recorded;
- an execution reservation is not persisted before the broker call.

The risk gate is therefore meaningful for a single uncontended call with a trustworthy context, but it is not a complete authorization barrier for concurrent or stale-state execution.

## 17. Price / Stale-Data Revalidation

The risk gate checks the context’s data age and chase distance. This is useful but not sufficient:

- the entry scan takes a quote before the LLM call;
- the provider can respond after a material delay;
- there is no fresh quote fetch and no second `AutonomousRiskGate` evaluation immediately before the broker call;
- `OrderManager` fetches execution quote data but does not compare it to the candidate entry and re-run the autonomous gate;
- a missing quote-age field defaults to `0.0`, which can turn absent freshness evidence into an apparently fresh quote.

**Severity: HIGH.** A valid LLM decision at time T can reach the broker using an unvalidated market state at time T+n.

## 18. Position Management Safety

The LLM is not called from `manage_positions`; deterministic broker position management includes kill-switch handling, broker position reads, loss-cut logic, trailing/replacement behavior, and protective-stop checks. The model therefore does not directly control the existing protective-management decision.

There is, however, a scheduling issue: Paper and Live entry evaluation is synchronous and occurs before `manage_positions` in the intraday flow. A provider stall can delay the application-level management pass.

For Paper, `review_position` and deterministic action evaluation exist, but no reviewed `run_intraday`/`run_entry_scan` path invokes the paper position review periodically. The method’s existence is not evidence that Paper protection and review are running continuously.

**Assessment: deterministic position-management logic is separate from the LLM, but its scheduling and latency isolation are not proven.**

## 19. Stop / Protection Invariants

The reviewed controls provide several positive invariants:

- model output does not set stop or target values;
- entry risk uses the candidate’s deterministic stop/target;
- the policy rejects stop widening and stop removal in position actions;
- Paper protection checks stop before target when both are crossed, so the conservative stop outcome wins;
- Paper stop movement to breakeven only tightens rather than loosens the stored stop;
- existing Live bracket and protective-stop code remains deterministic.

The main limitation is enforcement coverage. `PaperPortfolio.enforce_protection` is a callable method, not proof of a continuously scheduled protection loop. For Live, the new agent does not manage existing positions, but a slow entry provider can still delay the application-level manager. Broker-side bracket placement and local protective management must be separately verified operationally.

**Assessment: invariants are mostly present in the deterministic methods; continuous invocation is UNVERIFIED.**

## 20. Audit-before-Action

The implementation writes a decision snapshot before the provider call and writes model/risk records before the Live `OrderManager.execute_trade` call. This gives useful decision provenance.

It does not satisfy a strict audit-before-action requirement for execution authorization because there is no durable, single-use execution reservation or “submission intent” record written and committed immediately before the broker call. The execution link is recorded after `execute_trade` returns.

That ordering creates the key crash window:

```text
model/risk audit committed
broker accepts order
process crashes
execution link not written
restart sees no active execution
same candidate may be submitted again
```

**Severity: CRITICAL for Live.** Audit evidence and execution authorization must be atomic with respect to the broker handoff or reconciled by broker order identity before retry.

## 21. Restart Recovery

Paper state reload is durable through the portfolio JSON file and ownership marker. The candidate audit can prevent replay when an execution link was successfully persisted.

Restart safety is not complete:

- there is no durable pre-submit reservation that survives a crash before execution-link recording;
- there is no reviewed startup reconciliation that discovers an accepted broker order lacking a local execution row;
- the broker order reference does not visibly carry the candidate/agent identity in the final IBKR write;
- local ownership and broker open-order ownership are therefore difficult to reconstruct after partial failure;
- corrupted or unavailable paper state causes an operational failure path, but a complete recovery protocol is not proven.

**Assessment: restart duplicate prevention is UNVERIFIED and not guaranteed.**

## 22. Paper Portfolio Integrity

The Paper portfolio has positive properties:

- durable JSON state;
- atomic replacement-style save;
- explicit cash and position accounting;
- owner and candidate metadata;
- deterministic stop/target enforcement;
- rejection of duplicate/same-symbol ownership conditions.

The integrity gaps are coordination gaps:

- no interprocess lock or candidate reservation around read-modify-write;
- portfolio persistence and audit execution recording are separate operations;
- a crash can leave the portfolio and audit in different states;
- `enforce_protection` and `review_position` scheduling is not demonstrated in the real job path;
- shared candidate IDs and audit records across Paper and Live can suppress or confuse mode transitions.

**Assessment: suitable as a controlled simulation component, not yet sufficient as an autonomous paper-trading runtime without stronger coordination and scheduled protection.**

## 23. Secret / Provider Payload Review

The provider request does not include the configured provider API key in the decision payload. The request uses the key for transport authentication, while the prompt payload contains the candidate and a sanitized context.

The sanitization filter removes common secret-bearing key names including key, secret, token, password, credential, account number, account ID, and passphrase. The account ID is also not intentionally included in the model prompt context.

Residual concerns:

- key-name filtering is heuristic and does not cover every possible sensitive value or a value hidden under a benign key;
- authorization headers and arbitrary nested objects require continued review;
- logs and provider error paths must not serialize raw request headers or unsanitized broker objects;
- model prompts may include untrusted metadata, so prompt injection remains a model-behavior risk even though the parser bounds direct execution fields.

**Assessment: no direct secret leak was found in the reviewed normal payload path; this is not a proof that arbitrary future context additions are safe.**

## 24. Multi-Provider Isolation

Multi-provider behavior is limited to Shadow mode. Each provider receives a separate decision ID and provider/model identity, and the runner records results without a broker handoff. There is no automatic provider fallback into execution.

This is a good isolation property. The remaining operational issues are duplicated provider cost/latency, lack of Shadow candidate deduplication, and imperfect audit-failure handling in provider-error branches.

**Assessment: provider isolation is verified structurally; execution isolation is verified for the agent path.**

## 25. Legacy Compatibility

The OFF path preserves the existing legacy flow and does not create new provider or audit state. Shadow intentionally leaves the legacy execution path enabled. Existing deterministic position management remains independent of the LLM.

Compatibility risks are concentrated at shared boundaries:

- legacy news checks still gate the new path;
- the same `OrderManager` and broker state are used by legacy and autonomous Live flows;
- local ownership labels cannot independently partition an IBKR net symbol;
- shared audit/candidate state crosses mode transitions;
- no complete real-scan integration test was found that proves legacy behavior remains unchanged under each new mode.

The previously observed scoped decision/order-flow test run passed 49 tests, while the broader suite had one pre-existing Flex-statement failure tied to existing runtime/report state. That evidence supports local component behavior, not full production compatibility.

## 26. News Exclusion Verification

The requested safety boundary explicitly excludes News and assumes `NEWS_ENABLED_FOR_LLM_AGENT=false`. Under that boundary, missing News must not block the AI path.

The current integration does not meet that requirement cleanly:

- `run_entry_scan` unconditionally calls the legacy macro news risk function and can return early on a high result;
- it also unconditionally calls per-symbol news risk and skips a symbol on a high result;
- `route_strategies` receives `news_context`, and strategy detectors may use it;
- `AutonomousRiskGate` receives news-risk fields;
- `OrderManager` calls the news filter again during validation;
- the news service can therefore still gate or abort evaluation even though News is outside the new agent’s requested safety boundary.

Empty/missing headline data normally maps to a low-risk result, so “no headlines” alone does not necessarily block. A News service exception or a legacy high-risk result can still prevent the AI path from being evaluated.

**Finding: NEWS COUPLING BUG — MEDIUM.** The new safety boundary is not actually isolated from legacy News behavior. This review intentionally does not fix it.

## 27. Test Coverage Mapping

### Covered or substantially covered

- Shadow decision recording without Paper or broker execution;
- Paper autonomous entry without an order manager call;
- provider failure resulting in no action and no new Paper position;
- same-candidate duplicate rejection in a sequential case;
- OFF mode avoiding provider/audit initialization;
- multi-provider Shadow recording;
- Paper position ownership and close behavior;
- default Live safety vetoes;
- stale-data and price-chase policy cases;
- same-symbol legacy ownership rejection;
- stop widening/removal policy cases;
- provider invalid JSON/HTTP/prompt and audit redaction cases;
- crypto adapter/protection contracts;
- selected existing order-flow and intraday synchronization behavior.

### Missing or insufficient coverage

- two concurrent callers racing on the same candidate;
- two processes racing on the same candidate;
- crash after broker acceptance and before `record_execution`;
- startup recovery of a broker order with no local execution link;
- missing/unknown account ID with a non-empty Live allowlist;
- broker/local position or open-order mismatch immediately before entry;
- price movement after provider response and before broker submission;
- missing quote-age field being treated as fresh;
- provider timeout delaying `manage_positions`;
- scheduled Paper protection and position review through the real job loop;
- Paper portfolio/audit crash consistency;
- Paper-to-Live mode transition and shared candidate namespace;
- broker order-reference provenance for candidate/agent/decision identity;
- real `run_entry_scan` Shadow integration proving that legacy execution remains separate from AI execution;
- missing News and News-service exception behavior with News disabled for the AI boundary;
- audit database failure during provider failure, risk recording, and pre-execution recording;
- malformed extra model fields attempting to override execution values in an end-to-end call.

The existing passing scoped tests are valuable component evidence, but they do not close the concurrency, restart, reconciliation, or real-job integration gaps above.

## 28. Findings by Severity

### CRITICAL

**F-CRIT-01 — No atomic candidate claim or execution reservation.**  
`has_active_execution` is a read-only check separated from the provider/risk/execution sequence. Concurrent callers can both submit the same candidate.

**F-CRIT-02 — Broker acceptance can precede durable execution identity.**  
The execution link is recorded after the broker call. A crash can leave an accepted broker order with no local active-execution record, enabling restart replay.

**F-CRIT-03 — No authoritative broker reconciliation at the autonomous execution boundary.**  
The context marks `account_synced=True`, but no immediate positions/open-orders reconciliation is atomically tied to the autonomous decision and broker call.

### HIGH

**F-HIGH-01 — No post-LLM quote and risk revalidation.**  
The quote/risk context can be stale by the time the provider responds and the broker order is submitted.

**F-HIGH-02 — Unknown account identity can bypass the configured account allowlist.**  
The allowlist rejection is conditional on a truthy account ID.

**F-HIGH-03 — Synchronous provider latency can delay application-level protective management.**  
Paper/Live provider calls occur in `run_entry_scan` before `manage_positions`.

**F-HIGH-04 — Candidate identity can change on every scan when setup timestamp provenance is absent.**  
The fallback to current time undermines idempotency.

**F-HIGH-05 — Audit/storage failures are not uniformly fail-closed.**  
Error paths can fail while recording the evidence needed to decide whether retry is safe.

**F-HIGH-06 — Live ownership provenance is not visibly carried into the broker order identity.**  
Application metadata is added to the signal/payload, but the final broker order reference and subsequent broker state do not have a proven candidate/agent identity.

### MEDIUM

**F-MED-01 — Legacy News remains coupled to the AI path despite News being out of scope.**

**F-MED-02 — Paper protection/review invocation is not proven in the real periodic job path.**

**F-MED-03 — Paper portfolio persistence and audit execution recording are not one atomic state transition.**

**F-MED-04 — Candidate/audit namespace is shared across Paper and Live modes.**

**F-MED-05 — Shadow requests have no candidate-level deduplication or bounded backlog policy.**

**F-MED-06 — Complete real-scan integration coverage is missing.**

### LOW / INFO

**F-LOW-01 — Provider construction is not a provider health check.**

**F-LOW-02 — Heuristic payload redaction is not a formal data-classification boundary.**

**F-INFO-01 — The strict response schema and model-independent execution values are strong design choices.**

**F-INFO-02 — The decision package’s lack of direct broker imports is a useful structural defense.**

## 29. Required Fixes Before Shadow

Shadow AI order isolation is structurally present, so the minimum pre-Shadow work is operational and observability-focused:

1. Clearly label Shadow as “AI non-controlling; legacy execution remains active” in runtime status and operator documentation.
2. Add a real `run_entry_scan` Shadow integration test proving that the agent cannot invoke the broker while the legacy path remains observable and separately attributable.
3. Add candidate-level Shadow deduplication and a bounded queue/backpressure policy.
4. Make Shadow provider/audit failures visible in metrics and logs without allowing them to affect legacy execution.
5. Decide and document whether the News coupling is intentionally retained for legacy compatibility or disabled for the new AI boundary; under the requested boundary it should not silently block the AI evaluation.
6. Ensure Shadow requests cannot inherit Live dependencies or configuration through an accidental mode/provider fallback.

## 30. Required Fixes Before Paper Autonomous

Paper should not be treated as safe autonomous validation until the following are addressed:

1. Add an atomic candidate claim/reservation and deterministic per-candidate/per-symbol concurrency control.
2. Make Paper portfolio mutation and audit execution state recoverable as one state machine, including crash tests.
3. Integrate and test periodic `enforce_protection` and position review through the real scheduler/job path.
4. Make setup timestamp provenance mandatory or derive a stable setup identity from a durable source rather than the current clock.
5. Test provider timeout and latency isolation so Paper evaluation cannot delay protective-management work.
6. Define mode-specific namespaces or explicit mode-transition rules for candidate and execution records.
7. Add tests for restart, corrupted state, duplicate workers, and stale/missing quote context.

## 31. Required Fixes Before Live Autonomous

Live must remain disabled until all of the following are implemented and tested in a non-ordering environment first:

1. Replace the read-only active-execution check with a durable, single-use execution reservation/claim that closes the concurrent race.
2. Persist a pre-submit execution intent with candidate, decision, agent, account, symbol, and deterministic order fingerprint before broker submission.
3. Make broker submission and restart recovery idempotent using a durable broker order identity/order reference, followed by reconciliation before any retry.
4. Re-fetch broker positions and open orders immediately before submission; reject on any local/broker mismatch or unknown state.
5. Require a non-empty, verified selected account ID and enforce the Live allowlist even when configuration is incomplete or the broker does not expose the account.
6. Fetch a fresh quote immediately before submission and re-run deterministic price, stale-data, spread, risk, and ownership checks against that quote.
7. Make all audit/storage failures fail closed before submission and make post-submission uncertainty enter an explicit reconciliation state, never an automatic retry.
8. Preserve candidate/agent/decision provenance in the broker order reference and in broker/local reconciliation records.
9. Isolate provider latency from protective position management and prove timeout behavior.
10. Prove protective-stop integrity and broker connectivity/account synchronization at startup and on every autonomous execution cycle.
11. Add race, crash, restart, reconciliation, unknown-account, stale-price, and real-job integration tests; then perform a separately authorized paper and operational validation.

## 32. Final Recommendation

The implementation is a promising controlled decision layer, not a safe autonomous execution system yet.

- **Shadow:** approve only as an AI-observation mode with the explicit caveat that the legacy scan may still place orders. AI-to-broker isolation is present, but legacy execution and asynchronous telemetry need clear separation.
- **Paper Autonomous:** do not approve as a fully autonomous validation authority until candidate claiming, crash consistency, protection scheduling, and latency isolation are addressed.
- **Live Autonomous:** do not enable. The absence of an atomic execution reservation, immediate broker reconciliation, post-LLM quote revalidation, and crash-safe broker identity is disqualifying.

## Explicit final review questions

The answers below refer to the actual reviewed implementation and distinguish the agent path from the whole runtime where necessary.

| # | Review question | Answer | Basis |
|---:|---|---|---|
| 1 | Can Shadow mode ever place an order? | **NO for the AI path; YES for the overall Shadow scan** | The agent Shadow branch does not call the broker, but the legacy `OrderManager` path remains active after Shadow submission. |
| 2 | Can Paper mode call IBKR? | **NO in the reviewed agent path** | Paper routes to `PaperPortfolio`; no direct broker import/call was found in `src/decision`. |
| 3 | Can the provider call the broker directly? | **NO** | Provider and decision modules have no broker/exchange write dependency. |
| 4 | Can the same candidate be processed twice? | **YES** | Sequential duplicate tests pass when an execution link exists, but the check is not atomic and candidate identity may change without a stable timestamp. |
| 5 | Can a restart duplicate an accepted broker order? | **YES / UNVERIFIED in deployment** | A crash after broker acceptance and before `record_execution` is not closed by the current state model. |
| 6 | Is candidate execution race-safe? | **NO** | Read-only `has_active_execution` is separated from execution; no claim/lock/unique active reservation exists. |
| 7 | Can legacy and LLM ownership collide on the same symbol? | **YES** | Visible local ownership is checked, but net broker state can be stale or incomplete and cannot be safely partitioned by local labels. |
| 8 | Is real broker state checked immediately before AI entry? | **NO** | Session synchronization exists, but the autonomous call uses the supplied snapshot and hard-coded `account_synced=True`. |
| 9 | Can LLM latency delay protective position management? | **YES** | Paper/Live provider work is synchronous in entry scan before `manage_positions`. |
| 10 | Does provider failure prevent a new AI entry? | **YES for ordinary provider failure; UNVERIFIED for audit failure** | Provider exceptions normally become `NO_ACTION`, but error-recording failures are not uniformly handled. |
| 11 | Can malformed model output reach execution? | **NO through the strict parser** | Invalid JSON/actions/confidence/ranked values are rejected. |
| 12 | Can the model change quantity? | **NO** | Quantity is not taken from the parsed model response. |
| 13 | Can the model change entry price? | **NO** | Entry comes from deterministic signal/candidate data, not the response. |
| 14 | Can the model change target? | **NO** | Target comes from deterministic signal/candidate data. |
| 15 | Can the model widen or remove a stop? | **NO through the reviewed policy path** | Stop-widening/removal actions are rejected; deterministic protection remains outside the model. |
| 16 | Is price revalidated immediately before order submission? | **NO** | No fresh post-response quote plus second autonomous risk evaluation is present. |
| 17 | Is stale data always blocked? | **NO** | Age is checked when supplied, but missing age defaults to `0.0`, and no post-LLM refresh exists. |
| 18 | Does missing News data avoid blocking the AI path? | **NO in the integrated path** | Empty data commonly maps to low risk, but legacy News calls and exceptions can still gate/abort evaluation. |
| 19 | Does audit failure block Live execution? | **PARTIAL / UNVERIFIED** | Early audit unavailability blocks, but later audit-write failures are not a uniform fail-closed authorization barrier. |
| 20 | Can an old `ENTER` replay after restart? | **YES / UNVERIFIED in deployment** | No pre-submit reservation and no broker-order recovery identity close the crash window. |
| 21 | Are Paper and Live state fully isolated? | **PARTIAL** | Paper does not call IBKR, but audit/candidate identity is shared and mode transitions can suppress or confuse execution state. |
| 22 | Are multi-provider Shadow calls incapable of Live execution? | **YES** | The multi-provider runner is Shadow-only and records decisions without execution. |
| 23 | Is there no silent fallback to another provider or execution path? | **YES for the agent provider** | No automatic provider failover into execution was found; the legacy Shadow path is a separate compatibility path. |
| 24 | Does OFF preserve legacy behavior? | **YES structurally; full-suite compatibility UNVERIFIED** | OFF avoids new provider/audit work; complete runtime behavior remains subject to existing unrelated suite state. |
| 25 | Is Shadow safe for experimentation? | **YES conditionally** | Safe for non-controlling AI observation, not safe to claim that the overall scan cannot place orders. |
| 26 | Is Paper safe for autonomous operation? | **NO** | Race, crash-consistency, protection-scheduling, and latency gaps remain. |
| 27 | Is Live safe for autonomous operation? | **NO** | Critical execution reservation, reconciliation, revalidation, account, and restart gaps remain. |

**Final status: Shadow AI observation is conditionally acceptable; Paper Autonomous and Live Autonomous are not approved by this review.**
