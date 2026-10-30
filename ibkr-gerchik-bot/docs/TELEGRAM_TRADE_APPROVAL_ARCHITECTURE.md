# Telegram Trade Approval Architecture

Phase 1 is LONG-only, production strategies LP1/LP2/PRB1/PRB2, and IBKR PAPER only.

```text
Scanner / cron
  -> DecisionCandidate (authoritative candidates table)
  -> ApprovalStore (SQLite, immutable setup_id + plan_hash)
  -> Hermes trading-champion native Telegram gateway
  -> approve:<approval_id> / reject:<approval_id>
  -> authorized atomic state transition
  -> backend fresh_scanner_revalidate via existing strategy_sources adapters; canonical current plan hash, actionable status, and freshness check
  -> existing AutonomousRiskGate.evaluate_entry (deterministic quantity)
  -> immutable trade_execution_intents (unique approval and idempotency key)
  -> existing order_requests queue / execute_requests worker
  -> existing OrderManager and IBKRClient bracket path
  -> IBKR Paper
```

The existing Trading Bot MCP at `src/mcp/trading_bot_server.py` is extended with
`create_setup_approval`, `approve_setup`, `reject_setup`, and
`get_approval_status`. No second MCP process or broker client is created. MCP
approval methods perform backend scanner preflight revalidation; the backend worker
then owns the existing deterministic risk gate, intent enqueue, broker submission,
and reconciliation. Hermes supplies no setup or market fields.

`setup_id` is the existing persisted candidate ID. `plan_hash` is a canonical
SHA-256 over symbol, strategy, direction, level, entry, stop, target, reward/risk,
and risk-per-share. Telegram callback data contains only the approval ID.
