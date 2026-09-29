# Trading Bot Data MCP v0.1 Implementation Report

## Authoritative sources

| Exposed data | Existing source | Notes |
|---|---|---|
| Candles/OHLCV | `src/data/bar_store.py:load_bars`, CSV files under `memory/bars` | MCP only reads persisted bars; no IBKR fallback |
| `current_price` | Latest persisted close from `intraday_15m`, then `intraday_5m`, then `daily` | `last_price` is only a compatibility alias; this is not a live broker quote |
| `latest_closed_price` | Close of the latest row in persisted `daily` bars whose date is before today's session date | Separate from current price |
| `level_reference_price` | Persisted Premarket `watchlist[symbol].level_spacing.current_price` | Price associated with level spacing/generation; timestamp is not persisted and is returned as null |
| Daily ATR | Persisted watchlist `daily_atr`, produced by `src/jobs/premarket.py` using `src/strategy/atr.py:calculate_daily_atr` | MCP does not recalculate |
| Technical ATR | Persisted watchlist `technical_atr`, produced by `src/jobs/premarket.py` using `calculate_technical_atr` | MCP does not recalculate |
| Raw levels | Persisted watchlist `raw_levels` in the latest dashboard watchlist snapshot | These are the existing `filter_strong_levels` output, not every intermediate detector candidate |
| Consolidated levels | Persisted watchlist `levels` | These are `optimize_trade_levels` output already shown by Trading Bot UI |
| `zone_low`, `zone_high` | Serialized `src/strategy/levels.py:Level.to_dict()` values in persisted level records | No zone reconstruction |
| `touches`, `false_breakouts`, `strength` | Serialized Level metadata from `src/strategy/levels.py` | No new touch or breakout scan |
| Freshness | Bar timestamp and bar-store index metadata | Returned as source timestamp/age where available |

The dashboard's `dashboard/data_access.load_watchlist()` merges the latest
Premarket workflow snapshot with runtime state. The MCP imports that read-only
accessor to preserve UI parity. Candle coverage is computed from the same CSV
rows used by `dashboard/data_access.get_bars`.

Current-price distance and side filtering use `current_price`; they never fall
back to `level_reference_price`. `get_level_context` additionally reports
`distance_from_level_reference_price` for provenance. The three values can
legitimately differ because the level plan is persisted from an earlier
Premarket calculation while bars continue to update. No level or candle
calculation was changed.

## Boundary verification

The MCP package imports only configuration, symbol normalization, dashboard
read-only data access, and the persisted bar store. It does not import
`OrderManager`, broker execution methods, `MarketDataService`, strategy jobs,
market scanners, or session utilities. It does not call IBKR to fill missing
data. Missing persisted data returns a stable error.

## Acceptance status

The implementation provides the five requested tools over Streamable HTTP and
separate Windows lifecycle scripts. Automated tests cover symbol normalization,
closed-candle filtering, persisted level parity, level context matching, and
stable invalid-level errors. A live AAPL parity run requires the local
repository's persisted AAPL bars/watchlist to be present; no live broker fetch
is performed.

## Price and freshness verification

For the current persisted AAPL files, the trace is:

- `current_price`: `332.97`, from the latest `memory/bars/AAPL__intraday_15m.csv` close at `2026-09-29T10:15:00-04:00`.
- `latest_closed_price`: `338.40`, from the latest completed daily row in `AAPL__daily.csv` dated `2026-09-28`.
- `level_reference_price`: `338.37`, from the persisted Premarket `level_spacing.current_price` associated with the saved levels.
- Level-generation timestamp: unavailable in the persisted watchlist schema, so timestamp and age are `null` rather than inferred.

The values may legitimately differ because intraday bars update after the daily
bar closes and the persisted level plan retains its original spacing context.
Current distance and side filters use `current_price`; level values remain
unchanged. AAPL still returns 11 consolidated levels and the 327.30 level
retains its existing metadata.

### Final safety answers

- Can MCP place an order? **No.**
- Can MCP cancel an order? **No.**
- Can MCP modify a position? **No.**
- Can MCP launch intraday? **No.**
- Can MCP fetch missing candles from IBKR? **No.**
- Can MCP acquire `market_session.lock`? **No.**
- Does MCP depend on React Dashboard being open? **No.**
- Does stopping MCP stop the trading backend? **No.**
- Does MCP calculate a second independent copy of levels? **No.**
