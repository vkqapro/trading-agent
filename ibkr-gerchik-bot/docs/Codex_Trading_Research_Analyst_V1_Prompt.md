# Codex Implementation Prompt — Read-Only Trading Research Analyst for Hermes

## Mission

Implement **V1 of a read-only Trading Research Analyst layer for Hermes** on top of the existing trading bot / Orders Journal / market-data infrastructure.

The system already has:
- a working trading engine;
- strategies such as `PB1`, `PB2`, `LB1`, `LB2`;
- an Orders Journal UI/backend showing open, closed, rejected/unfilled/observed trades;
- existing order/execution data;
- existing market/candle-fetching functionality;
- an existing MCP server used for trading/scanning workflows;
- Paper trading and existing execution/risk infrastructure.

Do **not** rebuild these systems.

The goal is to add a separate **research/analytics layer** that allows Hermes to answer questions such as:

> Analyze all closed trades from October 1, 2026 through October 5, 2026. Separate winners and losers. Compare PB1/PB2/LB1/LB2. Explain the most likely reasons for losses. Identify recurring patterns. Suggest hypotheses for strategy improvement. Do not change any trading code.

or:

> Review PB2 for the last 90 days. Compare winners vs losers. Find statistically meaningful differences in entry timing, ATR, distance to level, MFE/MAE, market conditions, stop behavior, target behavior, and execution quality.

This feature is **analysis only**.

It must never be capable of changing the production trading engine, changing a strategy, placing/canceling an order, changing a database row, modifying `.env`, changing strategy configuration, committing code, or writing into the production repository at runtime.

---

# 1. First step: inspect before implementing

Before writing code:

1. Inspect the existing repository structure.
2. Locate:
   - Orders Journal backend;
   - Orders Journal database/storage;
   - trade/order models;
   - strategy identifiers;
   - signal/setup records;
   - execution records;
   - existing Paper trading records;
   - candle-fetching logic;
   - historical candle cache/store, if present;
   - existing MCP server;
   - Hermes skills/examples already present in the environment/repository;
   - any existing analytics/statistics utilities.
3. Determine exactly which existing components can be reused.
4. Do not create a parallel trade journal or duplicate candle system unless absolutely necessary.
5. Preserve all existing production behavior.

Write a short implementation plan after inspection, then continue directly into implementation.

Do not stop for confirmation unless there is a true blocker that makes safe implementation impossible.

---

# 2. Non-negotiable safety architecture

The runtime Trading Research Analyst must be **read-only toward production**.

This is not merely a prompt instruction. Enforce it technically.

## 2.1 Production repository

The analyst runtime may:

- read source files;
- read strategy documentation;
- read database data;
- read candle/market data;
- execute explicitly safe analytics code.

It must not:

- write to any file inside the production trading repository;
- modify Python/TypeScript/HTML/config files;
- modify strategy definitions;
- modify `.env`;
- modify execution settings;
- update/insert/delete production DB records;
- submit orders;
- cancel orders;
- modify positions;
- run migrations;
- install dependencies into the trading engine at runtime;
- perform git commits, pushes, resets, rebases, or checkout operations.

## 2.2 Database

Whenever the research layer reads SQLite, open it using a true read-only connection where supported, for example:

```python
sqlite3.connect("file:/path/to/trading.db?mode=ro", uri=True)
```

If the production storage is not SQLite, implement the equivalent read-only access pattern.

No write-capable database connection should be exposed to the Hermes analyst runtime.

## 2.3 Workspace

All temporary/generated research files must go only to a dedicated research workspace outside production state.

Use a configurable path with this default on Windows:

```text
C:\AI\TradingResearch\
```

Suggested layout:

```text
C:\AI\TradingResearch\
├── workspace\
│   ├── temp\
│   ├── calculations\
│   ├── exports\
│   └── charts\
├── reports\
├── strategy_library\
└── cache\
```

The implementation must centralize workspace-path handling.

Add a guard such that every write path is checked before writing.

If a path is outside the allowed research workspace, fail closed with a clear error.

Example invariant:

```text
ALL WRITES MUST RESOLVE UNDER TRADING_RESEARCH_ROOT
```

Never silently fall back to writing into the repository.

## 2.4 Runtime safety validator

Implement a small reusable safety utility, for example:

```python
assert_research_write_path(path)
assert_read_only_source(path)
```

Use it for every generated CSV/JSON/chart/report/temp DB/file.

