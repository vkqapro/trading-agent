# One-Bar False Breakout

## Runtime status

`false_breakout_one_bar` is in the source default `StrategyConfig.enabled_strategies` list and is evaluated by the production router while enabled.

## Confirmed detector contract

The detector normalizes the supplied bars and evaluates a level zone.

- The long path requires a candle to break below the zone and close back above its lower boundary with a lower wick larger than its body.
- The short path is the mirror: a break above the zone, a close back below its upper boundary, and an upper wick larger than its body.
- A qualifying path still needs the code's confirmation, trend-context, ATR/travel, target, and score gates. Entry is the last confirmation close; stop is derived from the false-break candle and confirmation extremes.

Exact minimum strength, confirmation count, score, and volatility thresholds are implementation/config values, not analyst assumptions. The router applies additional stop/target acceptance checks after the detector.

## Analyst evidence

Use the saved reason list, zone, confirmation type, ATR status, score, plan, and router status when they were recorded. A rejected detector decision and a router rejection are distinct facts.

## Code and test authority

- `src/strategy/false_breakout_one_bar.py:detect_false_breakout_one_bar`
- `src/strategy/strategy_router.py:route_strategies`
- `tests/test_false_breakouts.py`
- `tests/test_strategy_router.py`
