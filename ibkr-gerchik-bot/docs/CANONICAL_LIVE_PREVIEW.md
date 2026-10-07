# Canonical live preview

`backtest_engine_v24.4/live_engine` provides a deterministic, read-only two-stage preview for LONG LP1, LP2, PRB1 and PRB2.

1. Historical setup detection evaluates only closed daily bars strictly before the current session (`as_of`). Setup IDs are content-stable (`symbol`, strategy, setup date and level), so a setup remains the same from WATCH to READY and multiple setups are retained.
2. `CurrentSessionState` records session date, OHLCV, as-of, completeness, source, timeframe, persisted intraday timestamp and freshness. Freshness is centrally classified as `FRESH` (<=900s), `DELAYED_USABLE` (>900s and <=3600s), or `STALE` (>3600s). Stale or invalid data can never become READY.

Statuses are `WATCH`, `READY`, `REJECTED`, `STALE_DATA`, `INVALID`, and `NO_SETUP`. `WATCH` and `READY` may carry either `FRESH` or `DELAYED_USABLE` data. READY means that the entry level was touched in persisted session data; it is not an order approval, recommendation, or execution instruction.

A READY delayed-data proposal may pass through the canonical planner, but it always carries `execution_revalidation_required=true` and `execution_revalidation_reason=DELAYED_MARKET_DATA`. The future execution chain remains: READY → CandidateProposal → user approval → fresh broker/execution revalidation (price, chase, entry validity, stop, target, risk, funds, limits) → submit or revalidation failure.

MCP adds the focused `preview_live_setups` tool to the existing Strategy Scanner service. It reads persisted bars only and does not call brokers, orders, schedulers, Telegram, LLM, or the legacy `run_universe_scan` path.
