# Telegram Trade Approval Test Plan

## Lifecycle and authorization

Cover create, approve, reject, TTL expiry, approve/reject after expiry,
duplicate/replayed callbacks, approve-after-reject, reject-after-approve,
allowed identity, unauthorized user, wrong chat, malformed callback, and restart
persistence. Assert compare-and-set transitions and terminal-state stability.

## Revalidation and risk

Use authoritative candidate fixtures to test unchanged setup, missing setup,
non-actionable status, changed plan hash/entry/stop/target, stale data, closed
market, conflicting position/order, risk pass, each existing risk-cap reject,
account mismatch, live account detection, disabled Paper flag, and protective
stop failure. Assert that all rejects create no execution intent and no order.

## Execution and recovery

Test unique execution intent creation, duplicate worker delivery, Paper submit
success/failure/timeout, crash after intent creation, crash after broker submit,
reconciliation with and without an existing order, and no duplicate after retry.
The worker remains the only broker caller.

## End-to-end Paper case

Persist a scanner READY candidate, create an approval, simulate an authorized
Telegram approve, run fresh revalidation, call the existing deterministic risk
gate, create one intent, process through the existing queue worker with a mock
IBKR Paper adapter, and assert the audit EXECUTED result and final Telegram
status. Repeat with a changed hash, risk rejection, and double approve; each
negative case must submit zero orders.
