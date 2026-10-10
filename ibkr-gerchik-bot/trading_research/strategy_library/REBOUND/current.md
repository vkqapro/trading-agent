# Rebound

## Runtime status

`rebound` is in the source default `StrategyConfig.enabled_strategies` list. The production router calls it for every supplied level while it remains enabled.

## Confirmed detector contract

The detector uses the latest trading session only and requires at least three bars plus a level that meets the configured strength threshold.

- A long rebound requires an approach from above, a rejection candle that trades into the level zone but closes back above it with a lower-wick rejection, then a bullish confirmation close above the rejection close and zone.
- A short rebound is the mirrored path from below: upper-wick rejection through the zone and a bearish confirmation close below the rejection close and zone.
- The detector derives an entry from the confirmation close and a structural stop. It accepts a candidate only when its risk, ATR/travel, target, and reward/risk checks pass.

The router then recalculates the final stop and target and can reject a detector candidate that fails its own post-detector risk checks.

## Analyst evidence

Use the emitted direction, entry/stop/target, level zone, confirmation type, ATR fields, and router result. A detector name does not establish that the router accepted a tradable plan.

## Code and test authority

- `src/strategy/rebound.py:detect_rebound`
- `src/strategy/strategy_router.py:route_strategies`
- `tests/test_rebound_signal.py`
- `tests/test_strategy_router.py`
