---
type: "query"
date: "2026-08-24T12:54:33.859568+00:00"
question: "Implement Market Screener Crypto with the same LP1 LP2 PRB1 PRB2 strategy logic as stocks in a separate tab"
contributor: "graphify"
outcome: "useful"
source_nodes: ["market_screener.py", "run_market_screener", "_detect_signals", "server.py"]
---

# Q: Implement Market Screener Crypto with the same LP1 LP2 PRB1 PRB2 strategy logic as stocks in a separate tab

## Answer

Expanded from original query via graph vocab: screener scanner strategy pattern stock crypto market signal anchor risk okx chart. The shared strategy detector is dashboard_react/market_screener.py. Stock and crypto use the same run_market_screener and _detect_signals pipeline; dashboard_react/server.py provides asset-specific universe and candle loaders, and dashboard_react/index.html renders separate stock and crypto tabs while routing crypto charts and manual orders to OKX Demo endpoints.

## Outcome

- Signal: useful

## Source Nodes

- market_screener.py
- run_market_screener
- _detect_signals
- server.py