# Hermes Migration Plan

## Migration principle

Migrate presentation and analysis first, then orchestration convenience, while leaving deterministic trading and operational safety local. Do not remove Hermes, Telegram, or any execution component during this audit.

| Profile | Replacement assessment | Keep local? | Safe first step |
|---|---|---:|---|
| Daily Trader | ChatGPT can reproduce persisted scan summaries and explain Radar versus scanner context. It cannot safely run a fresh mutating scan merely to answer a question. | Persisted service and scanner: yes; Hermes presentation: optional | Build a read-only report adapter using completed scan IDs |
| Pre-Trade Reviewer | ChatGPT can produce a sourced review and identify missing evidence. It must not convert review into approval or bypass Risk Gate. | Canonical validation and approval state: yes | Compare ChatGPT report against existing reviewer for the same candidate |
| Trading Analyst | Mostly replaceable for narrative reporting because deterministic reports and journals already exist. Metrics must remain source-backed. | Journal, reconciliation, report calculations: yes | Generate a parallel report and reconcile counts, fills, P&L, R, MFE, and MAE |
| Trading Ops Supervisor | Interactive replacement is possible for one-off checks. Continuous health, restart ownership, lock handling, and recovery still require the resident supervisor. | Yes | Have ChatGPT read `service-status` output and report blockers |
| Trading Orchestrator | Partially replaceable as a conversational coordinator. It cannot be the always-on scheduler or execution owner. | Local lifecycle and scheduler: yes | Route read-only requests through one adapter and retain Hermes as fallback |

## Phases

### Phase A: Parallel observation

Expose read-only health, scan, Radar, candidate, journal, and report projections. Run ChatGPT-generated Daily Trader and Analyst reports alongside Hermes. Compare exact source IDs and counters. No writes.

### Phase B: Conversational analysis

Allow ChatGPT to answer operator questions and produce pre-trade reviews from persisted data. Require explicit evidence blocks and unresolved-data reporting. Keep Hermes delivery and Telegram approvals unchanged.

### Phase C: Notification simplification

If parallel results reconcile over a defined paper period, reduce Hermes to scheduled notifications and failure alerts. Keep the local supervisor, scheduler, worker, and approval store.

### Phase D: Optional approval adapter

Only after the read-only adapter is proven, add a human-reviewed workflow that writes to the existing approval state machine. The workflow must preserve candidate hash, source scan ID, current revalidation, and account identity. Never create a second execution path.

### Phase E: Retirement decision

Retire individual Hermes presentation profiles only after rollback, outage, stale-data, duplicate-request, and reconciliation tests pass. Keep an operational fallback until the local service has independently demonstrated paper reliability.

## Telegram assessment

Telegram can be reduced to notifications, but not immediately. The repository has durable Telegram delivery acknowledgement, approval TTL, revalidation, rejection, and reconciliation paths. Removing it before another approval channel is integrated would remove a tested human-control boundary. The safest outcome is to make Telegram optional for alerts while retaining it for explicit approvals until a replacement has equivalent audit semantics.

## Rollback

Rollback is a routing/configuration decision: point reports and notifications back to Hermes, leave the local execution core untouched, and preserve all source artifact IDs. No strategy or order code should be changed to roll back the conversational layer.