Add tests proving path traversal such as:

```text
..\..\production_repo\strategy.py
```

is rejected.

---

# 3. Do not modify strategy behavior

The implementation may add analytics/research code and may add metadata capture only when necessary and safe.

However:

- DO NOT change PB1 logic.
- DO NOT change PB2 logic.
- DO NOT change LB1 logic.
- DO NOT change LB2 logic.
- DO NOT change scanner thresholds.
- DO NOT change stop/target formulas.
- DO NOT change risk logic.
- DO NOT change execution behavior.
- DO NOT change approval behavior.
- DO NOT change order-routing behavior.
- DO NOT change Paper/Live mode behavior.

If analytics reveals a possible improvement, the analyst must output it only as a:

```text
HYPOTHESIS
```

Never as an automatic production change.

---

# 4. Core architecture

Implement approximately this architecture, adapting names to the existing codebase:

```text
                    EXISTING TRADING ENGINE
                             |
                       READ-ONLY DATA
                             |
              +--------------+--------------+
              |                             |
              v                             v
      Existing Orders Journal        Existing Candle/Data Layer
              |                             |
              +--------------+--------------+
                             |
                             v
                  Trading Analytics Service
                  / Read-Only Exporter
                             |
                             v
                    Hermes Research Skill
                             |
              +--------------+--------------+
              |                             |
              v                             v
        Strategy Library              Research Workspace
                                    C:\AI\TradingResearch\
```

There must be **no write arrow back into the Trading Engine**.

---

# 5. V1 components to implement

Implement these components.

## A. Read-only trade analytics service / CLI

Create a reusable read-only analytics entry point.

Suggested naming:

```text
trading_research/
    analytics_service.py
```

and/or CLI:

```text
python -m trading_research.cli ...
```

The exact structure may follow repository conventions.

It must support at least:

### Command 1 — trade review

Example:

```bash
python -m trading_research.cli trade-review \
  --from 2026-10-01 \
  --to 2026-10-05
```

Optional filters:

```text
--strategy PB1
--strategy PB2
--side LONG
--side SHORT
--symbol GSHD
--status CLOSED
```

### Command 2 — strategy review

Example:

```bash
python -m trading_research.cli strategy-review \
  --strategy PB2 \
  --days 90
```

or explicit date range.

### Output

Return machine-readable JSON to stdout.

Any exported artifacts must be written only under the research workspace.

---

# 6. Use existing journal data first

The Orders Journal already contains significant trade information.

Reuse the existing backend/data model.

At minimum attempt to retrieve, where available:

```text
trade/order id
symbol
market
source
strategy
strategy version
side
status
setup timestamp
signal timestamp
order timestamp
fill timestamp
exit timestamp
entry plan
entry fill
planned stop
current/final stop
planned target
current/final target
exit price
quantity
realized P&L
fees
exit reason
approval source
execution source
Paper/Live mode
```

Do not assume field names.

Inspect the existing schema and create an internal normalized research model.

Example:

```python
@dataclass(frozen=True)
class ResearchTrade:
    trade_id: str
    symbol: str
    strategy: str | None
    strategy_version: str | None
    side: str
    setup_time: datetime | None
    entry_time: datetime | None
    exit_time: datetime | None
    entry_price: Decimal | None
    stop_price: Decimal | None
    target_price: Decimal | None
    exit_price: Decimal | None
    quantity: Decimal | None
    realized_pnl: Decimal | None
    ...
```

The research model itself must not have save/update methods.

---

# 7. Strategy identity must become first-class metadata

The analyst must be able to distinguish:

```text
PB1
PB2
LB1
LB2
future strategies
```

If the current journal already stores strategy identity, reuse it.

If strategy identity is not currently persisted reliably, add the **smallest safe metadata-only change necessary** so future trades record:

```text
strategy
strategy_version
engine_version
strategy_config_hash (if practical)
signal/setup id (if practical)
```

This is the only category of production-side change that may be introduced without asking, and only if required for reliable historical attribution.

Rules:

1. Metadata capture must not affect signal generation.
2. Metadata capture must not affect execution.
3. Existing records must remain readable.
4. No destructive migration.
5. Any schema extension must be additive and backwards compatible.
6. Add regression tests proving order/scanner/execution behavior is unchanged.

If strategy can already be reconstructed reliably from existing immutable data, do not add schema changes.

---

# 8. Strategy Library

