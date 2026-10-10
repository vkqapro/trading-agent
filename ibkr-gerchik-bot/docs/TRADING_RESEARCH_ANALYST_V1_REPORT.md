# Trading Research Analyst V1 — Implementation Report

## Delivered

- Added the standalone `trading_research` package: immutable normalized trade models, read-only Orders Journal and candle-cache adapters, deterministic metrics, MFE/MAE, grouped comparisons, optional external artifacts, strategy library, and JSON CLI.
- Added the model-invoked Hermes skill at `C:\Users\Vitaly\.hermes\skills\trading-research-analyst\SKILL.md`.
- Added analyst documentation and safety/analytics tests under `tests/trading_research`.
- Verified the analyst-facing library against code and tests for the LP/PRB production scanner, the technical strategy router, feature-gated Inefficiency Reclaim, and analysis-only BMSB/Gaussian monitor sources.

## Safety

The analyst contains no import of production `src` modules or execution API, so no broker/order/approval path is reachable. Its adapter reads the persisted Orders Journal JSON ledgers and candle CSV cache directly because importing the existing production readers initializes a repository file logger. Generated JSON can only be written under `C:\AI\TradingResearch`; the runtime CLI has no alternate-root flag or environment override, and production-root destinations, traversal, and resolved symlink/junction escapes fail closed. The Hermes CLI requires `python -B` and the CLI rejects a non-`-B` invocation to prevent bytecode-cache writes.

## Hermes and CLI

Hermes runs a local command such as:

```powershell
C:\Users\Vitaly\Documents\GitHub\trading-agent\ibkr-gerchik-bot\.venv\Scripts\python.exe -B -m trading_research.cli strategy-review --strategy PRB1 --days 90
```

An example Hermes request is: “Analyze only losing PRB1 trades for the last 90 days. Separate FACT, OBSERVATION, HYPOTHESIS, and VALIDATION REQUIRED.”

## Validation

- Current strategy-library, router, Inefficiency Reclaim, Decision Lab source, and production-scanner verification suite: 99 passed, 1 symlink-permission skip, and 2 existing dependency deprecation warnings.
- The live `-B` CLI smoke test returned the current persisted journal result and produced identical SHA-256 values before/after for `memory/order_requests.json`, `memory/runtime/state.json`, and `memory/runtime/closed_positions.json`.
- The exact Hermes virtual-environment command passed without a `tzdata` installation; the package uses an in-package modern US-DST fallback only when IANA timezone data is unavailable.
- The Orders Journal timestamp assertions now explicitly test its existing canonical contract: absent timestamp values serialize as `""`, and legacy date-only values normalize to offset ISO-8601. `src/journal/order_journal.py` was not modified.

## Remaining limitations

The current checkout has confirmed production scanner identifiers `LP1`, `LP2`, `PRB1`, and `PRB2`; the technical router has default enabled `rebound`, `confirmed_breakout`, and `false_breakout_one_bar` detectors plus three runtime-selectable false-breakout variants; Inefficiency Reclaim is implemented behind a disabled-by-default paper-only feature flag; and the Decision Lab registry includes analysis-only BMSB and Gaussian monitors. Every one has a local Markdown definition with code/test authority. The checkout contains no PB1/PB2/LB1/LB2 source implementation, so those records remain explicitly `source_definition_unavailable`, not invented strategy rules. Historical strategy attribution remains dependent on the existing journal metadata; missing identities are explicitly emitted as `strategy_unknown`.
