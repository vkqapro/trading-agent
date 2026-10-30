# IBKR Paper Autonomous implementation report

## Scope and verdict

Implemented the fail-closed `ibkr_paper_autonomous` mode while preserving
`off`, `shadow`, isolated `paper_autonomous`, and guarded `live_autonomous`.

Final implementation verdict:

**IBKR PAPER AUTONOMOUS: READY WITH CONDITIONS**

The code and mocked safety tests are ready for controlled manual validation.
No IBKR order, fill, worker-mode switch, or restart acceptance run was
performed during implementation. The remaining conditions are therefore
operational Paper-account evidence and the manual one-order/restart runbook,
not a claim of live readiness.

## Delivered controls

- Added `AgentMode.IBKR_PAPER_AUTONOMOUS` and configuration for its dedicated
  permission, separate account allowlist, one-position cap, three-trade daily
  cap, and 0.10 percent risk cap.
- Added fail-closed validation for Paper mode, non-dry-run execution, disabled
  LLM and legacy live permissions, verified account identity, allowlisting,
  provider health, audit availability, and unresolved reservations.
- Added read-only IBKR account identity evidence based on account summary and a
  conservative `DU` Paper classification; no socket-port inference.
- Reused the existing fresh broker reconciliation, same-symbol conflict gate,
  deterministic risk sizing, atomic reservation namespace, order reference,
  and Gerchik bracket `OrderManager` path.
- Passed the deterministic risk quantity into the guarded order-manager call,
  with a final quote-status/age/spread/chase check immediately before submit.
- Added separate `ibkr_paper_executed` audit/status accounting and Decision Lab
  fields/badges for real broker Paper execution, no live money, disabled AI
  live trading, and disabled News. Account IDs are not rendered by the UI.
- Added `scripts/check_llm_ibkr_paper_ready.py`, which is read-only by default
  and has optional provider-only and broker-read-only checks.
- Added this runbook and expanded the architecture documentation.

## Verification performed

- Existing decision model, agent, audit, and policy focused tests passed before
  the new suite was added.
- `tests/test_ibkr_paper_autonomous.py` covers mode/config gates, Paper versus
  live/unknown account separation, exactly-one safe submission, provider
  health separation, WAIT/stale-quote vetoes, and restart reconciliation with
  no resubmission. The suite passed: **8 passed**.
- Python compilation passed for `src`, `dashboard_react/server.py`, and
  `scripts`.
- The dashboard/worker processes were not stopped or restarted by this
  implementation. The last observed dashboard configuration was Shadow; a
  final read-only port check found no listener on `127.0.0.1:8550`, so current
  runtime availability is not claimed. No `.env` file was modified.

## Conditions for manual validation

Run `docs/IBKR_PAPER_AUTONOMOUS_VALIDATION.md` only after the intended Paper
account is connected and its account-summary identity is allowlisted. Run the
preflight before enabling the mode and stop immediately on any condition in
the runbook. A successful Paper test does not authorize or imply live
trading.