Do not hard-code all strategy meaning inside the Hermes skill.

Create a version-aware Strategy Library.

Suggested structure:

```text
strategy_library/
├── registry.yaml
├── PB1/
│   └── current.md
├── PB2/
│   └── current.md
├── LB1/
│   └── current.md
└── LB2/
    └── current.md
```

If reliable strategy versions already exist, prefer:

```text
PB1/
├── 1.3.md
├── 1.4.md
└── 1.5.md
```

## registry.yaml

Example shape:

```yaml
strategies:
  PB1:
    name: Pullback 1
    enabled: true
    documentation: PB1/current.md

  PB2:
    name: Pullback 2
    enabled: true
    documentation: PB2/current.md

  LB1:
    name: Level Breakout 1
    enabled: true
    documentation: LB1/current.md

  LB2:
    name: Level Breakout 2
    enabled: true
    documentation: LB2/current.md
```

Design this so future strategies such as:

```text
GAUSSIAN
ORB
VWAP_RECLAIM
MEAN_REVERSION
PB3
```

can be added without changing the analyst code.

---

# 9. Build strategy documentation from the real implementation

For PB1/PB2/LB1/LB2:

1. Inspect the real production strategy/scanner code.
2. Inspect existing docs/tests.
3. Create analyst-facing strategy documentation that accurately reflects the implementation.
4. Do not invent rules that are not present in code/tests/docs.
5. If a rule is ambiguous, mark it explicitly as ambiguous rather than guessing.

Each strategy document should include, where applicable:

```markdown
# Strategy

## Purpose

## Direction
LONG / SHORT

## Setup definition

## Required conditions

## Entry logic

## Stop logic

## Target logic

## Invalidation rules

## Time/session rules

## Volatility requirements

## Level requirements

## Volume requirements

## Expected behavior after entry

## Known failure modes

## Key variables for post-trade analysis

## Source-of-truth references
- relevant production files
- relevant tests
```

The Strategy Library is **documentation for analysis only**.

Changing these Markdown files must never alter trading behavior.

---

# 10. Historical market context

Reuse the existing candle-fetching layer.

The screenshot/UI already shows a `FETCH CANDLES` capability, so locate the backend path that supports it.

For each closed trade, attempt to reconstruct the market path around:

```text
setup
entry
exit
```

Use existing cached candles first.

Do not create unnecessary external market-data dependencies.

Support enough pre/post context to calculate meaningful metrics.

Example:

```text
daily bars: relevant lookback
5m bars: setup through exit, with pre-entry context
```

Use the same time zone conventions as the production engine.

Do not silently mix UTC/ET.

Normalize timestamps explicitly.

---

# 11. Deterministic metrics engine

LLM/Hermes must not be responsible for arithmetic.

Compute metrics deterministically in Python.

At minimum implement, when data supports them:

## Portfolio/period metrics

```text
closed trades
wins
losses
breakeven
win rate
loss rate
net P&L
gross profit
gross loss
profit factor
average win
average loss
average R
median R
expectancy in R
largest winner
largest loser
max consecutive wins
max consecutive losses
```

If data is sufficient:

```text
equity curve
max drawdown
```

## Per-strategy metrics

For each strategy:

```text
trade count
win rate
net P&L
avg R
median R
profit factor
average winner
average loser
```

## Per-trade derived metrics

Where possible:

```text
initial risk per share
planned reward per share
planned R:R
realized R
MFE
MFE in R
MAE
MAE in R
time to MFE
time to MAE
time in trade
time to target/stop if touched
slippage
entry deviation from planned entry
exit deviation
```

All LONG/SHORT calculations must be direction-aware.

Use Decimal where appropriate for money.

---

# 12. Context features

Calculate only features that can be derived reliably from existing data.

Potential features:

```text
ATR
ATR %
RVOL
volume vs rolling average
distance from setup level
distance from level in ATR
entry time
day of week
minutes since market open
gap %
stock vs VWAP
stock vs EMA20
stock vs EMA50
stock vs SMA200
intraday trend
daily trend
SPY trend
QQQ trend
sector trend if sector data already exists
spread/liquidity if captured
```

Do not invent unavailable data.

If SPY/QQQ context requires existing market-data fetches, use existing infrastructure.

Do not add Quiver, news feeds, social sentiment, Congress data, or unrelated external sources in V1.

---

# 13. Winner vs loser comparison

Implement deterministic grouped comparisons.

Example output:

