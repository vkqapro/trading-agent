# PRB1

## Runtime status

PRB1 is one of the four static production scanner identifiers. Its registered contract is `LONG`, `1D`, and a minimum of 23 OHLCV bars.

## Confirmed detector contract

1. Price approaches an eligible resistance level from below.
2. One breakout bar trades and closes above the level with valid volume.
3. The following signal bar opens while holding the level.
4. That signal bar reaches the calculated entry trigger.

The live scanner adapter routes PRB1 to the canonical Web Screener evaluator. It owns all numerical thresholds and calculated trade-plan values.

## Analyst evidence

Use recorded breakout/signal-bar provenance, level, ATR, volume, trigger evidence, rejection evidence, and plan fields. A `READY`/entry-signal result is distinct from a pattern that did not reach its entry trigger.

## Code and test authority

- `backtest_engine_v24.4/scanner/registry.py`
- `backtest_engine_v24.4/scanner/adapters/lp_prb.py:evaluate_symbol_canonical`
- `dashboard_react/market_screener.py:_detect_signals`
- `backtest_engine_v24.4/scanner/explainability.py:pattern_definition`
- `backtest_engine_v24.4/tests/test_scanner_mcp.py`
- `backtest_engine_v24.4/tests/test_canonical_live_v1.py`
- `tests/test_web_mcp_scanner_parity.py`
