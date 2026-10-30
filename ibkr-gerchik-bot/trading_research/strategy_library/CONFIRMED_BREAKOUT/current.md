# Confirmed Breakout

## Runtime status

`confirmed_breakout` is in the source default `StrategyConfig.enabled_strategies` list. The router invokes `detect_breakout` for every supplied level while it remains enabled.

## Confirmed detector contract

The detector uses the latest trading session only and requires at least four bars plus a level that meets the configured strength threshold.

- A long setup treats the level as resistance, requires a breakout close above the level zone, then a qualifying confirmation close that continues to hold above it.
- A short setup is the mirrored support breakdown path, with the breakout and confirmation closing below the zone.
- The detector rejects overextended moves and candidates that fail structural risk, ATR/travel, target, or reward/risk checks. Its entry is the confirmation close and its stop is derived from the breakout/confirmation structure.

The router is a further acceptance gate: it recalculates stop and target and rejects invalid or insufficient-reward/risk plans.

## Analyst evidence

Use direction, the recorded breakout-candle index, confirmation type, level zone, ATR, and accepted/rejected router result. Do not treat a raw breakout detector result as proof of a finalized plan.

## Code and test authority

- `src/strategy/breakout.py:detect_breakout`
- `src/strategy/strategy_router.py:route_strategies`
- `tests/test_breakout.py`
- `tests/test_breakout_signal.py`
- `tests/test_strategy_router.py`
