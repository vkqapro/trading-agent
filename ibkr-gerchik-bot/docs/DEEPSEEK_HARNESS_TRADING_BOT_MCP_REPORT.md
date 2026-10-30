# DeepSeek Harness Trading Bot MCP Integration Report

## Configuration inspection

- Harness installation: `C:\Users\Vitaly\AppData\Local\npm-cache\_npx\4f4f47d9854f3c73`
- Harness home: `C:\Users\Vitaly\.dsh`
- Active Web profile: `C:\Users\Vitaly\.dsh\profiles\web`
- Composition mechanism: Cordis bundle patches and agent-preset declarations
- Existing MCP client: `@deepseek-ai/dsh-mcp-client`
- Existing MCP example: `project_runner` in the Web profile patch
- Existing profiles inspected: Web, Headless, and legacy agent preset directories

## Changed configuration

`C:\Users\Vitaly\.dsh\profiles\web\cordis.patch.yml` now contains a
`preset-trading-analyst` declaration with:

```yaml
serverName: trading_bot_data
transport: streamable-http
url: http://127.0.0.1:8765/mcp
headers: {}
toolCallTimeoutMs: 60000
failOnStartupError: true
reconnect:
  enabled: true
  maxAttempts: 5
```

The repository source bundle is [harness/trading-analyst](../harness/trading-analyst).

## Evidence completed

- Trading Bot MCP `/health`: verified `status: ok`.
- MCP Streamable HTTP initialize: verified.
- MCP raw tool discovery: verified all five tools.
- Direct MCP AAPL snapshot, history, levels, and context calls: previously verified.
- Harness composition dump: confirmed `trading-analyst`, `Trading Analyst`,
  `mcp-trading-bot-data`, `trading_bot_data`, and the target URL.

## Harness live-chat evidence

Not completed in this execution. The local Harness Web process requires its
fresh per-process authenticated URL (`dsh web: ...?token=...`) before browser
automation can access the UI. The current process was running, but its launch
URL was not available to this session; unauthenticated requests returned HTTP
401. No claim is made that a real Harness chat executed a tool call.

Therefore the following remain pending human/UI validation:

- AAPL coverage chat
- AAPL 60-day closed-history chat
- AAPL consolidated-level chat
- combined candles plus levels analysis
- level-context drilldown
- second-model test
- offline MCP behavior through Harness UI
- unknown-symbol and excessive-history behavior through Harness UI

## Safety

The configured profile contains only the read-only Trading Bot MCP client and
analyst persona. No order-write, broker, scanner, job, or session-lock tools
were added. No broker order was placed and no Trading Bot MCP code was changed.

## Status

`TRADING BOT DATA MCP ↔ DEEPSEEK HARNESS: NOT READY`

Blocker: real Harness chat/tool-call validation requires opening the currently
running Harness Web process using its fresh authenticated URL. The integration
configuration and MCP endpoint are ready, but live model-visible tool usage is
not honestly verified yet.
