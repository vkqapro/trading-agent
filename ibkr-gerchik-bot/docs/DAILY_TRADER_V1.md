# Daily Trader V1

Daily Trader is a read-only Hermes market-monitoring profile layered on the
existing Trend Radar and production scanner. It is an agent-facing API and
reporting contract, not a new scanner, strategy, approval flow, or execution
worker.

## Architecture

```text
Hermes scheduler/profile
        |
        v
loopback API client -> POST /api/daily-trader/scan
        |
        v
persisted latest closed-day Trend Radar rows
        |
        v
RadarService.scan_symbols()
        |
        v
existing dashboard _run_authoritative_radar_scanner()
        |
        v
dashboard_react.market_screener.run_market_screener()
        |
        v
structured read-only response -> Hermes report
```

Radar is read from its existing persisted snapshot store. The Daily Trader
layer does not recalculate Radar, duplicate LP1/LP2/PRB1/PRB2 logic, load a
broker, or call an order endpoint. Scanner lifecycle and current 5-minute /
partial-daily chronology remain owned by the existing market screener.

## API

The routes are served by the existing FastAPI dashboard on its configured
loopback listener (normally `http://127.0.0.1:8550`).

### `POST /api/daily-trader/scan`

Request body:

```json
{
  "mode": "scheduled",
  "top_n": 10,
  "include_breakouts": true,
  "include_fresh_flips": true,
  "max_symbols": 30
}
```

`mode` is one of `scheduled`, `top`, `breakouts`, or `fresh_flips`. Values are
bounded by the API (`top_n` 0–100 and `max_symbols` 1–100). Scheduled mode
selects Top Radar rows, then current Radar `breakout == true` rows, then
current Radar `trend_flip == true` rows, normalizes and deduplicates symbols,
and applies `max_symbols`. No new breakout, flip, or ranking definition is
created.

The response contains:

- `scan_id`, `scan_timestamp`, `market_status`, `mode`, and `radar_as_of`;
- `selection` counts for Top, breakouts, fresh flips, unique symbols, and the
  cap;
- `scanner.requested`, `scanner.completed`, and `scanner.failed`;
- `summary` counts for active, entry-triggered, pending-entry, terminal,
  no-setup, ambiguous, and error results;
- `active`, `terminal`, `no_setup`, and `errors` arrays;
- the mandatory safety fields `read_only: true` and `orders_submitted: 0`.

Each active or terminal setup contains Radar context plus scanner-owned values:
strategy, side, lifecycle status/reason, entry state, level, planned entry,
entry, stop, target, R:R, score, pattern quality, position size,
`direction_conflict`, and data-quality fields. These values are copied from
authoritative payloads; Daily Trader does not recompute them.

Active lifecycle statuses are exactly `PENDING_ENTRY` and
`ENTRY_TRIGGERED`. `INVALIDATED`, `EXPIRED`, and
`AMBIGUOUS_INTRABAR_ORDER` are terminal/non-actionable and never appear under
`active`. A selected symbol with no scanner signal appears under `no_setup`.
One symbol failure becomes an `errors` entry and does not cancel other symbols.

### `GET /api/daily-trader/symbol/{symbol}`

Runs the existing authoritative scanner for one normalized stock symbol and
returns Radar context, current scanner status, the same active/terminal/no-setup
classification, and the same read-only safety fields. It is intended for
requests such as “Check AMZN”.

### `GET /api/daily-trader/health`

Returns `status`, `radar_available`, `scanner_available`,
`latest_radar_as_of`, `read_only`, and `orders_submitted`. Broker connectivity
is not required for this read-only health check. `degraded` means the service
cannot currently prove both a Radar snapshot and an installed scanner callback
are available.

## Hermes integration

The target profile is `daily-trader` with display role `Daily Trader`. Its SOUL
must state that it is a read-only market-monitoring agent: it calls only the
approved API client, never places or approves trades, never changes scanner or
strategy settings, never invents a setup, and never promotes `NO_SETUP`,
`INVALIDATED`, `EXPIRED`, or ambiguous results to active candidates.

The profile has two narrow skills:

- `daily-trader-scan` calls only the scan endpoint and summarizes active,
  terminal, no-setup, error, market-status, and safety fields;
- `daily-trader-symbol` calls only the symbol endpoint and summarizes Radar plus
  current scanner/lifecycle state;
The skills invoke `tools/daily_trader_api.py`, a loopback-only standard-library
HTTP client. It does not import trading source files or shell out to a scanner.
It fails closed if the API is unavailable, the response is not JSON, or the
read-only safety assertion is absent.

## Schedule

The supported Hermes cron job is:

```text
Name: Daily Trader Market Scan
Profile: daily-trader
Schedule: 35 9-15 * * 1-5
Timezone: America/New_York (Hermes host/profile timezone)
Delivery: Hermes local output only
```

The schedule is intentionally hourly at 09:35 through 15:35 ET, after usable
regular-session 5-minute data begins. No Telegram, Slack, Discord, email, or
webhook delivery is configured.

## Manual verification

With the existing dashboard running on its loopback port:

```powershell
python tools/daily_trader_api.py health
python tools/daily_trader_api.py scan --mode scheduled --top-n 10 --max-symbols 30
python tools/daily_trader_api.py symbol AMZN
```

Expected safety evidence in every successful response is:

```json
{"read_only": true, "orders_submitted": 0}
```

When the dashboard is unavailable, the client reports exactly:
`Daily Trader API unavailable. No market report generated.` It does not fall
back to model-generated market analysis.

## Scope and limitations

- V1 reads the latest persisted Radar snapshot; it does not refresh Radar on
  every hourly scan or treat an unfinished daily candle as a closed Radar day.
- V1 reports current scanner output for the bounded Radar-selected universe;
  it is not a full-market scan.
- Hermes delivery is local/Hermes-only. External notification deduplication is
  intentionally deferred.
- Browser-level Hermes UAT and a live scheduled fire require the local Hermes
  gateway/profile and dashboard processes to be running; code-level tests do
  not claim that runtime evidence.
