# Decision Lab decision inspector

Decision Lab exposes an auditable, read-only view of the persistent decision
worker. The inspector is an observability surface; it does not create a
provider client, connect to IBKR, refresh market data, reserve an execution, or
place an order.

## Production prompt and provenance

The production prompt is assembled in
`src/decision/provider.py::build_system_prompt`. Its current version is
`decision-v1`. The prompt tells the provider to use only deterministic values
and the supplied action menu, not invent prices or indicators, not access the
broker, and return the structured decision fields:

```json
{
  "action": "ENTER|WAIT|REJECT",
  "confidence": 0.0,
  "ranked_actions": [["WAIT", 0.7]],
  "reason_codes": ["..."],
  "summary": "..."
}
```

The user message is the compact JSON serialization of
`DecisionRequest.to_prompt_payload()`. It contains the immutable candidate and
market snapshot, deterministic context, provider/model identity, prompt
version, and the allowed action menu. `HttpDecisionProvider` stores a
sanitized observation of the exact system prompt, structured user payload,
provider response text, parsed response JSON, status, error category, and
latency. Authentication headers are never stored.

The audit row preserves the distinction between:

- `model_action`: what the provider returned;
- `effective_action`: what the deterministic system allowed to continue;
- `decision_origin`: why the effective result was reached.

Typical origins are `MODEL`, `PROVIDER_FAILURE`,
`INVALID_PROVIDER_RESPONSE`, `ANALYSIS_ONLY_VETO`, `SYSTEM_VETO`,
`RISK_VETO`, and `STARTUP_VETO`.

## Action menus and strategy provenance

The strategy source remains authoritative about whether an executable entry
plan exists:

- Stock Screener executable entry candidates: `ENTER`, `WAIT`, `REJECT`.
- BMSB and Gaussian analysis candidates: `WAIT`, `REJECT`.
- Position-management signals use their source-specific position action menu.

If an analysis-only candidate receives `ENTER`, the response is still retained
as `model_action=ENTER`; the system records a risk row and sets
`effective_action=NO_ACTION`, `decision_origin=ANALYSIS_ONLY_VETO`. This makes
the provider output visible without granting it authority to enter.

Gaussian/BMSB rows therefore provide evidence about the model's requested
`WAIT`/`REJECT` response or a provider failure, while the source metadata
(`strategy_source`, `source_signal`, `candidate_class`, scan/bar timestamps,
and `execution_eligible`) explains why an entry was or was not possible.

## Failure handling

Provider failure and invalid response are fail-closed. The decision row has no
effective action and no execution link:

- transport/timeout/auth/model/rate-limit failures become
  `decision_origin=PROVIDER_FAILURE` and `effective_action=NO_ACTION`;
- malformed JSON or schema errors become
  `decision_origin=INVALID_PROVIDER_RESPONSE` and
  `effective_action=NO_ACTION`;
- downstream deterministic gates retain the model action but overwrite only
  the effective outcome and record the policy reason.

Legacy rows created before inspector fields existed display
`LEGACY / NOT RECORDED` in the UI rather than implying that the value was
`WAIT`, `UNKNOWN`, or a provider error.

## Audit schema and API

`model_decisions` stores the following inspector fields in addition to the
original decision/audit fields:

```text
run_id
prompt_version
allowed_actions_json
prompt_payload_json
provider_status
provider_error_category
raw_model_text_sanitized
parsed_response_json
model_action
effective_action
decision_origin
system_result_json
```

The list endpoint is intentionally lightweight:

```text
GET /api/decision-lab/decisions
```

It returns table-level provenance and action fields but not prompt payloads,
raw model text, or parsed response bodies. Full detail is fetched only when an
operator selects a row:

```text
GET /api/decision-lab/decision/{decision_id}
```

Run-level counters and lightweight decision summaries are available at:

```text
GET /api/decision-lab/run/{run_id}
```

Run counters distinguish model ENTER/WAIT/REJECT, provider failures, system
vetoes, risk vetoes, and executions. `PARTIAL` means the bounded run finished
with one or more per-candidate failures/vetoes; it does not mean that an
unverified decision was executed.

## Sanitization and retention

The inspector removes credential/account-sensitive mapping keys and redacts
authorization/API-key-like text and broker account patterns before persistence
or API rendering. The dashboard does not receive a full account ID, provider
secret, authentication header, or filesystem database path. Prompt and raw
response bodies are available only through the single-decision detail read and
remain subject to the same sanitization. The existing SQLite audit retention
policy applies; no new unbounded store is introduced.

The Decision Lab frontend distinguishes the LLM diagnostic connection from
the autonomous worker's provider health. Polling status, decision lists, and
inspector reads are dashboard-only reads; none of them performs an IBKR
connection.