```json
{
  "strategy": "PB2",
  "winner_count": 12,
  "loser_count": 18,
  "comparisons": {
    "entry_minutes_after_open": {
      "winner_median": 61,
      "loser_median": 18
    },
    "atr_pct": {
      "winner_median": 2.1,
      "loser_median": 4.8
    },
    "mfe_r": {
      "winner_median": 1.7,
      "loser_median": 0.18
    }
  }
}
```

Where practical, include robust statistics such as:

```text
median
IQR
mean
sample size
```

Do not overclaim statistical significance.

If implementing formal significance tests, label them correctly and account for small samples/multiple comparisons.

V1 may remain descriptive if rigorous statistical testing would add unnecessary complexity.

---

# 14. Loss classification support

Create a research-only classification vocabulary.

Suggested categories:

```text
ENTRY_QUALITY
LATE_ENTRY
EARLY_ENTRY
SETUP_INVALIDATION
STOP_PLACEMENT
TARGET_PLACEMENT
EXIT_MANAGEMENT
MARKET_REGIME
LOW_VOLUME
HIGH_VOLATILITY
EXECUTION_SLIPPAGE
INSUFFICIENT_LIQUIDITY
NO_CLEAR_CAUSE
```

Important:

- deterministic code should provide evidence;
- Hermes may infer a likely classification;
- inferred classifications must be marked as analytical interpretations;
- do not write them into production DB in V1;
- if persisted, persist only inside the research workspace.

---

# 15. Facts vs interpretation

Hermes output must strictly distinguish:

## FACT

Directly observed/calculated.

Example:

```text
PB2 had 14 trades, 3 winners, 11 losers, average -0.41R.
```

## OBSERVATION

Pattern supported by the data.

Example:

```text
9 of 11 PB2 losers occurred within the first 25 minutes after market open.
```

## HYPOTHESIS

Possible explanation or strategy adjustment.

Example:

```text
PB2 LONG may perform poorly during the first 25 minutes after the open.
```

## VALIDATION REQUIRED

Specify how to test the hypothesis.

Example:

```text
Backtest PB2 with and without a 09:55 ET eligibility filter across at least six months of comparable data.
```

Hermes must never present a hypothesis as a fact.

---

# 16. Small-sample protections

Implement guidance in the Hermes skill:

```text
< 5 comparable trades:
descriptive analysis only

5-19 comparable trades:
LOW CONFIDENCE

20-49:
MODERATE CONFIDENCE, depending on effect consistency

50+:
may support stronger conclusions, but still requires validation
```

Do not recommend production changes solely from a handful of trades.

Explicitly state:

```text
Correlation does not establish causation.
```

---

# 17. Strategy-change policy

The analyst must never modify a strategy.

Allowed output:

```text
Hypothesis:
PB2 LONG might benefit from delaying eligibility until 09:50 ET.

Evidence:
...

Recommended validation:
Run historical comparison.
```

Forbidden behavior:

```text
I updated PB2 to begin at 09:50.
```

Even if the user says:

> fix PB2

the research skill must respond with analysis/recommendation only.

Actual strategy modification belongs to a different development workflow.

---

# 18. Hermes Skill

Create a Hermes skill following the actual Hermes skill conventions found in the environment.

Suggested name:

```text
trading-research-analyst
```

or:

```text
trading-postmortem
```

The skill must explain:

- role;
- allowed operations;
- forbidden operations;
- workspace path;
- how to invoke the analytics CLI/service;
- how to read strategy definitions;
- how to analyze date ranges;
- how to analyze one strategy;
- how to handle missing data;
- how to separate fact/observation/hypothesis;
- small sample rules;
- how to report failures;
- absolute prohibition against production modifications.

The skill should support natural requests like:

```text
Analyze my closed trades from October 1 through October 5.

Review PB1 for the last month.

Compare PB1 and PB2 over the last 90 days.

Analyze only losing PB2 trades.

Why did I lose money this week?

Show me trades where entry quality looked good but exit management was poor.

Compare morning vs afternoon PB1 results.
```

---

# 19. No Telegram work in V1

Do not modify Telegram.

Do not add Telegram commands.

Do not modify Telegram approvals.

Do not modify existing trading MCP behavior for this task unless a tiny shared read-only utility is clearly the safest reuse.

The initial interface is:

```text
User -> Hermes -> Trading Research Skill -> local read-only analytics
```

---

# 20. No new trading MCP required in V1

