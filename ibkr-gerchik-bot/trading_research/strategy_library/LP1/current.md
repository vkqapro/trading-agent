# LP1

## Runtime status

LP1 is one of the four static production scanner identifiers. Its registered contract is `LONG`, `1D`, and a minimum of 23 OHLCV bars.

## Confirmed detector contract

1. Price approaches an eligible level from above.
2. One bar penetrates below the level and closes back above it.
3. That reclaim bar passes the implementation's range, wick, close-position, and volume conditions.
4. The following signal bar reaches the calculated entry trigger.

The live scanner adapter routes LP1 to the canonical Web Screener evaluator. Levels, numerical thresholds, entry, stop, target, score, and rejection values are calculated there and are not restated here.

## Analyst evidence

Use returned pattern-bar dates, trigger/rejection evidence, ATR, volume, level, and calculated plan fields when they are present. Do not infer a failed gate from the strategy name alone.

## Code and test authority

- `backtest_engine_v24.4/scanner/registry.py`
- `backtest_engine_v24.4/scanner/adapters/lp_prb.py:evaluate_symbol_canonical`
- `dashboard_react/market_screener.py:_detect_signals`
- `backtest_engine_v24.4/scanner/explainability.py:pattern_definition`
- `backtest_engine_v24.4/tests/test_scanner_mcp.py`
- `backtest_engine_v24.4/tests/test_canonical_live_v1.py`
