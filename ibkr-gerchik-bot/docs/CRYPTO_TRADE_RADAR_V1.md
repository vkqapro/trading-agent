# Crypto Trade Radar V1

Crypto Trade Radar is a read-only, deterministic discovery layer for the same live SPOT instruments used by the OKX Demo Crypto Screener and future OKX Demo execution. It is separate from the stock Trend Radar and never calls order, approval, queue, or execution endpoints.

## Architecture

- `src/crypto_radar/models.py`: account-instrument and snapshot models; OKX identity is the exact `instId`.
- `src/crypto_radar/engine.py`: pure confirmed-candle calculations.
- `src/crypto_radar/store.py`: idempotent SQLite snapshots keyed by instrument identity and UTC `as_of`.
- `src/crypto_radar/service.py`: account universe, refresh, filters, and read-only scanner handoff.
- `src/crypto/okx_client.py`: account instrument discovery and OKX public candles.

## Instrument authority

The authenticated OKX Demo account is authoritative. Discovery reads `/api/v5/account/instruments?instType=SPOT`, accepts only `state=live`, preserves the exact `instId`, and keeps sizing metadata (`baseCcy`, `quoteCcy`, `lotSz`, `minSz`, `tickSz`). The configured 26-symbol file is only a local mirror and is not used as a fallback when the account endpoint is unavailable.

## Candle and scoring rules

Candles come from OKX `/api/v5/market/candles` for the same `instId` used by execution. Daily candles use UTC-anchored `1Dutc`; 4H and 1H use OKX public candles. The parser preserves quote volume and the OKX confirmation flag. Only confirmed, non-future candles are scored. The current incomplete candle returns `UNCONFIRMED_BAR`; insufficient history returns `INSUFFICIENT_DATA`. Daily regime uses EMA20/EMA50, slope, consistency, ATR, and prior-range breakout. 4H confirms regime; 1H supplies secondary momentum. Scores are bounded 0-100 and are ranking metrics, not trade recommendations. Quote volume is used when supplied; instrument count is never treated as liquidity.

## API

- `GET /api/crypto/radar`
- `GET /api/crypto/radar/top?limit=25`
- `GET /api/crypto/radar/breakouts`
- `GET /api/crypto/radar/flips`
- `GET /api/crypto/radar/{symbol}`
- `POST /api/crypto/radar/refresh`
- `POST /api/crypto/radar/{symbol}/scan`
- `POST /api/crypto/radar/batch-scan`

Responses expose `read_only=true`, `orders_submitted=0`, `orders_created=0`, and `execution_requested=false`.

## Runtime diagnostics

Account instrument discovery and market-data failures are surfaced as per-refresh diagnostics. Daily history is fetched first; 4H/1H requests are skipped when daily history is missing or below the minimum. The engine rejects bars dated in the future relative to the current UTC clock, and the persisted listing returns one latest non-future observation per OKX `instId`.

The dashboard page is **Crypto Trade Radar** and now displays OKX Demo account instruments, OKX provider, exact `instId`, and OKX candles. IBKR Crypto contracts and IBKR historical data are no longer used by this radar. The feature remains read-only and does not fall back to IBKR, Binance, or another provider.