Do not create a large new MCP layer unless existing Hermes skill architecture absolutely requires it.

Prefer:

```text
Hermes Skill
    ->
local CLI / local analytics module
    ->
read-only production data
```

We may later expose individual tools through MCP.

V1 should remain small, understandable, testable, and safe.

---

# 21. JSON response contract

Design a stable structured result.

Example high-level shape:

```json
{
  "schema_version": "1.0",
  "request": {
    "type": "trade_review",
    "from": "2026-10-01",
    "to": "2026-10-05",
    "strategies": []
  },
  "data_quality": {
    "complete": true,
    "missing_fields": [],
    "warnings": []
  },
  "summary": {
    "closed_trades": 19,
    "wins": 8,
    "losses": 11,
    "net_pnl": -126.44,
    "win_rate": 42.1,
    "avg_r": -0.08,
    "profit_factor": 0.91
  },
  "strategies": {},
  "comparisons": {},
  "trades": [],
  "artifacts": []
}
```

Keep the contract versioned.

Do not dump enormous raw candle arrays into Hermes context unless explicitly requested.

Summarize deterministic metrics and retain detailed artifacts in the research workspace.

---

# 22. Reports and charts

V1 may generate useful charts such as:

```text
equity curve
P&L by strategy
R distribution
winner vs loser MFE/MAE
P&L by time of day
P&L by day of week
MFE vs realized R
MAE vs realized R
```

All chart files go only under:

```text
C:\AI\TradingResearch\workspace\charts\
```

or the configured equivalent.

Use non-interactive chart generation.

Charts are optional outputs; analysis must still work without charts.

Do not add dashboard UI changes in V1 unless required.

---

# 23. Data-quality behavior

Never hide missing data.

If a trade lacks strategy identity, say:

```text
strategy_unknown
```

If candles cannot be reconstructed, mark:

```text
market_context_available = false
```

If entry fill is missing, do not fabricate one.

If exit is ambiguous, label it.

Include:

```text
data_quality
warnings
coverage
```

in output.

Hermes must consider data-quality limitations before making conclusions.

---

# 24. Read-only tests

Add tests proving safety.

At minimum:

## File safety

- writes inside research workspace succeed;
- writes to production repository fail;
- path traversal fails;
- symlink/junction escape is rejected if relevant on platform.

## DB safety

- analytics opens production DB read-only;
- INSERT/UPDATE/DELETE fail;
- analytics succeeds for SELECT.

## Trading safety

Prove research flow does not call:

```text
place_order
submit_order
cancel_order
modify_order
approve_order
execute_trade
```

Mock/spy these boundaries if needed.

## Strategy safety

Regression tests prove PB1/PB2/LB1/LB2 outputs are unchanged by this implementation.

## Existing system regression

Run existing tests.

Do not weaken or delete existing tests.

---

# 25. Analytics tests

Create fixtures for:

```text
winning LONG
losing LONG
winning SHORT
losing SHORT
target hit
stop hit
MFE before stop
MAE before target
partial/missing data
unfilled order
rejected order
closed_unknown
```

Validate:

```text
R calculations
MFE
MAE
profit factor
win rate
LONG/SHORT direction handling
date filtering
strategy filtering
timezone handling
```

---

# 26. Orders Journal status handling

Inspect real statuses used by the system.

Do not assume only `OPEN` and `CLOSED`.

The screenshots indicate examples such as:

```text
OPEN
UNFILLED
CLOSED_OBSERVED
CLOSED_UNKNOWN
```

There may also be rejected/canceled/etc.

Create an explicit mapping of:

```text
active
completed
non-executed
ambiguous
excluded from P&L
```

Document the mapping.

Only include appropriate completed/fill-known trades in realized performance statistics.

Expose excluded/ambiguous counts separately.

---

# 27. Performance and context limits

The analyst may process many trades.

Avoid loading unnecessary full history or raw candle data into LLM context.

Use:

- SQL/date filters;
- aggregation in Python;
- bounded detail;
- artifact files for large exports.

For a broad review, send Hermes:

1. summary;
2. grouped metrics;
3. strongest comparisons;
4. concise per-trade evidence for relevant outliers.

Do not send thousands of candles unless explicitly necessary.

---

# 28. Future-ready design

V1 must make it easy to add later:

```text
run_strategy_experiment
compare_strategy_versions
historical replay
hypothesis registry
human-reviewed trade annotations
MCP wrappers
dashboard analytics page
automatic weekly research reports
```

