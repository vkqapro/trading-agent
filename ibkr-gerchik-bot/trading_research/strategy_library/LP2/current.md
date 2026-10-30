# LP2

## Runtime status

LP2 is one of the four static production scanner identifiers. Its registered contract is `LONG`, `1D`, and a minimum of 23 OHLCV bars.

## Confirmed detector contract

1. Price approaches an eligible level from above.
2. The penetration bar trades and closes below that level.
3. A later reclaim bar closes back above it and passes the implementation's volume and range conditions.
4. The following signal bar reaches the calculated entry trigger.

The live scanner adapter routes LP2 to the canonical Web Screener evaluator. The evaluator remains the authority for exact thresholds, levels, and calculated plan fields.

## Analyst evidence

Use returned pattern-bar dates, trigger/rejection evidence, ATR, volume, level, and calculated plan fields when available. Do not convert a two-bar label into rules beyond the recorded evidence.

## Code and test authority

- `backtest_engine_v24.4/scanner/registry.py`
- `backtest_engine_v24.4/scanner/adapters/lp_prb.py:evaluate_symbol_canonical`
- `dashboard_react/market_screener.py:_detect_signals`
- `backtest_engine_v24.4/scanner/explainability.py:pattern_definition`
- `backtest_engine_v24.4/tests/test_scanner_mcp.py`
- `backtest_engine_v24.4/tests/test_canonical_live_v1.py`
