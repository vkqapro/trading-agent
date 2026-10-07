# Strategy Scanner Explainability Schema 1.1

Schema 1.1 adds deterministic audit metadata without changing the existing signal, level, trade-plan, score, status, or rejection fields. Existing fields remain backward compatible.

## Per-strategy additions

- `pattern_definition`: strategy name, direction, pattern family, and ordered semantic steps.
- `pattern_bars`: exact input-frame positions and timestamps used by the selected rule path. Each bar includes its role and saved OHLCV values.
- `trigger_evidence`: named boolean and numeric outcomes produced by the authoritative strategy path.
- `rejection_evidence`: normalized failure reasons when the engine selected a candidate path but a post-pattern filter rejected it. It remains empty when the engine has no explicit failure reason.
- `chart_annotations`: ready-to-draw `bar_marker`, `horizontal_level`, and `trade_line` objects derived from the saved result.

The existing `signal` object now also includes `signal_bar_date` and `signal_bar_index`. The existing `level` object includes `level_type`. The existing `trade_plan` object includes `reward_risk` as an alias of `rr`.

## Pattern-bar semantics

| Strategy | Pattern bars |
|---|---|
| LP1 LONG | `bar_1`: penetration/reclaim bar; `signal_bar`: following entry-trigger bar |
| LP2 LONG | `bar_1`: penetration bar; `bar_2`: reclaim bar; `signal_bar`: following entry-trigger bar |
| PRB1 LONG | `bar_1`: breakout bar; `signal_bar`: following hold/entry-trigger bar |
| PRB2 LONG | `bar_1`: breakout bar; `bar_2`: hold bar; `signal_bar`: following entry-trigger bar |

A bar `index` is the zero-based positional index in the exact closed-bar input frame identified by `data.input_hash`, `data.first_bar_timestamp`, `data.last_bar_timestamp`, and `data.bars_used`.

## Status behavior

- `ENTRY_SIGNAL`: exact pattern bars, trigger evidence, and chart annotations are populated.
- `REJECTED`: pattern bars are populated; `rejection_evidence` identifies known post-pattern failures.
- `NO_SIGNAL`: `pattern_definition` is present, while bars/evidence/annotations remain empty rather than inventing a failed path.
- `INSUFFICIENT_DATA`: `rejection_evidence` contains `insufficient_bars`.
- `ERROR`: `rejection_evidence` contains `evaluation_error`.

## Compatibility

Artifacts created before schema 1.1 remain readable but cannot gain historical pattern identities without rerunning the scanner. New MCP responses preserve all existing fields and append the explainability fields.
