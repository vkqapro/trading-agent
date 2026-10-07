# Trading Bot Data MCP v0.1

## Purpose and architecture

`trading_bot_data` is a standalone, read-only Model Context Protocol server. It
reads the Trading Bot's persisted candle store (`memory/bars/*.csv`) and the
same persisted watchlist snapshots used by the dashboard (`memory/RESEARCH_LOG.md`
and `runtime/state.json`). It does not connect to IBKR, start jobs, acquire
session locks, or calculate a second set of levels.

The default Streamable HTTP endpoint is `http://127.0.0.1:8765/mcp`; `GET
/health` returns a small read-only health response. Configure with
`TRADING_BOT_MCP_ENABLED`, `TRADING_BOT_MCP_HOST`, `TRADING_BOT_MCP_PORT`,
`TRADING_BOT_MCP_MAX_CANDLES`, and `TRADING_BOT_MCP_MAX_LEVELS`. The default bind is localhost only.

Run independently with `python -m src.mcp.trading_bot_server`, or use
`run_trading_bot_mcp.cmd` and `stop_trading_bot_mcp.cmd` on Windows. These
scripts track only the MCP PID and do not affect the dashboard, scheduler,
TWS, or Trading Bot backend.

## Tools

- `get_symbol_snapshot(symbol)` returns persisted price, security type, daily
  and technical ATR from the latest watchlist plan, candle coverage, level
  counts, and freshness.
- `get_symbol_data_coverage(symbol, timeframe?)` reports earliest/latest and
  latest closed stored bars. With no timeframe it lists available stored
  timeframes.
- `get_symbol_history(symbol, timeframe="1D", lookback_days?, limit?,
  start?, end?, include_incomplete=false)` returns structured OHLCV. Daily
  rows dated today are treated as incomplete and excluded by default; any
  returned incomplete row is marked `closed: false`.
- `get_symbol_levels(symbol, level_set="consolidated", min_strength?,
  level_type?, side?, limit?)` filters existing `levels` (consolidated) or
  `raw_levels` (raw) from the persisted plan. It never recalculates them.
- `get_level_context(symbol, level_price)` matches an existing level within a
  documented deterministic tolerance of `0.005` price units and returns all
  stored metadata.

Level fields are preserved as available. Consolidated levels normally include
`price`, `zone_low`, `zone_high`, `type`, `touches`, `false_breakouts`, and
`strength`; raw levels are not padded with fabricated fields. Responses use a
stable `{error: {code, message}}` shape for `SYMBOL_NOT_FOUND`,
`NO_CANDLE_DATA`, `NO_LEVEL_DATA`, `INVALID_TIMEFRAME`, `INVALID_LEVEL_SET`,
`LEVEL_NOT_FOUND`, `REQUEST_LIMIT_EXCEEDED`, and `DATA_SOURCE_UNAVAILABLE`.

## Price and freshness semantics

The MCP keeps three concepts separate:

- `current_price` is the latest persisted close from the current-price source
  ordering: `intraday_15m`, then `intraday_5m`, then `daily`. Its timestamp is
  `current_price_timestamp`; `current_price_age_seconds` is calculated from
  that persisted bar timestamp. `last_price` remains only as a compatibility
  alias for this same value.
- `latest_closed_price` and `latest_closed_bar_timestamp` come from the most
  recent closed daily candle. They are never substituted with the current
  snapshot price.
- `level_reference_price` comes from the persisted Premarket
  `level_spacing.current_price`, the price associated with the persisted level
  set when that spacing context was built. The current backend does not store a
  reliable per-symbol level-generation timestamp, so
  `level_reference_price_timestamp`, `levels_as_of`, and `levels_age_seconds`
  are explicitly `null` rather than inferred from file mtime.

`get_symbol_levels` and `get_level_context` use `current_price` for
`distance_from_current_price` and `side=above_price` / `side=below_price`.
`distance_from_level_reference_price` is exposed only in level context for
provenance. Persisted level values themselves are not recalculated or changed.
Future charts must use the current-price marker from `current_price` while
retaining level provenance metadata.

## Chart rendering (v0.2)

`render_symbol_chart` returns mixed MCP content: JSON provenance metadata and
an in-memory PNG image. It renders closed OHLC candles from
`get_symbol_history` and, when requested, overlays existing zones from
`get_symbol_levels`. It does not read persistence independently, recalculate
levels, fetch data, or create signals.

Arguments include `symbol`, `timeframe`, `lookback_days`, `start`, `end`,
`show_levels`, `level_set`, `min_strength`, `level_type`, `side`,
`level_labels` (`none`, `compact`, or `full`), `show_current_price`,
`include_volume`, `width`, and `height`. Dimensions are bounded to 800-2000 by
500-1200 pixels and candle limits remain governed by
`TRADING_BOT_MCP_MAX_CANDLES`.

The current-price marker uses `current_price`, never the latest closed price or
level reference price. Zones are drawn only when they intersect or are near the
visible candle range; metadata reports `levels_total`, `levels_rendered`, and
`levels_outside_chart`. Labels are thinned when their vertical positions would
collide. No temporary chart files are created.

Example Harness prompts:

```text
Show AAPL for the last 60 closed daily candles.
Now add the consolidated levels.
Show only consolidated levels with strength >= 30.
```

## Safety boundary

There are no order, position, broker, scanner, candle-fetch, strategy-job, or
session-lock tools. The service is read-only and idempotent. It does not depend
on React Dashboard being open and stopping it cannot stop the Trading Bot.
Localhost-only binding is the v0.1 security boundary; bearer authentication is
left for a future remote deployment. DeepSeek Harness configuration is
intentionally not changed in v0.1.

## Future work

Future chart enhancements may add more persisted timeframes and richer zone
styling. Future Harness integration should point its MCP client at `/mcp` only
after independent protocol and data-parity verification. Remote deployment
requires authentication and an explicit network/security review.
