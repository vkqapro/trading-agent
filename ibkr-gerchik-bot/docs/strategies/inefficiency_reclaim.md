# Inefficiency Reclaim Strategy

`INEFFICIENCY_RECLAIM` is an isolated, deterministic strategy subsystem. It
detects an hourly displacement, freezes the resulting inefficiency zone, waits
for a controlled 15-minute retracement, requires a confirmation, and builds a
cost-aware stop-limit plan.

The implementation is disabled by default, paper-only, and fail-closed. Live
submission is prohibited even if another bot strategy supports it.

## Strategy Flow

1. Use completed regular-session bars only. Intraday timestamps represent bar
   close/availability time.
2. Calculate robust ATR from prior true ranges. Median/MAD filtering is used
   first, with a winsorized fallback when MAD is zero.
3. Detect bullish or bearish hourly displacement with all required filters:
   true-range multiple, body ratio, close location, directional efficiency,
   overlap, relative volume, and meaningful structure.
4. Build one immutable zone:
   - `STRICT_THREE_BAR_GAP`
   - `LOW_OVERLAP_DISPLACEMENT`
5. Wait at least one full hour and at most seven regular-session hours for a
   15-minute retracement. The preferred depth is 40%-70%; the hard range is
   25%-85%.
6. Reject a deep retrace, excessive pullback volume, two adverse 15-minute
   closes, or an adverse hourly close.
7. Confirm in deterministic priority order:
   - `SWEEP_AND_RECLAIM`
   - `MICRO_BREAK_OF_STRUCTURE`
   - `TWO_BAR_RECLAIM`
8. Build a stop-limit entry, technical stop, structural target, cost-adjusted
   R multiple, and risk-capped quantity.
9. Arm only when every hard filter passes.

The same logic is mirrored for `IRS_LONG` and `IRS_SHORT`.

## Hard Filters

An order is not eligible when any required input is missing or unsafe,
including:

- incomplete or invalid market data;
- unknown or blocked news, earnings, corporate-action, or halt status;
- stale/missing quote or excessive spread;
- opposed or unknown market/sector context;
- insufficient target space or score;
- no connected paper account snapshot, buying power, short availability, or
  protective-stop capability;
- existing position/order, daily trade/loss limit, or max positions;
- entry outside the 09:30-15:45 America/New_York window;
- current executable quote beyond the stop-limit cap.

Historical anchor-day scans intentionally fail the session/quote/account gates.
They are analysis and audit results, never executable signals.

## State And Audit

SQLite migration `migrations/001_inefficiency_reclaim_up.sql` creates scanner,
zone, setup, transition, signal, rejection, order, fill, risk, and news audit
tables. The down migration removes only this subsystem.

The setup identity is stable for a symbol/profile/zone/direction. A later
confirmation can create a new signal for that setup without duplicating or
moving the immutable zone. State transitions are forward-only and idempotent.

Runtime database:

```text
memory/inefficiency_reclaim.db
```

## Data And Jobs

Required histories:

- daily: 10 years configured, at least 5 years required for paper readiness;
- 1 hour: 3 years configured, at least 120 completed bars required;
- 15 minutes: 2 years configured, at least 200 completed bars required;
- 5 minutes and 1 minute: optional execution-resolution histories.

Run a connected hydration/analysis job:

```powershell
python -m src.main --job irs_scan --irs-mode PREMARKET_CONTEXT
python -m src.main --job irs_scan --irs-mode HOURLY_SETUP_SCAN
python -m src.main --job irs_scan --irs-mode FIFTEEN_MIN_CONFIRMATION_SCAN
python -m src.main --job irs_scan --irs-mode EOD_REPORT
```

Use `--no-hydrate` to scan saved bars without requesting IBKR history. The
scheduler command uses client ID `71`.

Recommended cadence:

- premarket context once before 09:30;
- hourly setup scan just after each hourly bar closes;
- confirmation scan just after each 15-minute bar closes;
- EOD report after 16:00.

## Dashboard

The **Inefficiency Reclaim** tab is manually scanned. It does not continuously
poll or execute orders. It provides:

- anchor-day control;
- feature/paper/readiness metrics;
- setup and rejection tables;
- 15-minute chart with immutable zone, displacement/retrace/confirmation
  highlights, entry, stop, and target;
- score breakdown, data diagnostics, and deterministic explanation.

## Configuration

The `.env.template` contains every environment-facing option. Safety defaults:

```env
INEFFICIENCY_RECLAIM_ENABLED=false
IRS_TRADING_MODE=paper
ALLOW_LIVE_TRADING=false
IRS_RISK_PERCENT_PER_TRADE=0.25
```

`IRS_RISK_PERCENT_PER_TRADE` is expressed in percent units, so `0.25` means
0.25% of equity. It is converted internally to `0.0025`.

To perform paper analysis, keep live trading false and set:

```env
INEFFICIENCY_RECLAIM_ENABLED=true
IRS_TRADING_MODE=paper
ALLOW_LIVE_TRADING=false
```

This enables strategy qualification only. It does not automatically submit an
order. Controlled submission uses `InefficiencyReclaimPaperExecutor` with a
fresh paper-account snapshot and all broker checks repeated immediately before
submission.

## Execution And Replay

The paper executor:

- verifies all IBKR account codes are paper accounts;
- checks local and broker-side idempotency by deterministic `orderRef`;
- refreshes quote/spread and rejects overextension;
- submits a parent stop-limit with OCA stop and target children;
- persists broker IDs and statuses;
- resizes both protective children after a partial fill;
- audits local orders, broker orders, positions, and exact child quantities
  after reconnect without repeating an entry;
- cancels expired unfilled brackets during the EOD workflow;
- raises a global-entry lock result when protection cannot be maintained.

Only newly armed setup IDs generate setup alerts. Order, partial-fill,
protection, reconnect-error, risk-data, and EOD messages explicitly state
`PAPER`. Per-scan observability counters and durations are returned by the API
and stored with the scanner-run audit.

The replay engine models stop-limit no-fills, commissions, spread, slippage,
MAE/MFE, and lower-timeframe sequencing. If a bar touches both stop and target
and 5-minute/1-minute data cannot resolve the order, it labels the trade
`AMBIGUOUS` and counts it as a loss in conservative results.

## Current Limitations

- The connected analysis job does not call the paper executor automatically.
- The repository has no authoritative earnings, corporate-action, or halt
  provider for this subsystem. Unknown status blocks entry.
- Sector regime and relative strength must be supplied by trusted watchlist
  context; unknown sector context blocks entry.
- IBKR historical coverage and pacing determine hydration completeness.
- Reconnect discrepancies lock entries and alert. The executor does not
  fabricate replacement protection because the existing broker abstraction
  cannot atomically recreate both OCA children.
- Historical performance by score is produced by the replay report but is not
  displayed until a replay report is persisted by an external research run.
- This implementation is not approved for live trading.

Run focused tests:

```powershell
python -m unittest discover -s tests -p "test_inefficiency_reclaim*.py"
```
