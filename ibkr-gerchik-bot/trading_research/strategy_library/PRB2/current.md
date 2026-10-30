# PRB2

## Runtime status

PRB2 is one of the four static production scanner identifiers. Its registered contract is `LONG`, `1D`, and a minimum of 23 OHLCV bars.

## Confirmed detector contract

1. Price approaches an eligible resistance level from below.
2. The breakout bar trades and closes above the level with valid volume and range.
3. A hold bar preserves the breakout above the level.
4. The following signal bar opens while holding the level and reaches the calculated entry trigger.

The live scanner adapter routes PRB2 to the canonical Web Screener evaluator. The evaluator remains authoritative for thresholds and plan values.

## Analyst evidence

Use recorded breakout, hold, and signal-bar provenance; level; ATR; volume; trigger/rejection evidence; and plan fields. Do not infer a missing hold from the name.

## Code and test authority

- `backtest_engine_v24.4/scanner/registry.py`
- `backtest_engine_v24.4/scanner/adapters/lp_prb.py:evaluate_symbol_canonical`
- `dashboard_react/market_screener.py:_detect_signals`
- `backtest_engine_v24.4/scanner/explainability.py:pattern_definition`
- `backtest_engine_v24.4/tests/test_scanner_mcp.py`
- `backtest_engine_v24.4/tests/test_canonical_live_v1.py`
