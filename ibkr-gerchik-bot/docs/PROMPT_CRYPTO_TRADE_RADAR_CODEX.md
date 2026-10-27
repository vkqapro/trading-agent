# Codex Prompt: Implement Crypto Trade Radar

You are working in:

```text
C:\Users\Vitaly\Documents\GitHub\trading-agent\ibkr-gerchik-bot
```

Current branch:

```text
feature-crypto-scanner
```

Implement a new **Crypto Trade Radar** for the existing application. The application already has a working stock **Trend Radar**. The crypto version must reuse its proven architecture where appropriate, but it must not copy stock-market assumptions blindly.

The result must be a working, tested feature, not a stub or design document.

---

## 1. Start with repository reconnaissance

Before changing code, read:

```text
docs/TREND_RADAR_V1.md
src/radar/models.py
src/radar/engine.py
src/radar/service.py
src/storage/trend_radar_store.py
src/brokers/ibkr.py
src/crypto/config.py
src/crypto/symbols.py
src/crypto/okx_client.py
src/crypto/bar_store.py
src/crypto/analysis.py
dashboard_react/server.py
```

Also read `AGENTS.md` and the existing Trend Radar and crypto tests.

Use `graphify query` first when investigating the codebase because this repository has a graphify index.

Inspect the current API, dashboard navigation, persistence schema, configuration conventions, and test conventions before designing the implementation.

Do not overwrite or weaken existing stock Trend Radar behavior.

---

## 2. Product goal

Create a read-only discovery and ranking layer called **Crypto Trade Radar** that:

- discovers crypto instruments actually available through the connected IBKR account;
- ranks the strongest crypto trend and breakout candidates;
- uses confirmed OHLCV candles only;
- supports multi-timeframe analysis;
- shows the result in the existing dashboard;
- allows selected symbols to be passed to the existing crypto scanner/analyzer;
- never creates, queues, approves, previews, or submits an order.

Crypto Trade Radar is not an execution system, trading recommendation engine, or autonomous agent.

Every batch response must explicitly contain:

```json
{
  "orders_submitted": 0,
  "orders_created": 0,
  "execution_requested": false
}
```

---

## 3. IBKR is the authority for tradability

The radar universe must be based on IBKR contract discovery, not on a hard-coded exchange list and not on the OKX list alone.

The current connected IBKR Paper account returned these crypto contracts during read-only contract discovery:

```text
AAVE.USD
BCH.USD
BTC.USD
CC.USD
DOGE.USD
ETH.USD
LINK.USD
LTC.USD
MATIC.USD
NEAR.USD
PAXG.USD
SHIB.USD
SOL.USD
UNI.USD
USDT.USD
XPL.USD
XRP.USD
```

These are the initial observed contracts, not a permanent hard-coded whitelist.

IBKR generally advertises additional crypto assets such as Aptos, Avalanche, Cardano, Lido DAO, Monad, and Sui, but they were not returned by the current account-connected TWS contract search. The implementation must distinguish:

- `SUPPORTED_BY_IBKR_PUBLIC_CATALOG`
- `RETURNED_BY_CONNECTED_ACCOUNT`
- `QUALIFIED_FOR_ACCOUNT`
- `UNAVAILABLE`

Do not add an instrument to the executable crypto universe only because it appears on a public IBKR marketing page.

Use IBKR contract discovery and qualification to obtain and persist:

- `conId`;
- `symbol`;
- `currency`;
- `secType=CRYPTO`;
- provider/exchange, such as `PAXOS`, `ZEROHASH`, or `ZEROHASHE`;
- `validExchanges`;
- `minTick`;
- trading hours;
- account qualification status;
- discovery timestamp;
- source and diagnostics.

If the current MCP layer does not expose contract discovery, do not invent an MCP tool. Add a narrow read-only adapter around the existing IBKR/ib_insync connection or extend the existing project MCP with a read-only contract-discovery tool. Keep the operation separate from all order tools.

The contract discovery tool must never call:

- `placeOrder`;
- order preview for execution;
- approval creation;
- Telegram approval;
- order queue;
- private crypto exchange order endpoints.

