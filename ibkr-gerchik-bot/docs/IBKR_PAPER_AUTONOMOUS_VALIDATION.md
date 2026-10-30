# IBKR Paper Autonomous validation runbook

This is a controlled manual-validation runbook for
`LLM_AGENT_MODE=ibkr_paper_autonomous`. It is intentionally not executed by
the implementation task. No implementation test or preflight command places
an order.

## Preconditions

1. Start the complete local bundle with `run_react_dashboard.cmd`. It starts
   market data, the execute-request worker, crypto, the resident Autonomous
   Stock / LLM worker, and FastAPI. Do not launch separate `open` or
   `intraday` commands; the resident worker owns those transitions.
2. Keep the existing deterministic stock path available and confirm the
   dashboard is in its expected read-only operational state. The worker may
   show `RUNNING` while new LLM entries remain `BLOCKED`.
3. Use an IBKR Paper TWS/Gateway session and confirm the account shown by the
   broker API is the intended `DU...` account. The runtime must obtain this
   from account-summary evidence; a port number alone is insufficient.
4. Add only the intended Paper account ID to
   `LLM_IBKR_PAPER_ACCOUNT_ALLOWLIST`. Keep the live allowlist separate.
5. Initialize or verify the Decision Lab database, then run the read-only
   preflight:

   ```powershell
   python scripts/check_llm_ibkr_paper_ready.py
   python scripts/check_llm_ibkr_paper_ready.py --check-broker --check-provider
   ```

6. The intended controlled-validation configuration is:

   ```text
   LLM_AGENT_MODE=ibkr_paper_autonomous
   ALLOW_LLM_IBKR_PAPER_TRADING=true
   PAPER_TRADING=true
   DRY_RUN_MODE=false
   ALLOW_LLM_LIVE_TRADING=false
   ALLOW_LIVE_TRADING=false
   LLM_AGENT_USE_NEWS=false
   LLM_IBKR_PAPER_ACCOUNT_ALLOWLIST=<the verified DU account>
   LLM_IBKR_PAPER_MAX_OPEN_POSITIONS=1
   LLM_IBKR_PAPER_MAX_TRADES_PER_DAY=3
   LLM_IBKR_PAPER_RISK_PER_TRADE_PCT=0.10
   ```

   Change runtime configuration only through the approved local deployment
   procedure. Do not place these values in a chat message containing secrets.

## Controlled observation

The worker remains alive overnight, on weekends, and whenever TWS/provider
dependencies are temporarily unavailable. During those periods the dashboard
reports `MARKET CLOSED`, `BLOCKED`, or `DEGRADED`; it does not silently exit.
When the next weekday session becomes eligible it re-runs preflight and
resumes only after broker/account/reconciliation gates pass. The current
calendar is weekday/time based and has no exchange holiday data, so holidays
remain an operator verification limitation.

Before allowing a candidate to reach the worker, record the broker account
identity, flat positions, no conflicting AAPL/open-entry order state, current
quote timestamp, spread, and the Decision Lab reservation state. Then permit
one known Gerchik stock candidate and verify:

- the provider response is a structured decision and News is absent from its
  context;
- the audit rows contain the candidate, decision, deterministic risk quantity,
  reservation, order reference, and mode;
- the existing Gerchik parent/stop/target bracket is used;
- the order appears in the Paper account with the expected provenance;
- the dashboard counts it under `IBKR PAPER EXECUTIONS`, not internal Paper or
  live executions;
- no second submission occurs after a worker restart or an uncertain response.

Do not validate by trying a live account, by changing the mode from the UI, or
by disabling any guard. Do not infer a fill from an order acknowledgement;
reconcile the broker order, executions, and positions.

## Immediate stop conditions

Stop the validation and leave the mode disabled if any of the following occurs:

- account identity is live, unknown, multiple, or not allowlisted;
- `PAPER_TRADING`/`DRY_RUN_MODE` or either live permission has an unexpected
  value;
- the quote is stale/unknown, same-symbol broker state exists, or the audit DB
  is unavailable;
- a provider error, risk veto, reservation conflict, or reconciliation-required
  result is bypassed;
- an order lacks the agent/candidate/decision provenance or a second order is
  attempted;
- the dashboard reports a live execution, internal Paper execution, or an
  unclassified execution for the Paper broker test.

## Evidence to retain

Retain the redacted preflight output, configuration checksum without secrets,
Decision Lab rows, broker order/execution/position records, dashboard status,
and restart/reconciliation observations. This evidence supports only Paper
validation. It is not evidence for live readiness.
