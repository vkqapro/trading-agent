# Pre-Trade Reviewer V1

## Role

Pre-Trade Reviewer is a dedicated, read-only Hermes profile for reviewing an
already-discovered production scanner candidate before a future human approval.
Daily Trader discovers; Pre-Trade Reviewer reviews; Trading Analyst remains the
post-trade operational/research role. The profiles are not merged.

## Safety boundary

The reviewer cannot submit, queue, approve, reject, cancel, or modify an order
or candidate. It cannot change entry, stop, target, quantity, risk, scanner,
Radar, strategy, Paper/Live state, or broker state. It does not add Telegram,
Slack, Discord, email, or webhook delivery. Every response carries
`read_only: true`, `orders_submitted: 0`, and `approval_created: false`.

## Architecture and API

V1 reuses the existing authoritative loopback endpoint:

`GET /api/daily-trader/symbol/{symbol}`

The approved client is `tools/pre_trade_reviewer_api.py`; it calls no scanner
module and no broker endpoint. It consumes the Daily Trader payload and applies
the pure deterministic classifier in `src/pre_trade_reviewer.py`.

Batch review calls the same endpoint once per explicitly supplied symbol through
the client. This avoids a redundant API namespace while preserving the existing
scanner, Radar, lifecycle, chronology, and risk authority. Health reuses
`GET /api/daily-trader/health`.

## Skills and profile

The repository-owned Hermes templates are under
`config/hermes/pre-trade-reviewer/`:

- `pre-trade-review`: one symbol, authoritative API only.
- `pre-trade-review-batch`: explicit symbols, structured comparison only.

The installed profile name is `pre-trade-reviewer`, display role
`Pre-Trade Reviewer`, using the existing local Unslouth chat-completions model
and only the `skills` and narrow `terminal` toolsets. No generic shell,
repository editing, broker, approval, execution, or messaging capability is
enabled.

## Review states and hard rules

- `ELIGIBLE_FOR_APPROVAL`: active authoritative lifecycle, complete required
  setup fields, and all safety assertions pass with no policy warnings. This is
  not an approval.
- `REVIEW_WITH_CAUTION`: active setup with meaningful warnings such as Radar
  direction conflict, partial quality data, or market status not OPEN.
- `NOT_ELIGIBLE_FOR_APPROVAL`: `INVALIDATED`, `EXPIRED`,
  `AMBIGUOUS_INTRABAR_ORDER`, `NO_SETUP`, unknown/non-reviewable lifecycle,
  missing authoritative setup fields, `read_only != true`, or
  `orders_submitted != 0`.

`PENDING_ENTRY`, `ENTRY_TRIGGERED`, and existing authoritative `READY` are
reviewable names. A pending entry is explicitly reported as “Entry has not
triggered yet.” Lifecycle remains authoritative; the reviewer never upgrades a
terminal state.

## Warnings and data quality

`direction_conflict = true` is surfaced as a counter-trend warning. It does not
become a hard rejection by itself. `data_quality_complete = false` is rendered
as `DATA QUALITY PARTIAL` with the unmodified `missing_quality_data` list.
Unknown values remain unknown. Market status is copied and surfaced.

## Radar, lifecycle, chronology, and risk

Radar is context, not a setup generator. A high Radar score with `NO_SETUP`
remains `NOT_ELIGIBLE_FOR_APPROVAL`. Scanner lifecycle and chronology fields
are copied from the authoritative production response. Position size, risk
amount, risk percent, reward amount, and R:R are displayed as supplied; V1 does
not recompute or resize them. Risk Gate is not evaluated in this layer.

## Integration with Daily Trader

The user runs Daily Trader, sees active candidates, then explicitly asks the
Pre-Trade Reviewer to review one or more symbols. No profile automatically
invokes the other. If context handoff is unavailable, the symbols are explicit
inputs to the batch skill; raw JSON copy/paste is not required.

## Examples

Clean active setup: `PENDING_ENTRY`, aligned Radar, complete quality,
authoritative risk payload -> `ELIGIBLE_FOR_APPROVAL`, with the pending-entry
warning stated.

Counter-trend or partial-quality active setup -> `REVIEW_WITH_CAUTION` and the
exact warning/missing fields.

Invalidated or expired setup -> `NOT_ELIGIBLE_FOR_APPROVAL`; terminal, no longer
active, and no trade inferred.

`NO_SETUP` despite a strong Radar score -> `NOT_ELIGIBLE_FOR_APPROVAL`; no
production setup exists.

Unavailable API -> `PRE-TRADE REVIEW UNAVAILABLE`; no review and no action.

## Limitations and future compatibility

V1 has no Risk Gate action, approval card, human approval/rejection action, or
execution handoff. Its output is a review report only. The clean contract can
later feed a deterministic Risk Gate and approval card, but that authority is
deliberately not implemented here.