Deduplicate by `conId` and by logical pair identity. Keep provider-specific contract records separate when the same pair exists through multiple providers.

---

## 4. Data source and candle rules

For a radar intended to trade through IBKR, prefer IBKR historical market data for the qualified IBKR contract so that the radar signal and tradable instrument refer to the same venue and instrument.

The existing OKX crypto implementation may remain available for the separate crypto workflow, but do not silently mix OKX prices, volumes, or symbols into IBKR Radar results.

If IBKR historical crypto data is unavailable for a qualified contract:

- mark the row `DATA_UNAVAILABLE` or `INSUFFICIENT_DATA`;
- include the exact diagnostic reason;
- do not silently fall back to another venue;
- do not produce a tradable-looking signal from unrelated data.

Crypto runs continuously, but candle handling must still be deterministic:

- use UTC timestamps;
- use a clearly documented UTC daily boundary;
- exclude the current incomplete candle by default;
- use only confirmed/closed candles for a persisted Radar snapshot;
- never calculate a fresh breakout or trend flip from an unfinished daily candle;
- preserve timestamp, confirmation state, and data source in provenance.

Add tests for confirmed and incomplete candles.

---

## 5. Multi-timeframe analysis

Use these timeframes:

```text
1D  = primary market regime
4H  = trend confirmation
1H  = momentum and continuation context
5m  = optional trigger context only; never part of the stable daily ranking
```

The primary Radar snapshot must be stable and not change every few minutes because of 5-minute noise.

### Daily regime factors

Calculate from pure deterministic functions:

- close versus EMA20;
- EMA20 versus EMA50;
- EMA50 versus EMA200 when enough history exists;
- EMA20 and EMA50 slope;
- ADX and directional movement;
- recent consistency above/below EMA20;
- ATR and ATR percentage;
- compression relative to the instrument's own historical range;
- breakout beyond the prior range.

### 4H confirmation factors

Calculate:

- price versus EMA20/EMA50;
- slope;
- momentum;
- direction relative to the daily regime;
- alignment or conflict with the daily regime.

### 1H context

Use only as a secondary factor:

- normalized return;
- range position;
- volume expansion;
- volatility expansion;
- overextension detection.

Do not implement a separate trading strategy inside the Radar.

---

## 6. Scoring

Use configurable weights. Start with:

```text
Trend Score:
  35% daily price/EMA structure
  25% daily slope and ADX/DI
  20% 4H confirmation
  10% daily consistency
  10% 1H momentum
```

```text
Breakout Score:
  25% compression
  25% displacement relative to ATR
  20% quote-volume expansion
  20% multi-timeframe alignment
  10% volatility expansion
```

```text
Radar Score:
  45% trend/regime score
  25% breakout score
  15% momentum score
  15% liquidity quality
  + bounded fresh-flip bonus
  + bounded breakout bonus
```

Keep scores bounded to 0-100.

The score is a deterministic prioritization metric. It is not a probability of profit, expected return, or trade recommendation.

Trend states:

```text
GREEN = bullish regime
RED   = bearish regime
GREY  = neutral, transition, or conflicting timeframes
```

Add machine-readable reasons and diagnostics. Do not fabricate reasons for missing data.

---

## 7. Liquidity and volume

Do not use stock thresholds such as `$20,000,000 average dollar volume`.

Crypto liquidity must be instrument-specific and configurable.

Use the correct quote-volume field from the data source. Do not assume that raw crypto volume is already USD volume:

- for spot instruments, use quote currency volume when available;
- for other contract types, do not treat contract count as quote volume;
- if quote volume is unavailable, mark liquidity as unavailable or lower confidence rather than inventing it;
- calculate volume ratio against the instrument's own historical baseline;
- do not compare raw BTC token volume against raw SHIB token volume.

Add configuration such as:

