# Decision Lab provider diagnostics report

## Status

Implemented as a narrow diagnostics/UI change. No trading worker was started,
no provider request was sent, no broker or exchange connection was opened, and
no order was placed during implementation or testing.

## Files changed

- `dashboard_react/server.py`
  - extended `GET /api/decision-lab/status` with sanitized runtime
    configuration, safety flags, sizing values, connection state, and bounded
    provider-test result fields;
  - added `POST /api/decision-lab/test-provider`;
  - added a direct synthetic provider request builder and safe error
    categorization;
  - deliberately does not import or invoke the autonomous agent, risk gate,
    OrderManager, broker client, exchange client, or paper portfolio from the
    diagnostic endpoint.
- `dashboard_react/index.html`
  - added a Decision Lab provider-status card;
  - added MODE, PROVIDER, MODEL, CONNECTION, and NEWS fields;
  - added actual safety badges for Shadow/Live AI, News, paper broker, and
    dry-run state;
  - added the `TEST LLM CONNECTION` button with `TESTING...` state, retryable
    error state, latency, timestamp, and diagnostic explanation;
  - changed the provider-health empty state to distinguish no trading
    observations from provider configuration/test state.
- `tests/test_decision_lab_provider_diagnostics.py`
  - status sanitization checks;
  - successful direct-provider WAIT diagnostic;
  - timeout, HTTP 401, invalid JSON, and unknown-model failures;
  - explicit no-call spies for DecisionAudit, OrderManager, PaperPortfolio,
    and IBKR order placement.
- `docs/SHADOW_RUNTIME_VALIDATION.md`
  - added the Decision Lab provider-test section and interpretation boundary.
- `docs/DECISION_LAB_PROVIDER_DIAGNOSTICS_REPORT.md`
  - this report.

Existing trading strategies, entry/stop/target calculations, position sizing,
autonomous risk logic, paper portfolio behavior, broker execution, IBKR
settings, and crypto execution were not modified.

## API contract

### `GET /api/decision-lab/status`

The response now includes at least:

```json
{
  "mode": "shadow",
  "provider": "deepseek",
  "model": "deepseek-flash",
  "news_enabled": false,
  "multi_provider_shadow": false,
  "decision_workers": 2,
  "workers": 2,
  "queue_depth": 16,
  "candidate_expiry": 45.0,
  "connection_status": "not_tested",
  "last_provider_test_status": "not_tested",
  "last_provider_test_at": null,
  "last_provider_latency_ms": null,
  "last_provider_error": null
}
```

It also reports sanitized paper/dry-run/live-warning state and the existing
Decision Lab counters. It does not return API keys, authorization headers,
account credentials, or environment contents.

### `POST /api/decision-lab/test-provider`

The endpoint calls `build_provider(SETTINGS.decision_agent)` once and then
calls only `provider.decide(...)` once. The request is synthetic and
non-trading:

```text
symbol: TEST
strategy: connectivity_check
asset_class: diagnostic
direction: none
allowed_actions: WAIT only
metadata: diagnostic=true, non_trading=true
```

No `DecisionAudit` object is created by this endpoint and no provider-health
database row is required. The diagnostic state is kept in the dashboard
process for the status card. Existing provider-health rows from actual
candidate decisions remain available through the existing provider endpoint.

Success returns `ok=true`, `connection_status=connected`, provider/model,
timestamp, bounded latency, and a fixed success message. Failure returns
`ok=false`, `connection_status=error`, provider/model, timestamp, bounded
latency, and one sanitized category such as `HTTP 401`, `timeout`,
`invalid JSON response`, or `model unavailable or not configured`.

## UI behavior

The Decision Lab immediately shows the effective runtime configuration rather
than hard-coded provider names. Before a click, the connection state is
`NOT TESTED`. During the request, the button becomes `TESTING...` and repeat
clicks are disabled. A valid strict `WAIT` response changes the state to
`CONNECTED`; failures change it to `ERROR` and leave the button retryable.

The provider-health panel separately shows:

- configured provider;
- configured model;
- runtime test state;
- last latency and timestamp;
- sanitized error reason when applicable;
- number of trading observations;
- existing provider-health observations, if any.

Thus configured, tested, and used-for-trading are separate states.

