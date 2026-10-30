# Decision Lab: Strategy-Aware Prompt Layer

Decision Lab now treats the deterministic source calculation as the authority
for strategy meaning. The provider receives a compiled, sanitized prompt that
contains the selected source definition, the current runtime parameter
snapshot, the selected versioned prompt preset, candidate evidence, applicable
position context, and the allowed response actions.

## Source definitions

The code-owned definitions live in `src/decision/strategy_definitions.py`.
They are versioned and hashed, and their provenance points at the existing
calculators rather than reimplementing them:

- Gaussian uses the existing HLC3/true-range recursive channel in
  `dashboard_react/server.py`. Current parameters are period 144, four poles,
  multiplier 1.414, near threshold 1.0%, and two recent signal days.
- BMSB uses the existing weekly 21 EMA / 20 SMA band and its two-day recent
  cross and 0.75% near-cross settings.
- Stock Screener describes the existing LP1/LP2/PRB1/PRB2 detector, cleaned
  Wilder ATR(14), pivot lookback, level-touch, and READY/pending-entry rules in
  `dashboard_react/market_screener.py`.
- Gerchik Router consumes persisted output from the existing router and
  detector registry; Decision Lab does not launch a second router.

The API exposes the definition and current parameters at
`GET /api/decision-lab/strategy-definitions`. Parameters are resolved from the
same runtime constants/calculators used by the application.

## Applicability gate

`src/decision/applicability.py` runs before the provider request. A Gaussian or
BMSB position-management signal is `NOT_APPLICABLE` when there is no nonzero
same-symbol position in the supplied current/broker position context. The
agent records `SYSTEM_FILTER`, `NOT_APPLICABLE`, and `NOT_CALLED`; no provider
request is made. Genuine conflicting positions are not silently discarded.

Analysis-only zero fields are normalized to `null` by
`src/decision/prompt_compiler.py`. The compiler also removes broker/account
secrets from position context and preserves only safe position evidence.

## Presets and compiler

Built-in presets are immutable and read-only. Operators can duplicate a
built-in, edit the custom copy, and save append-only new preset versions. The
runtime store is `memory/decision_lab_prompt_presets.json` (or the configured
runtime directory). Prompt text is limited to 4,000 characters and rejects
credential/account field names.

`compile_decision_prompt()` is the single compiler used by the agent, the
read-only preview endpoint, and tests. It produces the same structured content
for the real provider and preview. `DecisionRequest.to_prompt_payload()` sends
the compiled prompt as one payload and does not repeat the candidate and
snapshot as duplicate top-level request fields.

The locked policy remains code-owned. A preset can guide analytical emphasis,
but cannot override source semantics, parameters, candidate class, allowed
actions, deterministic prices, risk, or the response schema.

## Provider boundary

The default completion budget is now 900 tokens, configurable with
`LLM_DECISION_MAX_COMPLETION_TOKENS` between 600 and 2,000. Provider
observations persist usage and finish reason. A `length`, `max_tokens`, or
`max_completion_tokens` finish reason is classified as `OUTPUT_TRUNCATED` and
fails closed; it is never converted into a decision.

The Decision Inspector exposes the definition version/hash, parameter snapshot,
preset ID/version/hash, applicability, compiled prompt, finish reason, token
usage, provider response, and downstream policy result. Full account IDs and
secrets are sanitized before persistence and before the dashboard response.

## Read-only UI/API behavior

The Decision Lab UI shows the strategy definition, current parameters, active
preset, immutable/editable state, preview result, and run counters for
applicable, not-applicable, provider-success, and provider-failure outcomes.
Editing a prompt blocks RUN ANALYSIS until the edit is saved. Preview is
explicitly marked `PROVIDER CALLED: NO`.

The dashboard status and preview paths do not connect to IBKR. The persistent
worker remains responsible for runtime analysis and broker safety state; the
standalone paper preflight remains an independent read-only diagnostic.
