# Shadow runtime preparation report

## Readiness

SHADOW RUNTIME PREPARATION:
READY WITH CONDITIONS

The repository is prepared for a controlled, stock-only LLM Shadow
observation, subject to the conditions below. No broker job, worker, provider
generation, order, or Live path was started as part of this preparation.

## Conditions that must remain true

- Effective `LLM_AGENT_MODE=shadow`.
- `ALLOW_LLM_LIVE_TRADING=false`.
- `LLM_AGENT_USE_NEWS=false`.
- One explicitly selected provider, currently prepared as `deepseek`.
- An exact non-placeholder `LLM_DECISION_MODEL` is supplied locally.
- `LLM_MULTI_PROVIDER_SHADOW=false`.
- `PAPER_TRADING=true` and `DRY_RUN_MODE=true` for the first runtime.
- The first run is stocks only and uses an existing `open` or `intraday` job.
- The broker is a paper TWS/IB Gateway session with expected host/port.
- The strict preflight passes after Decision Lab initialization.
- No dashboard order endpoint or broad launcher with execution workers is
  used.

The exact operator procedure is in
`docs/SHADOW_RUNTIME_VALIDATION.md`.

## Completed implementation: News timing isolation

`src/jobs/session_utils.py` now separates autonomous AI timing from the
legacy News-dependent strategy timing:

1. When autonomous AI is enabled with `LLM_AGENT_USE_NEWS=false`, it uses an
   explicit neutral context (`risk_level=DISABLED`, empty provider/headline
   lists, source marker `disabled_for_autonomous_ai`). It does not call News.
2. In Shadow mode, the neutral candidate is routed through the technical
   eligibility gate and submitted before the legacy macro/symbol News lookup.
3. Shadow then preserves the legacy route with the real News context. A legacy
   HIGH News result can suppress the legacy execution observation, but cannot
   reject the AI candidate already submitted.
4. In Paper Autonomous mode, the AI handoff remains independent of the legacy
   News path.
5. With the agent off, the prior macro HIGH gate and symbol News behavior stay
   on the legacy path.

No fake headline, fabricated provider hit, or News-derived AI risk is injected
when News is disabled.

## Added validation assets

- `scripts/check_llm_shadow_ready.py` is a read-only configuration and runtime
  preflight. It checks effective mode, all relevant live/paper/dry-run flags,
  provider/model, single-provider Shadow, worker/queue/expiry values, Decision
  Lab parent access, and the required SQLite schema when initialized.
- `tests/test_news_timing_isolation.py` covers blocking News, News exceptions,
  HIGH News, and legacy off-mode behavior.
- `docs/SHADOW_RUNTIME_VALIDATION.md` contains the process-scoped config,
  exact commands, data-only dashboard command, stop conditions, and first-day
  measurement checklist.

The preflight does not edit `.env`, create SQLite state, start an LLM worker,
connect to IBKR, or call an order method. Its optional `--check-provider`
switch makes only a provider-only synthetic `WAIT` request and is not run by
default.

## Effective configuration contract

The first-run contract is:

```text
LLM_AGENT_MODE=shadow
ALLOW_LLM_LIVE_TRADING=false
LLM_AGENT_USE_NEWS=false
LLM_DECISION_PROVIDER=deepseek
LLM_DECISION_MODEL=<exact configured model>
LLM_MULTI_PROVIDER_SHADOW=false
PAPER_TRADING=true
DRY_RUN_MODE=true
```

Operational sizing is taken from the existing configuration, not invented by
this report. The checked baseline is two decision workers, queue depth 16,
and 45-second candidate expiry. The Decision Lab path is
`memory/decision_lab.db`; a fresh checkout may not have initialized it yet.

## Verification evidence

The following focused tests passed after the timing-isolation change:

```text
tests/test_news_timing_isolation.py: 6 passed
focused Shadow/news/decision/order-flow/session suite: 36 passed, 2 warnings
full repository suite: 378 passed, 1 unrelated failure, 4 warnings
```

The read-only preflight was executed with process-scoped Shadow values and
`--allow-uninitialized-db`. It passed all configuration, safety, sizing, and
database-parent checks and reported the expected warning that the current
`memory/decision_lab.db` file is not initialized. The real provider check was
not run, so provider credentials, model availability, latency, and response
quality remain unverified.

The full repository suite was run before final handoff. Its one failure is the
pre-existing unrelated `tests/test_flex_statement.py::FlexStatementTests::test_disabled_configuration_fails_closed`
expectation mismatch (`failed` returned where the test expects `disabled`). It
must remain explicitly identified rather than hidden or reclassified as a
Shadow result.

## Runtime data flow to verify later

```text
stock technical state
        |
        +--> neutral autonomous candidate --> Shadow queue / Decision Lab
        |                                    (before deferred legacy News)
        |
        +--> legacy strategy route --> real News gate --> dry-run legacy path
```

The expected Shadow result is an auditable decision observation and no live
broker order. With `DRY_RUN_MODE=true`, the existing legacy route may still
load paper account/market data, but it must not submit a broker order.

## Known limitations and unresolved evidence

- No provider-only generation was exercised during preparation.
- No TWS/IB Gateway connection was made during preparation.
- No actual Shadow candidate, decision, provider latency, token/cost result,
  queue saturation, or performance result is claimed.
- The current database file is absent/uninitialized in this checkout; the
  strict schema gate must be rerun after the first safe job initializes it.
- The timing contract is covered by deterministic tests. A first-day operator
  record should still capture actual log and Decision Lab timestamps to measure
  the News-call timing effect in the live environment.
- This change does not enable Live mode, create a production worker, add
  multi-provider Shadow, or authorize any order.