## Safety boundary

The diagnostic flow is:

```text
Decision Lab button
    -> POST /api/decision-lab/test-provider
    -> build_provider(config)
    -> provider.decide(synthetic WAIT-only request)
    -> sanitized status response
```

It does not enter:

```text
AutonomousGerchikAgent
AutonomousRiskGate
OrderManager
IBKRClient
OKXClient
PaperPortfolio
DecisionAudit candidate/decision recording
execution reservations or links
```

The endpoint does not create a candidate row, trade decision, risk row,
execution link, reservation, paper position, broker reference, or exchange
order. It also has no UI control that changes Live, paper, News, or dry-run
configuration.

## Tests

The dedicated diagnostics test file passed:

```text
4 passed, 2 warnings
```

The focused dashboard/Decision Lab/decision suite passed:

```text
42 passed, 2 warnings
```

The tests assert that:

- status returns provider, model, mode, News state, queue/worker/expiry
  values, and no secret-like fields;
- a success invokes the provider exactly once and accepts only `WAIT`;
- the diagnostic payload is explicitly non-trading;
- DecisionAudit, OrderManager, PaperPortfolio, and IBKR order methods are not
  called;
- timeout, HTTP 401, invalid JSON, and missing/unknown model failures produce
  bounded safe messages.

The full repository suite was also run with a process-scoped
`LLM_AGENT_MODE=off` override so the invalid annotated value currently present
in `.env` could not contaminate unrelated tests:

```text
382 passed, 1 unrelated failure, 4 warnings
```

The remaining failure is
`tests/test_flex_statement.py::FlexStatementTests::test_disabled_configuration_fails_closed`,
which returns `failed` where the existing test expects `disabled`. It is not
part of this diagnostics change.

## Manual verification steps

Use the data-only dashboard server, not the broad launcher that starts workers:

```powershell
python -m uvicorn dashboard_react.server:app --host 127.0.0.1 --port 8550
```

With the intended process-scoped Shadow configuration loaded:

1. Open `http://127.0.0.1:8550` and select Decision Lab.
2. Confirm the card shows actual MODE, PROVIDER, MODEL, `NEWS: DISABLED`, and
   `CONNECTION: NOT TESTED` before the click.
3. Confirm `SHADOW`, `LIVE AI DISABLED`, `NEWS DISABLED`, `PAPER BROKER`, and
   `DRY RUN` badges when those effective values are configured.
4. Click `TEST LLM CONNECTION` once and verify `TESTING...` disables the
   button.
5. With a reachable configured provider, verify `CONNECTED`, latency, and
   timestamp. With a deliberately unavailable or missing provider, verify
   `ERROR` and a sanitized reason.
6. Refresh the Decision Lab. Refresh must call status/decision/provider reads
   only; it must not issue another provider request.
7. Confirm Candidates, Decisions, Risk Rows, Executions, and Paper Positions
   remain unchanged by the diagnostic click.

The data-only server was manually started with process-scoped synthetic Shadow
configuration, `GET /api/decision-lab/status` returned HTTP 200 with the
expected mode/provider/model/News/connection fields, and `GET /` returned the
Decision Lab UI marker. The temporary server was stopped afterward. The
provider button itself and interactive browser rendering were not exercised;
those require the operator's configured provider credentials and a deliberate
dashboard session. No provider-connectivity result is claimed here.

## Known limitations

- Diagnostic state is held in the running dashboard process and resets when
  that process restarts. It is intentionally not written to the Decision Lab
  trading/audit database.
- A successful diagnostic validates transport/authentication, model
  availability, bounded response time, and strict response-schema compatibility
  only. It says nothing about trading quality, candidate generation, risk
  quality, broker execution, or profitability.
- The frontend is an inline Babel/React page. Automated backend tests and
  targeted source inspection cover the new behavior; interactive browser
  rendering still requires the manual steps above.
- The diagnostic endpoint reports provider errors using safe categories and
  intentionally omits raw HTTP bodies, authorization material, and secrets.
- The current local `.env` contains an annotated `LLM_AGENT_MODE` value rather
  than a valid enum value. It was not modified. The Shadow runbook's
  process-scoped assignments are required until that local configuration is
  corrected by the operator.