```text
CRYPTO_RADAR_ENABLED=true
CRYPTO_RADAR_MAX_UNIVERSE_SIZE=200
CRYPTO_RADAR_LOOKBACK_DAYS=300
CRYPTO_RADAR_MIN_HISTORY_DAYS=200
CRYPTO_RADAR_TREND_GREEN_THRESHOLD=60
CRYPTO_RADAR_TREND_RED_THRESHOLD=40
CRYPTO_RADAR_COMPRESSION_THRESHOLD=45
CRYPTO_RADAR_RANGE_WINDOW=20
CRYPTO_RADAR_SLOPE_WINDOW=20
CRYPTO_RADAR_PRIMARY_TIMEFRAME=1D
CRYPTO_RADAR_CONFIRMATION_TIMEFRAME=4H
CRYPTO_RADAR_MIN_QUOTE_VOLUME_USD=...
```

Do not filter crypto by absolute price. BTC, ETH, and SHIB prices are not comparable for liquidity filtering.

---

## 8. Data model and persistence

Do not identify crypto snapshots by symbol alone. `BTC` from different providers must remain distinguishable.

Use a natural identity including at least:

```text
asset_class
conId
symbol
currency
provider/exchange
as_of
primary_timeframe
```

Persist:

- contract identity and provenance;
- latest confirmed candle timestamp;
- trend state and previous trend;
- fresh trend flip metadata;
- daily score;
- 4H confirmation score;
- alignment/conflict;
- breakout state and direction;
- compression score;
- momentum score;
- ATR and ATR%;
- quote volume and volume ratio when available;
- Radar Score;
- reasons;
- diagnostics;
- scanner status;
- created/updated timestamps.

Use the existing SQLite persistence pattern where possible. Add a safe migration if required.

Refresh must be idempotent. Repeating a refresh for the same confirmed candle must not create duplicate snapshots.

Do not destructively modify existing stock Radar data.

---

## 9. API

Add crypto-specific endpoints without changing stock endpoint semantics:

```text
GET  /api/crypto/radar
GET  /api/crypto/radar/top?limit=25
GET  /api/crypto/radar/breakouts
GET  /api/crypto/radar/flips
GET  /api/crypto/radar/{symbol}
POST /api/crypto/radar/refresh
POST /api/crypto/radar/{symbol}/scan
POST /api/crypto/radar/batch-scan
```

Support filters:

- `trend`;
- `min_radar_score`;
- `min_trend_score`;
- `breakout`;
- `breakout_direction`;
- `trend_flip`;
- `symbol`;
- `conId`;
- `provider`;
- `timeframe_alignment`;
- `status`;
- `limit`.

Use explicit statuses:

```text
NOT_SCANNED
SCANNING
NO_SETUP
CANDIDATE
ERROR
INSUFFICIENT_DATA
DATA_UNAVAILABLE
STALE_DATA
UNCONFIRMED_BAR
UNAVAILABLE_FOR_ACCOUNT
```

A failed symbol must not abort the full batch. Return a result for every requested symbol.

---

## 10. Scanner integration

Radar may hand selected instruments to the existing crypto scanner/analyzer, but it must not pass crypto symbols through the stock scanner.

Use the existing crypto path only after verifying its actual API and semantics:

- `/api/crypto/market-screener`;
- `run_crypto_analysis`;
- existing crypto strategy endpoint, if appropriate.

Persist scanner provenance:

- `radar_snapshot_id`;
- `conId`;
- `symbol`;
- `radar_as_of`;
- `scan_timestamp`;
- `source_scan_id`.

If scanner direction conflicts with the Radar regime, expose:

```text
direction_conflict=true
```

as metadata. Do not silently block, rewrite, approve, or execute the scanner result.

---

## 11. Dashboard

Add a separate navigation item:

```text
Crypto Trade Radar
```

Do not rename or break the stock `Trend Radar` page.

The page must show:

- ranked table;
- pair and IBKR contract identity;
- provider/exchange;
- conId;
- GREEN/GREY/RED state;
- Radar Score;
- daily score;
- 4H confirmation;
- alignment/conflict;
- breakout;
- momentum;
- ATR%;
- quote-volume quality;
- fresh flip;
- last confirmed candle;
- data age/freshness;
- scanner status;
- diagnostics for unavailable data or permissions.

