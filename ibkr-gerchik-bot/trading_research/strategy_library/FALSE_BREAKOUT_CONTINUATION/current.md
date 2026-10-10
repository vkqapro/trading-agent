# False Breakout Continuation

## Runtime status

`false_breakout_continuation` is a real production-router detector, but it is not in the source default enabled-strategies list. It runs only when runtime configuration explicitly includes it.

## Confirmed detector contract

This detector is a continuation entry following the source-defined prior-session false breakdown at support.

- It evaluates the prior-session setup and the current-session continuation conditions before producing a candidate.
- The emitted candidate uses the strategy identifier `false_breakout_continuation` and is subject to the same router-level final stop/target validation as other routed signals.
- An emitted detector candidate can therefore be absent from accepted router output when its finalized plan is invalid.

The source owns the detailed session, candle, level, stop, target, and volatility conditions.

## Analyst evidence

Use the persisted detector reasons, prior/current-session evidence, plan fields, and router status. Do not collapse a detector candidate and an accepted router signal into the same fact.

## Code and test authority

- `src/strategy/false_breakout_continuation.py:detect_false_breakout_continuation`
- `src/strategy/strategy_router.py:route_strategies`
- `tests/test_false_breakout_continuation.py`
- `tests/test_strategy_router.py`
