# Crypto Screener: All Universe and Radar Setups

## Behavior

`Screener Crypto` now supports the same universe selector as `Screener Stocks`, but the crypto universe is account-bound:

- **ALL UNIVERSE** scans the live SPOT instruments returned by the authenticated OKX Demo account endpoint `/api/v5/account/instruments`.
- **RADAR SETUPS** scans only symbols with a signal from the latest fresh crypto `ALL UNIVERSE` scan, intersected with that same account-specific instrument list.
- `config/crypto_symbols.txt` is only a local mirror of the last verified list; it is not authoritative.

The crypto Radar Setups universe is persisted in `crypto_screener_snapshots` inside the configured decision database. It is derived from LP1/LP2/PRB1/PRB2 signal rows, not from the separate IBKR Crypto Trade Radar trend score. This prevents a trend candidate from being presented as a strategy setup before the strategy scanner has actually evaluated it.

A Radar Setups scan never falls back to the full universe. If there is no completed scan, the snapshot is stale, or the latest scan has no signals, the API returns HTTP 409 with a diagnostic reason.

## Data-source boundary

The canonical crypto market-data source for this universe is **OKX public market data** for the same `instId` values returned by the authenticated OKX Demo account. Candle collection uses OKX `/api/v5/market/candles`; the screener consumes those stored candles after confirmed UTC-bar and incomplete/future-bar filtering. Ticker/price and volume are OKX fields for the same instrument identity. Account-specific metadata (`state`, `baseCcy`, `quoteCcy`, `lotSz`, `minSz`, `tickSz`) is read from `/api/v5/account/instruments` and is used for eligibility and future order validation.

The Crypto Trade Radar and Crypto Screener now share the same OKX Demo account universe and OKX public candles. They remain separate analytical layers: Radar is discovery/data-health, while Screener LP/PRB results are authoritative for setup status. No IBKR crypto contracts or IBKR historical candles are used by either layer.

## Safety

The feature is read-only. Saving a screener snapshot does not create an approval, execution intent, order, or broker action. Every API response keeps `orders_submitted=0`.
