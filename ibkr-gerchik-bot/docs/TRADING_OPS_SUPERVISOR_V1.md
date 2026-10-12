# Trading Ops Supervisor V2

Trading Ops Supervisor is a narrow Hermes SRE profile for weekday morning
startup, bounded safe self-healing, and local readiness reporting. It is not authorized to create, approve,
reject, modify, or execute trades.

## Architecture

Hermes `trading-ops-supervisor` invokes the repository-scoped
`tools/trading_platform_ops.py` client. The client calls the existing process,
heartbeat, HTTP, SQLite, and `src.brokers.ibkr.IBKRClient` seams. The LLM only
summarizes the structured result.

The dependency order is: preflight/time classification; read-only database
check; IBKR process/socket/adapter/account identity check; dashboard and the
existing individual worker processes; heartbeat/port verification; read-only
Radar, scanner, Daily Trader, and Pre-Trade Reviewer API checks; safety flags;
final readiness state. Pre-market and closed-market operation is allowed.

## Profile and skills

The source profile is `config/hermes/trading-ops-supervisor/`. It contains
`SOUL.md` and the narrow skills `trading-platform-morning-startup`,
`trading-platform-health`, `trading-platform-service-status`, and
`trading-platform-readiness-recheck`. The profile
inherits the existing local Unslouth model configuration and exposes only the
Hermes skills and terminal toolset needed to invoke the fixed client. It has no
Telegram, Slack, email, webhook, broker-order, approval, or repository-editing
skill.

## Startup and idempotency

The client recognizes the existing dashboard on port 8550 and workers by their
authoritative command-line patterns and runtime files. It starts only a missing
singleton. Zero instances are started; one is verified; more than one becomes
`DUPLICATE_PROCESS` with PIDs and no automatic termination. The aggregate
`run_react_dashboard.cmd` is used only for a missing dashboard when no worker
would be collided with; a partial stack is reported `ACTION_REQUIRED`. Existing
worker launchers are otherwise reused. No service is automatically restarted
merely because its process is unhealthy.

## IBKR handling

The supervisor detects TWS/IB Gateway processes, checks the configured socket,
then uses the existing `IBKRClient` connection and `get_account_identity()`.
That authoritative method uses managed accounts and ignores aggregate `All` and
`BASE` rows. Account mismatch, adapter failure, or missing manual
authentication is `ACTION_REQUIRED`; credentials are never automated or stored.
Paper identity must be verified before broker health is `HEALTHY`, and broker
failure prevents an overall `READY` result.

## States and safety

Overall states include `READY_FOR_MARKET_OPEN`, `WAITING_FOR_IBKR`, `PARTIAL`,
`ACTION_REQUIRED`, and `FAILED`.
Services use stable machine-readable states including `HEALTHY`, `STARTED`,
`NOT_READY`, `DUPLICATE_PROCESS`, `ACTION_REQUIRED`, and `FAILED`.
`PAPER_TRADING` and `DRY_RUN_MODE` are observed, never changed. A mismatch is
`SAFETY_CONFIGURATION_MISMATCH`. Every workflow result includes
`orders_submitted: 0`, `approvals_created: 0`, and
`execution_intents_created: 0`.

## Schedule

Hermes Scheduler job: **Trading Morning Startup**, profile
`trading-ops-supervisor`, skill `trading-platform-morning-startup`, local
delivery only, weekdays at `07:00 America/New_York`, cron `0 7 * * 1-5`. The
existing job `27f450c3ab7c` is edited in place.

## Manual validation

```powershell
python -B tools/trading_platform_ops.py health
python -B tools/trading_platform_ops.py morning
python -B tools/trading_platform_ops.py readiness-recheck
```

The second morning invocation must preserve the first invocation's service PIDs
when they are healthy and must not increase singleton counts. Verify the Hermes
job with `hermes cron list`.

## Limitations

The supervisor does not terminate duplicates, automate broker authentication, refresh Radar,
run a full scanner, or send external notifications. A dashboard-down/worker-up
partial stack is intentionally reported for manual repair because the existing
aggregate dashboard launcher starts worker children. Live runtime acceptance
also depends on the current machine's TWS/IB Gateway authentication state.
