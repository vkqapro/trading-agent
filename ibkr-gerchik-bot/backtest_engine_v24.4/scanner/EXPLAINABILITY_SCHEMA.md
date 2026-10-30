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

## Candidate lifecycle

The production screener keeps the existing pattern and entry evaluation as the
authority, then finalizes an untriggered candidate from that same latest daily
or current-session partial-daily row:

| `lifecycle_status` | Condition | `lifecycle_reason` | Trade fields |
| --- | --- | --- | --- |
| `PENDING_ENTRY` | `entry_triggered=false`, current session is still in progress, and the selected structural stop has not been breached | `waiting_for_entry` | `trade_occurred=false`, `realized_pnl=0`, `realized_r=0` |
| `ENTRY_TRIGGERED` | Existing entry trigger is true, or 5m chronology proves the entry touched before invalidation | `entry_triggered` or `entry_triggered_before_invalidation` | Existing downstream status is preserved |
| `INVALIDATED` | 5m chronology proves invalidation touched before entry, or daily-only fallback sees the entry-day high/low breach the existing selected stop boundary | `pre_entry_structure_broken` | `trade_occurred=false`, `realized_pnl=0`, `realized_r=0` |
| `EXPIRED` | `entry_triggered=false`, the entry day is complete, and the stop boundary was not breached | `entry_window_closed` | `trade_occurred=false`, `realized_pnl=0`, `realized_r=0` |
| `AMBIGUOUS_INTRABAR_ORDER` | Both entry and invalidation are touched inside the same 5m candle, so OHLC cannot establish event order | `entry_and_invalidation_same_5m_bar` | Conservative no-trade: `trade_occurred=false`, `realized_pnl=0`, `realized_r=0` |

The structural boundary is the strategy-specific `stop_price` already produced
by the screener (`behind_signal_bar`, `behind_two_bar_structure`,
`behind_level`, or `behind_day1`). Invalidation uses a long low breach or a
short high breach; it does not introduce a separate close-only strategy rule.
The open-session path remains `closed daily + current 5m ->
build_partial_daily_frame -> run_market_screener`, so an incomplete current
session is not expired merely because its daily candle is unfinished. The exact
5m rows are retained as in-memory provenance on that same partial frame. The
authoritative finalizer compares the first entry-touch timestamp with the first
invalidation-touch timestamp for the entry day. If no usable 5m rows cover that
day, it explicitly reports `chronology_source=daily_ohlc_fallback` and applies
the deterministic existing daily high/low rule. If both thresholds occur in
one 5m row, it reports `AMBIGUOUS_INTRABAR_ORDER` and does not claim a trade.

Signals also expose `chronology_source`, `chronology_event`,
`entry_touch_time`, and `invalidation_touch_time` so Web, Radar, batch, and MCP
surfaces can audit the same lifecycle decision without reinterpreting it.

A bar `index` is the zero-based positional index in the exact closed-bar input frame identified by `data.input_hash`, `data.first_bar_timestamp`, `data.last_bar_timestamp`, and `data.bars_used`.

## Status behavior

- `ENTRY_SIGNAL`: exact pattern bars, trigger evidence, and chart annotations are populated.
- `REJECTED`: pattern bars are populated; `rejection_evidence` identifies known post-pattern failures.
- `NO_SIGNAL`: `pattern_definition` is present, while bars/evidence/annotations remain empty rather than inventing a failed path.
- `INSUFFICIENT_DATA`: `rejection_evidence` contains `insufficient_bars`.
- `ERROR`: `rejection_evidence` contains `evaluation_error`.

## Compatibility

Artifacts created before schema 1.1 remain readable but cannot gain historical pattern identities without rerunning the scanner. New MCP responses preserve all existing fields and append the explainability fields.