But do not implement unnecessary scope now.

Build clean interfaces so these can be added later.

---

# 29. Suggested module boundaries

Adapt to repository conventions, but keep conceptual separation similar to:

```text
trading_research/
├── __init__.py
├── cli.py
├── config.py
├── safety.py
├── models.py
├── journal_reader.py
├── candle_reader.py
├── metrics.py
├── context.py
├── compare.py
├── reports.py
├── artifacts.py
└── strategy_library.py
```

Tests:

```text
tests/trading_research/
```

Do not force this exact layout if the repository already has a strong structure.

---

# 30. Example final Hermes analysis

The system should enable Hermes to produce something like:

```text
Trade Review
2026-10-01 through 2026-10-05

FACT

Closed analyzable trades: 19
Winners: 8
Losers: 11
Net P&L: -$126.44
Average R: -0.08R
Profit Factor: 0.91

PB1
8 trades
5 winners
3 losers
+2.30R total

PB2
7 trades
2 winners
5 losers
-2.80R total

OBSERVATION

4 of 5 PB2 losers were entered before 09:50 ET.

PB2 before 09:50:
5 trades
1 winner
4 losers
Average R: -0.61R

PB2 at/after 09:50:
2 trades
1 winner
1 loser
Average R: +0.14R

The sample is too small for a strong conclusion.

HYPOTHESIS

PB2 LONG entries may be less reliable during the first 20 minutes of the regular session.

CONFIDENCE

LOW

VALIDATION REQUIRED

Compare current PB2 against a candidate 09:50 eligibility filter using a substantially larger historical sample.

NO PRODUCTION CHANGE RECOMMENDED YET.
```

---

# 31. Documentation

Add concise documentation covering:

```text
what the Trading Research Analyst does
what it absolutely cannot do
workspace location
CLI usage
strategy library format
adding a future strategy
interpreting the JSON
data-quality behavior
safety guarantees
```

Also document any metadata-only schema extension.

---

# 32. Acceptance criteria

The task is complete only when all of the following are true:

1. Hermes has a dedicated trading-research skill.
2. The skill can analyze closed trades by date range.
3. The skill can analyze one selected strategy.
4. PB1/PB2/LB1/LB2 strategy definitions are discoverable through a strategy registry/library.
5. Future strategies can be added without changing core analyst logic.
6. Trading data is accessed read-only.
7. No runtime writes can occur inside the production repository.
8. All generated files go to the dedicated research workspace.
9. No order-placement/cancel/modify code path is reachable from the research flow.
10. Deterministic metrics are calculated in code, not mentally by the LLM.
11. MFE/MAE are calculated when candle data is available.
12. Winner/loser comparisons work.
13. Strategy-level metrics work.
14. Ambiguous/missing data is surfaced, never fabricated.
15. Hermes separates FACT / OBSERVATION / HYPOTHESIS / VALIDATION REQUIRED.
16. Small-sample confidence rules are enforced in the skill.
17. Existing trading/scanner/execution tests continue to pass.
18. New safety and analytics tests pass.
19. No PB1/PB2/LB1/LB2 production behavior changes.
20. The implementation includes a short final report describing:
    - files added/changed;
    - how safety is enforced;
    - how Hermes invokes the analyst;
    - example command;
    - example Hermes request;
    - tests executed and results;
    - any remaining limitations.

---

# 33. Absolute rule

This is the most important requirement:

```text
THE TRADING RESEARCH ANALYST IS AN OBSERVER, NOT AN OPERATOR AND NOT A DEVELOPER.
```

At runtime it may:

```text
READ
CALCULATE
COMPARE
CLASSIFY
VISUALIZE
REPORT
PROPOSE HYPOTHESES
```

It may never:

```text
TRADE
EDIT PRODUCTION
CHANGE STRATEGIES
MODIFY CONFIG
WRITE PRODUCTION DATA
COMMIT CODE
APPLY ITS OWN RECOMMENDATIONS
```

If a requested action conflicts with this rule, the analyst must refuse that action and instead provide a research recommendation.

---

# 34. Begin now

Start by inspecting the existing repository and identifying the Orders Journal data path, candle path, strategy implementation locations, and Hermes skill conventions.

Then implement V1 end-to-end.

Prefer reuse over duplication.

Prefer simple, deterministic, testable code over LLM magic.

Preserve all existing production trading behavior.
