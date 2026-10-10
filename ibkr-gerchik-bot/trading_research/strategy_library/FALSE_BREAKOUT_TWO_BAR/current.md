# Two-Bar False Breakout

## Runtime status

`false_breakout_two_bar` is a real production-router detector, but it is not in the source default enabled-strategies list. The router evaluates it only when the runtime configuration explicitly includes its identifier.

## Confirmed detector contract

This detector uses the same level-zone, context, volatility, target, and explainability helpers as the one-bar detector, with a two-bar false-break/reclaim sequence followed by its required confirmation.

- Long and short paths are evaluated as mirrors around the level zone.
- A potential pattern is still rejected unless the detector's confirmation, trend, ATR/travel, target, and score conditions pass.
- The detector supplies a proposed plan; the router recalculates stop/target and can reject it after detection.

The exact bar geometry and thresholds remain in the cited source implementation.

## Analyst evidence

Use recorded reasons, pattern/confirmation metadata, score, ATR status, proposed plan, and router status. Do not infer that a two-bar name alone establishes a valid reclaim.

## Code and test authority

- `src/strategy/false_breakout_two_bar.py:detect_false_breakout_two_bar`
- `src/strategy/strategy_router.py:route_strategies`
- `tests/test_false_breakouts.py`
- `tests/test_strategy_router.py`
