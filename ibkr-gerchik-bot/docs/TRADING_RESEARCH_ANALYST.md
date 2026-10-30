# Trading Research Analyst

The Trading Research Analyst is an observer. It reads persisted Orders Journal and candle data, calculates deterministic metrics, creates optional research artifacts, and proposes hypotheses for validation. It cannot trade, call a broker, change a strategy, alter configuration, write production data, or modify the production repository at runtime.

## Interface

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -B -m trading_research.cli trade-review --from 2026-10-01 --to 2026-10-05 --strategy PRB1
.\.venv\Scripts\python.exe -B -m trading_research.cli strategy-review --strategy PRB1 --days 90
.\.venv\Scripts\python.exe -B -m trading_research.cli list-strategies --strategy PRB1
```

`-B` prevents Python bytecode-cache writes inside the repository. Reviews emit versioned JSON to stdout. `--write-artifact` is optional; it writes only below `C:\AI\TradingResearch\workspace\exports\`. The runtime CLI has no alternate-root option or environment override. A workspace inside this repository, path traversal, or a symlink/junction escape is rejected.

## Data and safety

The analyst reads the existing `memory/order_requests.json`, runtime Orders Journal ledgers, and `memory/bars` CSV cache. It does not import the production journal or bar-store modules at runtime because their import initializes the production file logger. The narrow adapter is limited to the persisted formats those components use.

Only completed, fill-evidenced records with realized P&L enter realized-performance metrics. `OPEN`, `UNFILLED`, rejected/canceled, and `CLOSED_UNKNOWN` records remain visible in status coverage but are excluded. Missing strategy attribution is emitted as `strategy_unknown`; missing candles set `market_context_available` to false. No missing fill, exit, or market data is fabricated.

If a future adapter reads SQLite, it must use `trading_research.safety.open_sqlite_readonly`, which opens a URI with `mode=ro` and `PRAGMA query_only`.

## Strategy library

`trading_research/strategy_library/registry.json` is data-only. Every implementation-confirmed entry links its Markdown definition to authoritative code and tests: the LP/PRB production scanner, the configured technical router, the separately feature-gated Inefficiency Reclaim scanner, and Decision Lab's analysis-only BMSB/Gaussian monitors. `PB1`, `PB2`, `LB1`, and `LB2` remain discoverable reserved records with explicit `source_definition_unavailable` status; no rule is inferred from their names. The library loader rejects a documentation path that escapes this directory.

## Interpreting JSON

`summary`, strategy metrics, comparisons, R multiples, P&L, MFE, and MAE are deterministic calculations. Loss-classification tags are evidence candidates, not conclusions. Hermes renders direct calculations as **FACT**, patterns as **OBSERVATION**, possible explanations as **HYPOTHESIS**, and a proposed test as **VALIDATION REQUIRED**. Correlation does not establish causation.
