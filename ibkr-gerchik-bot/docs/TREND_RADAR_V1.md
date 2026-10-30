# Trend Radar V1

Trend Radar is a deterministic, read-only market-discovery layer in the
existing dashboard. It ranks interesting US equity symbols from persisted
daily OHLCV bars and can hand one or more symbols to the existing production
market screener. Radar is not a strategy, proposal, approval, risk, or order
subsystem.

## Architecture

```text
config/STOCK_SYMBOLS.csv + persisted memory/bars/*__daily.csv
                              |
                              v
                    src/radar/service.py
                              |
                              v
                    src/radar/engine.py
                              |
                              v
       existing Decision Lab SQLite DB: radar_snapshots
                              |
                              v
 dashboard_react/server.py  -->  run_market_screener (existing path)
                              |
                              v
             Radar table: NOT_SCANNED / SCANNING / NO_SETUP /
                          CANDIDATE / ERROR
```

The engine imports no broker, order manager, execution worker, or approval
module. The scanner callback in the dashboard calls the same
`run_market_screener` function and `_screener_frame` loader used by the
existing `/api/market-screener` endpoint. Radar does not reproduce LP/PRB
logic.

## Deterministic scoring

The latest persisted daily bar is treated as the observation. An incomplete
intraday session is not stitched into a Radar snapshot.

Trend Score is a weighted normalized combination:

| Factor | Weight |
| --- | ---: |
| Close relative to EMA20 | 25% |
| EMA20/EMA50/EMA200 structure | 25% |
| Normalized EMA20/EMA50 slope | 20% |
| ADX with +DI/-DI direction | 15% |
| Recent closes relative to EMA20 | 15% |

`GREEN` is at or above 60, `RED` is at or below 40, and the remainder is
`GREY`. Thresholds and history requirements are configurable through the
`RADAR_*` environment settings.

Compression compares the latest prior 20-session high-low width as a percent
of price with the instrument's own rolling historical widths. It is not a raw
dollar-range comparison. A breakout requires a close outside the prior range
and a compression score at or above the configured threshold. Breakout Score
uses compression (25%), displacement (25%), volume expansion (20%), trend
alignment (20%), and volatility expansion (10%).

Momentum uses normalized five- and twenty-session returns, recent range
position, and EMA slope. Radar Score is a bounded ranking score:

```text
35% Trend Score
35% Breakout Score
20% Momentum Score
10% Liquidity quality
+ bounded +5 fresh-flip bonus
+ bounded +5 breakout bonus
```

Radar Score is a prioritization metric, not a probability of profit or a
trade recommendation.

## Persistence and history

`TrendRadarStore` adds a `radar_snapshots` table to the existing configured
Decision Lab SQLite database (default `memory/decision_lab.db`). The natural
key is `(symbol, as_of)`, so rerunning a day is idempotent. The payload stores
trend state, scores, breakout fields, metrics, machine-readable reason codes,
and scanner status. The previous trend is read from the prior persisted
observation rather than recalculated ad hoc from yesterday's candles.

## API

```text
GET  /api/radar
GET  /api/radar/top?limit=25
GET  /api/radar/breakouts?direction=BULLISH
GET  /api/radar/flips?trend=GREEN
GET  /api/radar/{symbol}
POST /api/radar/refresh
POST /api/radar/{symbol}/scan
POST /api/radar/batch-scan
```

`GET /api/radar` supports `trend`, `min_radar_score`, `min_trend_score`,
`breakout`, `breakout_direction`, `trend_flip`, `symbol`, and `limit`.

The batch body is `{ "symbols": ["GSHD", "NVDA"], "max_workers": 3 }`.
The server caps the batch at 100 symbols and concurrency at five workers. It
returns a result for every requested symbol, including `ERROR` entries, and
reports `orders_submitted: 0` as an explicit safety assertion.

## UI

The existing single-file React dashboard has a new `Trend Radar` navigation
item. The page supports:

- ranked Radar table with GREEN/GREY/RED, breakout, momentum, volume, and scanner columns;
- search, trend, breakout, and fresh-flip filters;
- per-row checkboxes with `SCAN SELECTED`;
- `SCAN TOP 10`, `SCAN BREAKOUTS`, and `SCAN FRESH FLIPS`;
- controlled batch progress and per-symbol `SCANNING`, `NO_SETUP`, `CANDIDATE`, or `ERROR` status;
- asset detail panel with reasons, persisted history, and the existing single-symbol scanner result.

Candidate rows display the first authoritative strategy/side label, such as
`PRB1 LONG`. No result is promoted to approval or execution by Radar.

## Safety contract

- Radar calculations use daily data and never call a broker.
- Radar batch scanning calls only the existing deterministic scanner path.
- Batch failures are isolated per symbol; one failure does not cancel the rest.
- Scanner responses are returned as analysis only; no order is submitted or queued.
- Existing proposal, approval, risk, Paper/Live, and broker safeguards remain outside Radar.
- No LLM, news, sentiment, crypto, options, MCP, or autonomous Radar execution is included in V1.

## Configuration

The defaults are intentionally conservative and can be overridden without
hard-coding a ticker list:

```text
RADAR_ENABLED=true
RADAR_MIN_PRICE=5
RADAR_MIN_AVG_DOLLAR_VOLUME=20000000
RADAR_LOOKBACK_DAYS=250
RADAR_MIN_HISTORY_DAYS=120
RADAR_MAX_UNIVERSE_SIZE=1000
RADAR_TREND_GREEN_THRESHOLD=60
RADAR_TREND_RED_THRESHOLD=40
RADAR_COMPRESSION_THRESHOLD=45
RADAR_RANGE_WINDOW=20
RADAR_SLOPE_WINDOW=20
RADAR_DATABASE_PATH=memory/decision_lab.db
```

The initial universe is the configured stock-symbol file plus symbols already
present in the existing bar index, deduplicated and sorted. This keeps V1
deterministic and avoids inventing a second universe provider.