Filters:

- symbol search;
- trend;
- provider;
- breakout;
- fresh flip;
- alignment;
- minimum score;
- account-qualified only.

Actions:

```text
REFRESH RADAR
SCAN SELECTED
SCAN TOP 10
SCAN BREAKOUTS
SCAN FRESH FLIPS
```

The UI must clearly label the feature as analysis/discovery only, not execution.

---

## 12. Safety requirements

The crypto Radar must remain read-only.

It must not:

- place an IBKR order;
- call `placeOrder` directly or indirectly;
- create an approval;
- enqueue an order;
- call Telegram approval flows;
- change Paper/Live state;
- use OKX private order endpoints;
- use an LLM to calculate scores;
- use unconfirmed candles for final signals;
- treat a public catalog listing as account qualification;
- silently substitute data from another exchange.

Preserve all existing IBKR, Paper/Live, risk, approval, and execution safeguards.

---

## 13. Tests

Add focused tests covering at least:

1. IBKR crypto contract discovery returns only `secType=CRYPTO`.
2. Contract identity preserves `conId` and provider.
3. Duplicate provider records are not collapsed incorrectly.
4. Logical pair deduplication works.
5. Account-unqualified instruments receive the correct status.
6. Incomplete candles are excluded.
7. Confirmed UTC daily candles are used.
8. Current-day crypto candle does not create a false breakout.
9. Insufficient history returns `INSUFFICIENT_DATA`.
10. Daily GREEN plus 4H GREEN produces positive alignment.
11. Daily GREEN plus 4H RED produces a conflict.
12. Fresh trend flips are persisted correctly.
13. Breakout requires compression and displacement conditions.
14. Quote volume is used correctly.
15. Contract count is not treated as USD liquidity.
16. Refresh is idempotent.
17. One failed symbol does not abort a batch.
18. Crypto symbols never enter the stock scanner path.
19. Scanner provenance includes `conId` and `radar_snapshot_id`.
20. All batch responses report zero orders submitted/created.
21. No order API is called by Radar.
22. Existing stock Trend Radar tests continue to pass unchanged.
23. Existing crypto tests continue to pass.
24. API filters and statuses behave correctly.

Use realistic IBKR contract and historical-bar fixtures. Do not make tests pass by mocking away the core contract or timestamp behavior.

---

## 14. Documentation

Create:

```text
docs/CRYPTO_TRADE_RADAR_V1.md
```

Document:

- architecture;
- IBKR contract discovery;
- account qualification versus public support;
- provider/conId identity;
- UTC and confirmed-candle rules;
- multi-timeframe calculations;
- score formulas;
- liquidity rules;
- configuration;
- API;
- UI;
- persistence;
- scanner integration;
- safety boundaries;
- known limitations.

Mention that the initial account-connected contract set observed for the Paper account was:

```text
AAVE.USD, BCH.USD, BTC.USD, CC.USD, DOGE.USD, ETH.USD,
LINK.USD, LTC.USD, MATIC.USD, NEAR.USD, PAXG.USD, SHIB.USD,
SOL.USD, UNI.USD, USDT.USD, XPL.USD, XRP.USD
```

This list must be treated as a discovered snapshot, not as a hard-coded permanent truth.

---

## 15. Verification and delivery

After implementation:

1. Run targeted crypto Radar tests.
2. Run all existing stock Trend Radar tests.
3. Run existing crypto tests.
4. Run the relevant full test suite.
5. Run lint/type checks if configured.
6. Check the API with fixture/local data.
7. Verify no order API was called.
8. Verify all order counters remain zero.
9. Inspect `git diff` for accidental changes and secrets.
10. Do not modify `.env`.
11. Do not commit or push unless explicitly asked.
12. Do not claim completion until the implementation has actually been exercised.

Final response must include:

- changed files;
- tests actually run and their results;
- API/UI verification results;
- discovered limitations;
- assumptions;
- manual dashboard verification steps.

If a requirement is blocked by missing IBKR permissions, missing historical data, or an unavailable MCP operation, report the exact blocker instead of inventing a fallback.
