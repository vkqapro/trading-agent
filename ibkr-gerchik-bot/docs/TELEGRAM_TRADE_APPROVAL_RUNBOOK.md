# Telegram Trade Approval Runbook

## Configuration

Set `PAPER_TRADING=true`, `ALLOW_LLM_LIVE_TRADING=false`, and keep
`ALLOW_LLM_IBKR_PAPER_TRADING=false` until the staged test is ready. Configure
`TELEGRAM_APPROVAL_TTL_SECONDS=120`, `TELEGRAM_APPROVAL_ALLOWED_USER_IDS`, and
`TELEGRAM_APPROVAL_ALLOWED_CHAT_IDS`. Values are comma-separated IDs; usernames
are not accepted.

## Safe staged test

1. Run the existing scanner and confirm a READY candidate is persisted.
2. Call the existing Trading MCP `create_setup_approval` with only its setup ID.
3. Send the returned message/buttons through the existing Hermes
   `trading-champion` Telegram gateway.
4. Test unauthorized user/chat and duplicate callbacks; both must fail closed or
   return the already-terminal state.
5. Approve only in dry-run/simulated mode first; inspect the approval and audit
   SQLite database, then verify no direct Telegram-to-broker call exists.
6. Run the backend approval worker with `ApprovalStore.process_approved` and
   `enqueue_existing_worker`; it must invoke the existing `execute_requests`
   worker, never Telegram or the dashboard directly.
7. Enable the existing Paper kill switch only for a controlled IBKR Paper test.

## Inspection and recovery

Use `get_approval_status` and inspect `trade_approvals`,
`trade_approval_events`, and `trade_execution_intents` in the configured
`LLM_DECISION_DATABASE_PATH`. Pending records older than their TTL become
`EXPIRED`. A changed plan becomes `REVALIDATION_FAILED`; create a new approval.
A submission uncertainty must be reconciled against IBKR/open orders before any
retry; never blindly resubmit.

## Immediate disable

Set `ALLOW_LLM_IBKR_PAPER_TRADING=false` and stop the existing execute worker.
Approvals remain auditable, but broker execution must be impossible. For a hard
stop also use the existing global kill switch and disconnect TWS/IB Gateway.
