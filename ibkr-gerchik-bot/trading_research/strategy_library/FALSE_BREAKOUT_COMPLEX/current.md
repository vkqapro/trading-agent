# Complex False Breakout

## Runtime status

`false_breakout_complex` is a real production-router detector, but it is not in the source default enabled-strategies list. The router invokes it only when the runtime configuration explicitly enables it.

## Confirmed detector contract

The detector implements the multi-bar false-breakout path using the shared false-breakout zone, context, target, and TradeSignal helpers.

- It evaluates a complex sequence rather than the one-bar or two-bar forms.
- It emits a candidate only when its source-defined confirmation, trend/context, volatility, target, and score checks pass.
- The production router is a separate final gate that recalculates stop/target and can reject a detector candidate.

The strategy code, not this document, defines how many bars qualify and the exact geometry/thresholds.

## Analyst evidence

Use persisted detector reasons, pattern metadata, score, volatility/ATR evidence, and router status. Treat absent pattern-bar evidence as missing data rather than reconstructing a complex pattern from a label.

## Code and test authority

- `src/strategy/false_breakout_complex.py:detect_false_breakout_complex`
- `src/strategy/strategy_router.py:route_strategies`
- `tests/test_false_breakouts.py`
- `tests/test_strategy_router.py`
