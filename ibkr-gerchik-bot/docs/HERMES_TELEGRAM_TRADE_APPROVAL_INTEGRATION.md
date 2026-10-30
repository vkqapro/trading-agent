# Hermes Native Telegram Trade Approval Integration

## Boundary

Hermes `trading-champion` is a transport/presentation controller. It sends the
message returned by the existing Trading MCP and forwards only Telegram event
identity plus an opaque `approval_id`. It never sends trade parameters or calls
IBKR.

The repository's existing Trading MCP is `src/mcp/trading_bot_server.py`; its
approval tools are:

- `create_setup_approval(setup_id, scanner_run_id?, telegram_chat_id?, telegram_message_id?)`
- `approve_setup(approval_id, telegram_user_id, telegram_chat_id, telegram_callback_id?)`
- `reject_setup(approval_id, telegram_user_id, telegram_chat_id, telegram_callback_id?)`
- `get_approval_status(approval_id)`

## Native callback contract

Use inline callback data exactly as:

```text
trade:approve:approval_<32 lowercase hex characters>
trade:reject:approval_<32 lowercase hex characters>
trade:details:approval_<32 lowercase hex characters>
```

`parse_callback_data`, `telegram_controls`, and `telegram_status_message` in
`src/decision/telegram_approval.py` are deterministic helpers for the native
Telegram adapter. DETAILS calls `get_approval_status` only and never mutates
state.

## Flow

```text
scanner READY / ENTRY_SIGNAL
 -> Hermes calls create_setup_approval with setup_id only
 -> Hermes sends returned READY text and native inline controls
 -> Telegram callback parser extracts action + approval_id
 -> Hermes forwards Telegram user/chat/callback IDs to the existing Trading MCP
 -> backend reloads setup and runs fresh_scanner_revalidate
 -> backend plan hash/actionability/freshness checks
 -> existing deterministic risk gate and execution worker
```

Repeated `setup_id + plan_hash` creation is deduplicated by the backend. Replayed
callbacks are terminal/idempotent. MCP errors, malformed callbacks, unknown
approvals, unauthorized identities, and expired approvals fail closed.

## Profile and gateway

Use the already configured `trading-champion` profile through the default host
gateway. Do not install a second Telegram bot, gateway, polling loop, MCP
server, or approval service.

## Profile-local plugin

The native adapter is implemented at:

`C:\Users\Vitaly\AppData\Local\hermes\profiles\trading-champion\plugins\telegram-trade-approval`

It registers a scoped `^trade:` `CallbackQueryHandler` through
`ctx.register_platform_handler("telegram", factory)`. It also exposes the
`telegram_trade_approval_ready` tool used by `/trading-scan-long`: the tool
creates/reuses the backend approval, reads its authoritative expiry, and sends
one native card through the already-connected Telegram adapter. The profile
config grants this plugin an MCP allowlist containing only `trading_bot_data`.
Telegram-origin sessions are mapped to their originating chat; scheduler calls
fall back to the configured plugin home chat.

Enable/reload with:

```text
hermes -p trading-champion plugins enable telegram-trade-approval
hermes -p default gateway restart
```

No Hermes core patch, second gateway, Telegram bot, polling loop, MCP server, or
LLM callback interpretation is used.

## Runtime diagnostic findings

The live `trading-champion` profile resolves skills from:

`C:\Users\Vitaly\AppData\Local\hermes\profiles\trading-champion\skills`

A stale duplicate existed in `~/.hermes/skills`; it did not contain the
approval-card section. The profile-local copy is now updated and is the one used
by the Telegram gateway. The profile tool summary confirms that
`telegram-trade-approval`, `strategy_scanner`, and `trading_bot_data` are enabled
for Telegram.

The Strategy Scanner artifact contract currently returns `ENTRY_SIGNAL` rows
without `setup_id` or `plan_hash`. The compact response now exposes these fields
when the upstream artifact provides them and logs a fail-closed
`canonical_identity_status: missing` otherwise. It does not invent identifiers.
The known ALOY smoke candidate demonstrated this exact condition, so no
approval call or buttons are made until the existing canonical identity bridge
provides both immutable values.

## Safety / kill switch

Keep `PAPER_TRADING=true`, `ALLOW_LLM_LIVE_TRADING=false`, and
`ALLOW_LLM_IBKR_PAPER_TRADING=false` until controlled Paper validation. Never
place bot tokens, credentials, full setup JSON, quantity, prices, or account
fields in callback data.
