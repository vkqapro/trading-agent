# Inefficiency Reclaim

## Runtime status

The `INEFFICIENCY_RECLAIM` subsystem is implemented as a separately scheduled scanner. Its source default is disabled, and its settings validator permits only `paper` mode with live trading disabled. When enabled, the job calls the scanner, which evaluates the pure strategy and records its stateful setup lifecycle.

## Confirmed detector contract

The strategy evaluates completed market bars in stages.

1. It calculates a robust ATR and detects bullish or bearish displacement. The tests establish that the two directions are mirrored and that incomplete displacement bars are ignored.
2. It constructs an eligible strict three-bar gap or low-overlap displacement zone and evaluates a retracement into that zone.
3. It chooses a confirmation using the source priority: sweep-and-reclaim first, then two-bar reclaim, then micro break of structure.
4. It evaluates hard gates and builds a candidate with its direction, state, diagnostics, confirmation, and order plan when the required conditions are satisfied.

The code/configuration own the numerical ATR, body, close-location, relative-volume, zone-width, liquidity, freshness, score, risk, and expiration thresholds. This document does not substitute values or imply that any observed zone became an order.

## Analyst evidence

Use the candidate state, zone type/direction, retracement result, confirmation type, gate diagnostics, score/grade, plan-risk status, and persisted scanner/job status. Historical-anchor diagnostics intentionally differ from a live runtime risk check; do not infer a missing quote failure from an historical analysis candidate.

## Code and test authority

- `src/strategy/inefficiency_reclaim.py:evaluate_strategy`
- `src/scanners/inefficiency_reclaim.py:run_inefficiency_reclaim_screener`
- `src/jobs/inefficiency_reclaim.py:run_inefficiency_reclaim_job`
- `src/config.py:InefficiencyReclaimSettings`
- `tests/test_inefficiency_reclaim.py`
- `tests/test_inefficiency_reclaim_scanner.py`
- `tests/test_inefficiency_reclaim_jobs.py`
