# Strategy Prompt Layer Implementation Report

## Scope

Implemented the Decision Lab strategy-aware prompt layer without changing
order placement, broker execution, or the locked risk policy. No order was
placed during implementation or validation.

## Changed components

- `src/decision/strategy_definitions.py`: versioned definitions, hashes,
  calculator provenance, signal semantics, and live parameter snapshots.
- `src/decision/applicability.py`: deterministic same-symbol position gate for
  position-management signals.
- `src/decision/prompt_presets.py`: immutable built-ins and append-only custom
  preset/version storage.
- `src/decision/prompt_compiler.py`: authoritative source-aware compiler and
  locked system policy.
- `src/decision/models.py`: compiled prompt metadata and duplicate-free provider
  payload; token-count sanitizer preserves diagnostic usage.
- `src/decision/agent.py`: applicability check before provider and audit fields.
- `src/decision/provider.py`: 900-token default, usage/finish-reason capture,
  and fail-closed `OUTPUT_TRUNCATED` handling.
- `src/decision/audit.py`: persisted definition/preset/applicability/compiler
  metadata and inspector visibility.
- `src/decision/strategy_control.py` and `strategy_controller.py`: prompt
  selection and applicable/provider run counters.
- `dashboard_react/server.py`: definition, preset, and no-provider preview APIs.
- `dashboard_react/index.html`: definition/parameter/preset/preview panels,
  dirty-edit run lock, counters, and inspector metadata.
- `.env.template`: `LLM_DECISION_MAX_COMPLETION_TOKENS=900`.

## Verification

Focused tests cover source definitions, current parameters, all applicability
outcomes, analysis-only zero normalization, duplicate-free compiled requests,
preset versioning/disable behavior, provider usage and truncation, agent
provider bypass for non-applicable exits, audit persistence/sanitization, and
controller idempotence/counters.

The final browser UAT must verify the live Decision Lab panel, read-only prompt
preview, inspector rendering, and absence of client errors. Until that browser
pass is complete, the overall release status remains `NOT READY` even though
the focused backend suite is passing.
