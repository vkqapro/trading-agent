# DeepSeek Harness ↔ Trading Bot Data MCP

## Current integration

DeepSeek Harness Web uses the installed profile composition at
`C:\Users\Vitaly\.dsh\profiles\web\cordis.patch.yml`. A dedicated preset was
added there:

- preset id: `trading-analyst`
- display name: `Trading Analyst`
- MCP server name: `trading_bot_data`
- transport: `streamable-http`
- URL: `http://127.0.0.1:8765/mcp`
- timeout: `60000` ms
- startup failure: enabled
- reconnect: enabled, five attempts

The profile contains only a short read-only analyst persona and the official
`@deepseek-ai/dsh-mcp-client` plugin. Existing Champion, Professor, and other
profiles were not changed. The source bundle used for review is
`harness/trading-analyst/` in this repository.

## Startup order

1. Run `run_trading_bot_mcp.cmd` from the Trading Bot repository.
2. Verify `http://127.0.0.1:8765/health` reports `status: ok`.
3. Start/open DeepSeek Harness Web and use the authenticated URL printed by
   `dsh web`.
4. Select the `Trading Analyst` profile and start a new chat.
5. Confirm the MCP panel/profile diagnostics show `trading_bot_data`.

The MCP server remains independent of the Trading Bot scheduler, dashboard,
IBKR/TWS, and Harness lifecycle. Stop it with `stop_trading_bot_mcp.cmd`.

## Expected model-visible tools

The official MCP client should expose:

- `mcp__trading_bot_data__get_symbol_snapshot`
- `mcp__trading_bot_data__get_symbol_data_coverage`
- `mcp__trading_bot_data__get_symbol_history`
- `mcp__trading_bot_data__get_symbol_levels`
- `mcp__trading_bot_data__get_level_context`

The raw MCP server discovery was independently verified to contain the five
underlying names: `get_symbol_snapshot`, `get_symbol_data_coverage`,
`get_symbol_history`, `get_symbol_levels`, and `get_level_context`.

## Validation prompts

Use a new `Trading Analyst` chat and ask:

```text
Use the Trading Bot Data MCP only.
Check what daily historical data is available for AAPL.
Tell me the earliest available candle, latest available candle,
latest closed candle, and available daily bar count.
Do not use web search.
```

```text
Using Trading Bot Data MCP, retrieve the last 60 days of closed daily candles for AAPL.
Report the returned date range, candle count, latest close, highest high, and lowest low.
Use only MCP data.
```

```text
Get the current consolidated Trading Bot levels for AAPL.
For each level show price, zone low, zone high, type, touches,
false breakouts, and strength. Sort by strength descending.
Use only Trading Bot MCP data.
```

```text
Using only Trading Bot Data MCP, retrieve the last 60 closed daily candles for AAPL
and consolidated levels. Identify the nearest important levels above and below the
latest price using distance, strength, touches, false breakouts, and type.
```

Then pass one real returned level price to `get_level_context`.

## Troubleshooting

- MCP health failure: start the Trading Bot MCP before Harness.
- Plugin startup failure: inspect Harness diagnostics; `failOnStartupError` is
  intentional and prevents silent model-memory fallback.
- No profile after configuration change: restart the Harness Web profile or
  wait for its Cordis HMR reload, then create a new chat. Existing sessions
  retain their original plugin revision.
- Do not expose the MCP beyond `127.0.0.1`; no remote authentication is set up.

## Model switching and safety

The MCP is attached to the `Trading Analyst` profile, not to a specific model.
A compatible tool-using model can be selected for a new chat without changing
the MCP configuration. The integration exposes no order, cancel, position,
execution, broker, scanner, or session-lock tools.
