# Trading Strategies Project Memory

> **This file** = WHAT to do: rules, API surface, available indicators, pitfalls, coding standards.
> **BACKTESTING.md** = HOW things work under the hood: formulas, fill mechanics, edge cases, TV quirks.

## Key Rules
- **Always read MEMORY.md first** before starting work on any strategy.
- **Python setup**: All engine commands assume `python3` works and resolves to a supported Python version. If `python3` doesn't resolve or returns a version below the minimum, see Requirements → First-time setup help (bottom of this file) before debugging anything else.
- **Never suggest strategy code without backtest KPIs** — always run the backtest first. If given PineScript code, convert it to Python and run it through the Backtest Engine to get KPIs before presenting the strategy.
- **NEVER brute-force optimize parameters.** Grid search, exhaustive sweeps, or iterating on params to improve backtest KPIs is overfitting. **Scope**: this applies when **building a new strategy from scratch**. When **converting user-provided Pine**, use whatever parameters the Pine specifies — don't substitute "canonical" values, you'll break TV-matching. Instead, when building from scratch:
  - Use **canonical defaults**: EMA 9/21, EMA 12/26, EMA 50/200, RSI 14, ATR 14, MACD 12/26/9, Bollinger 20/2, Donchian 20, Ichimoku 9/26/52.
  - Use the **indicator's published defaults** (from its Pine source or original paper).
  - Pick **one** sensible param set per build attempt. If the user wants comparison, run **at most 2–3 explicitly named variants** (e.g. "EMA 9/21 vs 12/26 vs 50/200") — report all three honestly. **Let the user pick the winner based on their stated criteria** (return target, drawdown tolerance, trade frequency, etc.); don't silently pre-select based on raw KPI maxima.
  - **Validate the chosen params via cross-market** — if the strategy works on BTC with EMA 9/21 but breaks on ETH/SOL with the same params, it's overfit, not robust.
  - If the user asks for "optimal" or "best" params, refuse the optimization framing. Counter-propose: "I'll try 2–3 sensible variants and we'll see which holds up across markets."
- **Warm up moving averages**: Fetch chart data going at least 2x as far back as the longest MA period before the strategy start date.
- **PineScript version**: Always use V6 or later when analysing or writing TradingView Pine Script code.
- **File naming**: When outputting a strategy filename, increment the number by one (or add it if missing). This number must also be reflected in the strategy title.
- **PineScript for the user-chosen version**: After the user picks a variant from a comparison (based on their stated criteria — return target, drawdown tolerance, trade frequency, etc.), automatically create the PineScript V6 file for that version. No need for them to ask. (Don't auto-pick a "winner" by raw KPIs — see "NEVER brute-force optimize parameters" above.)
- **Data source**: Use TV CSV exports from `data/` when available (`load_tv_export()`). If the required data file is missing, **do NOT silently fetch it** — tell the user the file is missing and ask permission. **Preferred fetcher: `fetch_tv()`** (direct TradingView fetch via `tradingview-datafeed`) — produces OHLC identical to a manual TV CSV export, so trade-by-trade matching is preserved. State the symbol you'll fetch (e.g. "I'll fetch `INDEX:BTCUSD` 1D from TradingView"). Fall back to `fetch_crypto()` (ccxt, crypto only) only if `fetch_tv()` fails. Manual TV CSV export remains the gold-standard option — explain the flow to the user: on the TV chart, click the **Export chart data…** button (small download icon in the bottom-right of the chart pane), save the CSV, and place it in the `data/` directory.
- **Slippage: NOT SIMULATED** — we cannot simulate slippage because it requires super-granular tick/order-book data which is expensive to obtain and analyse. Always set slippage to 0 in the engine and mention this in every backtest result.
- **Backtest results must always show**: (1) which chart data was used and where it came from, (2) all strategy settings so the user can verify they match TV, (3) the slippage note (set to 0, not simulated), (4) First Order and Last Order dates (for verifying the trading range matches TV).
- **KPI display rule — always show both profit lines and use percentages**:
  1. **Total P&L (incl. open)** — first line, matches TV's Overview "Total P&L" = net_profit + open_profit. This is what the user sees first in TV.
  2. **Net Profit (closed)** — second line, matches TV's Excel "Net Profit" = sum of closed trade PnLs only.
  3. **Always show % alongside $** for Net Profit and Max Drawdown — users compare across different account sizes, so absolute $ alone is not useful.
  4. Show Max Drawdown as both $ and %.
- **Start-date safeguard**: The user's TV CSV export may not go back to the beginning of the chart. The engine auto-adjusts `start_date` to the first available bar and prints a warning. To match KPIs, the PineScript strategy **and** the Python backtest must use the same start date. Always code PineScript strategies with a configurable start date input. When the engine adjusts the date, tell the user to set the same date in TV's strategy properties (Date Range → Start Date).

## Strategy Build Process (MANDATORY before writing strategy code)

**When this applies**: building a new strategy from a brief like "build me a swing strategy for BTC."
**When this does NOT apply**: converting user-provided Pine to Python — the Pine already encodes most of the design parameters; jump straight to Pine Sanitization.
**Hybrid case** (e.g. "build something similar to test_ema_cross but add an RSI filter"): treat as **building new** — run the full checklist for the new design choices. Only the parameters explicitly inherited from the template are fixed.

When a user requests a new strategy, run this checklist BEFORE writing any Python or Pine. Skipping = imposing your preferences silently.

### Step 1 — Confirm or default the design parameters

| # | Parameter | Default | Why / when to ask |
|---|---|---|---|
| 1 | Trading style → Timeframe | Swing=`1D`, Day=`1h`/`4h`, Position=`1W`, Scalp=`≤15m` | Confirm if user's style is ambiguous |
| 2 | Asset + validation set | User asset + **all available same-class peers** in `data/`. **Always `ls data/` first** — don't memorize a list. The folder grows/changes over time. Filter for liquidity + ≥2 years history. Use `≥2` peers as the absolute fallback if most aren't available. | Confirm peers if user has preferences |
| 3 | Direction | Long-only | Ask if bear-period coverage matters |
| 4 | Position sizing — **style-dependent** | **Trend-following**: `qty_type="percent_of_equity", qty_value=100.0` (acceptable — disclose). **Swing**: `percent_of_equity` at `25–50%` per trade. **Day/scalp**: `percent_of_equity` at `25–50%` per trade. Engine supports `percent_of_equity`, `cash`, `fixed` — these are TV's modes too. | **Never offer "risk N% per trade with stop loss"** — without tick data we can't guarantee fills at the stop price; actual loss depends on next bar's close. If the user asks for risk-based sizing, explain this and steer them to `percent_of_equity` instead. Calibrate pushback on 100% sizing to the strategy class: trend-followers OK at 100%; swing/day must confirm before 100%. |
| 5 | Stop-loss / Take-profit | Signal-based or next-bar-open exits. **Avoid intrabar TP/SL on intraday TFs** (Pine Sanitization rule #11) until Bar Magnifier is implemented. | Daily+ TFs can use fixed TP/SL safely (still disclose intrabar fill ambiguity). |
| 6 | Trade frequency target | Match to TF — rough eyeball guidelines, not hard rules: 1D ≈ 10–30/yr, 4h ≈ 50–150/yr, scalp ≥ 500/yr | Drives strategy class — confirm if undecided. Mean-reverting strategies on 1D may exceed 30/yr; trend-followers on 1D may stay under 10/yr. |
| 7 | Validation method | **Cross-market** (**all available same-class peers**, ≥2 minimum) primary. **IS/OOS** supplementary when no peers exist OR user wants extra rigor OR for regime check (split build asset into bull/bear/sideways sub-periods, report KPIs per regime). | Always run cross-market validation — manually per-asset until the engine runner exists; mandatory either way. Report per-asset KPI table with spread (best/median/worst); don't average across assets — a strategy that crushes BTC and loses on 8 others can look profitable on average. IS/OOS one-shot only — if OOS fails, the strategy is dead. Iterate freely on build (in-sample), never on OOS. **If validation peer CSVs aren't in `data/`, follow the Data Source rule (ask user permission before fetching; name exchange and pair, e.g. "I'll fetch ETH/USDT 1D from Binance"). If no liquid peers exist for the asset class (e.g. obscure FX pair, single futures contract, single equity), fall back to IS/OOS. If the user explicitly opts out (e.g. "just test on BTC for now"), respect the override but state clearly in the result: "Cross-market validation skipped per user request — strategy is not yet stress-tested across markets." Do not silently drop the validation step.** |
| 8 | Date range / capital / commission / slippage / margin | `2018-01-01` / `$1,000` / `0.1%` / `0` / `0%` long & short | Sanitization rules cover these — no need to ask |

### Step 2 — Disclose all assumptions BEFORE coding

Open with a one-paragraph summary stating every assumption made. Format example:

> "I'm building this as: **swing / 1D / INDEX:BTCUSD / long-only / 50% of equity per trade (swing default — trend-followers can use 100%; want a different size or `cash`/`fixed` mode?) / signal-based exits / 2018-01-01 → present / 0.1% commission / cross-market validation on all available same-class peers in `data/` (I'll `ls data/` to find current peers — fetch any missing ones with your permission) after BTC build.** Override any?"

Calibrate the pushback on position sizing to the strategy class (Step 1 row 4). Don't default to 100% silently for swing/day strategies. **Never offer "1% risk per trade with a stop loss"** — see row 4: we cannot guarantee fills at the stop without tick data, so the loss isn't actually capped. Steer the user to `percent_of_equity` sizing if they ask for risk-based.

### Step 3 — Build, backtest, validate

1. Use **canonical or published default parameters** — never grid-search, never iterate to improve KPIs (see Key Rules → "NEVER brute-force optimize parameters").
2. Backtest on build asset; show all required outputs (per Key Rules → KPI display rule).
3. Cross-market validate on **all available same-class peers** (≥2 minimum fallback) BEFORE declaring "good." Report a per-asset KPI table with spread (best / median / worst); don't average. If KPIs collapse on most validation assets → strategy is overfit. **Do not tweak to "fix" it** — propose a different approach.
4. If using IS/OOS: one-shot only. If OOS fails, the strategy is dead. Iterate freely on build (in-sample), never on OOS.

(Pine generation for the user-chosen variant happens AFTER this step — see Key Rules → "PineScript for the user-chosen version". Any Pine you write must follow Pine Sanitization rules.)

### Anti-patterns to avoid

- ❌ Silent assumptions ("Here's an EMA strategy.")
- ❌ Brute-force / grid-search parameter optimization
- ❌ Iterating params after seeing the backtest until KPIs improve
- ❌ Iterating after seeing OOS results (= contamination)
- ❌ Declaring strategy "good" without cross-market or regime validation
- ❌ Defaulting to 100% sizing silently on swing/day strategies
- ❌ Offering "risk N% per trade with stop" sizing — we can't deliver this honestly without tick data; actual loss depends on next bar's close, not the stop level
- ✅ Disclose → confirm → build with sensible defaults → validate → honest result

## Pine Script Sanitization (applied to EVERY .pine before backtesting)

**MANDATORY**: Before converting any PineScript strategy to Python, check and fix ALL of these in `strategy()`. WARN the user about every change. If the Pine code violates any of them, **stop and fix it first**:

1. The Pine code must be modified to comply before conversion can proceed.
2. The user must apply the same changes in TradingView and re-export XLSX — otherwise TV numbers will differ.
3. Return the ENTIRE sanitized .pine to the user (not just changed lines).
4. Save the sanitized `.pine` in `strategies/`. The `.pine` and `.py` MUST share the same filename.

| # | Setting | Required Value | Why |
|---|---|---|---|
| 1 | **Date range** | Add `start_date="2018-01-01"` + `timeCondition` gate if missing. **If `start_date` is earlier than 2018-01-01, raise it to `2018-01-01`** so Pine matches the Python trading-start floor (Strategy Template uses `max(pine_start, "2018-01-01")`). Without this, TV trades from the earlier date but Python clamps to 2018-01-01 — trade-by-trade matching fails silently. | Exchange data starts ~2017; need 1yr warmup for indicators. **This is the #1 most common mistake.** Aligning Pine and Python dates here keeps TV-matching intact. |
| 2 | **Commission** | `commission_value=0.1, commission_type=strategy.commission.percent` if `0` or missing | Zero commission is unrealistic and gives misleading results |
| 3 | **Slippage** | `slippage = 0` | Engine cannot simulate slippage — requires tick/order-book data we don't have |
| 4 | **Margin Long / Margin Short** | `margin_long = 0, margin_short = 0` | TV's default 100% margin creates spurious margin-call mini-trades. TV has known bugs with non-zero margin. |
| 5 | **Bar Magnifier** | `use_bar_magnifier = false` | Engine uses TV's heuristic for intrabar TP/SL fill order. Bar Magnifier uses lower-TF tick data we don't have. |
| 6 | **Recalculate after order is filled** | `calc_on_order_fills = false` | **Forward-looking bias**: TV's own docs warn this causes forward-looking bias — must NEVER be used. |
| 7 | **On every tick** | `calc_on_every_tick = false` | Engine computes signals on bar close only. |
| 8 | **Initial capital** | Add `initial_capital=1000` if missing | Pine defaults to $1M. Doesn't affect percentages but makes dollar amounts mismatch if not set. Always set explicitly. |
| 9 | **XLSX data coverage** *(only when user provides XLSX for matching validation)* | XLSX trade range ⊆ CSV date range | If the user provides an XLSX for trade-by-trade validation and it shows trades outside the CSV range, numbers will differ. Ask user to provide matching data or narrow the date range. (XLSX is optional — skip this rule if no XLSX is provided.) |
| 10 | **`request.security()` lookahead** | `lookahead=barmerge.lookahead_off` | **Forward-looking bias**: `lookahead_on` lets daily bars see a higher-TF bar's final value before it closes. If Pine is v1 or v2 and `lookahead` is not specified, add it explicitly (default was `lookahead_on` before v3). Same rule for `request.security_lower_tf()`. |
| 11 | **Intrabar TP/SL on intraday TFs (≤4h)** | If user-provided Pine has intrabar TP/SL on intraday TFs, **WARN the user and proceed**. The engine matches TV's heuristic exactly (backtest validation still works), but both share the heuristic blind spot — **live trading sees real ticks, so live results will diverge from both engine and TV**. **Do NOT strip TP/SL from user's Pine** — that would change the strategy. When **building a new strategy from scratch**, prefer signal-based or next-bar-open exits on intraday TFs until Bar Magnifier is implemented. On daily+ TFs, fixed TP/SL is acceptable (still disclose live divergence). | Engine uses TV's heuristic for same-bar TP+SL conflicts. On intraday TFs, bars routinely span both levels — heuristic is unreliable. Daily bars rarely span both → low risk. |
| 12 | **Pine v6 lazy evaluation vs stateful `ta.*` calls** | Hoist EVERY stateful `ta.*` call (`ta.atr`, `ta.crossover`, `ta.crossunder`, `ta.barssince`, `ta.valuewhen`, …) to a global-scope variable; use only the variable inside conditions. | Pine v6 short-circuits `and`/`or`, ternaries, and `if` bodies. A stateful `ta.*` call buried in a condition chain (e.g. `A and B and math.abs(open-close) > ta.atr(15)`) stops executing on every bar, corrupting its rolling state and **silently killing the signal path**. Pine v5 evaluated all operands every bar — and the Python engine computes all indicators on all bars (v5 semantics) — so an unhoisted v6 Pine will NOT match either. **Both directions**: when writing v6 Pine, hoist; when converting a user's v6 Pine that has buried `ta.*` calls, the TV behavior itself is the corrupted one — sanitize the Pine (hoist), return it to the user for re-export, and only then convert. |

**Quick self-check before proceeding:**
> Does the Pine have a date range? Is commission set to 0.1%? Any `request.security()` with `lookahead_on`? Any intrabar TP/SL on intraday TFs (rule #11)? Any stateful `ta.*` call inside an `and`/`or` chain, ternary, or `if` body in v6 Pine (rule #12)? If any fail, STOP and fix first.

**How to communicate this to the user:**

> "Before I convert this strategy, I need to sanitize the Pine script. I found these issues: [list violations]. I'll fix the Pine code and give you the full sanitized version — you'll need to paste it into TradingView and re-export the XLSX so the numbers match. Should I proceed?"

## PineScript Coding Standards
- **Hoist stateful `ta.*` calls (v6 lazy evaluation)**: never call `ta.atr()`, `ta.crossover()`, `ta.crossunder()`, or any other stateful `ta.*` function inside an `and`/`or` chain, a ternary branch, or an `if` body. Pine v6 evaluates these lazily, so the call stops running every bar and its rolling state silently corrupts. Compute each `ta.*` value once at global scope, store it in a variable, and reference the variable in conditions. (See Pine Script Sanitization rule #12.)
- **`active` parameter**: Always use Pine Script V6's `active` parameter on checkboxes and pulldowns with a "No" option, so all related controls disable in tandem with the main settings input control.
- **Tooltips**: Always add comprehensive tooltips to ALL commands and fields in the Settings Inputs tab. Every `input.*()` call must have a `tooltip=` argument explaining what the setting does.

## Backtesting Engine
- Structure:
  - `engine/` — business logic: `engine.py` (core + indicators), `data.py` (data loaders), `__init__.py` (re-exports)
  - `data/` — chart data: CSV files + `cache/` for fetched data
  - `strategies/` — strategy scripts (e.g. `example_ema_cross.py` as reference)
  - `indicators/` — **reference Pine indicator library**. Pure indicators (no strategy logic) saved as `.pine` for inspiration / building blocks. When asked to design or extend a strategy, scan this folder first to see if a relevant indicator is already on hand.
- **Default data path**: TV CSV exports in `data/` (e.g. `INDEX_BTCUSD, 1D.csv`) — exact same OHLC as TradingView. Other fetchers available — see Data Loading section for the full priority list.
- See [BACKTESTING.md](BACKTESTING.md) for TV matching internals (formulas, fill mechanics, edge cases)
- **Last bar is always dropped** (unfinished candle) in all data loaders
- All imports come from one place: `from engine import load_tv_export, BacktestConfig, ...`

## Strategy Template
Full copy-paste template for a new strategy. Hard floor of `2018-01-01` for trading start (engine auto-relaxes to the first available bar if the asset's history is shorter).

**XLSX is optional** — only needed when you want trade-by-trade matching validation against a TV export. The template handles both modes automatically:

- **No XLSX (default)**: uses hardcoded `start_date="2018-01-01"`, `end_date="2069-12-31"`. Engine runs on all available data. This is the normal mode for users who just want to backtest a strategy.
- **With XLSX present**: switches to `read_tv_xlsx_dates()` which caps the backtest window to what TV observed (keeps validation stable across CSV refreshes — when more bars are added to the CSV later, the backtest still produces the same trades because the window is capped at the XLSX's snapshot). Use this when comparing trades to a TV XLSX export.

Transitions are automatic — drop an XLSX in next to the script to switch modes; no code change.

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import (
    load_tv_export, read_tv_xlsx_dates,
    BacktestConfig, run_backtest, print_kpis,
    calc_ema, detect_crossover, detect_crossunder,
)

# Trading-start floor: never trade earlier than this even if data exists.
# (Engine's start-date safeguard auto-relaxes to first bar if asset history
# starts later — e.g. recently-listed coin like SUI from 2023.)
TRADE_START_FLOOR = "2018-01-01"

def my_strategy_signals(df, fast=9, slow=21):
    df = df.copy()
    df["fast_ema"] = calc_ema(df["Close"], fast)
    df["slow_ema"] = calc_ema(df["Close"], slow)
    df["long_entry"] = detect_crossover(df["fast_ema"], df["slow_ema"])
    df["long_exit"] = detect_crossunder(df["fast_ema"], df["slow_ema"])
    return df

def main():
    df = load_tv_export("INDEX_BTCUSD, 1D.csv")
    df = my_strategy_signals(df)

    # Auto-detect: if XLSX exists (post-validation phase), cap backtest
    # window to what TV observed. Otherwise fall back to first-build mode.
    xlsx_path = Path(__file__).resolve().parent / "my_strategy.xlsx"
    if xlsx_path.exists():
        dates = read_tv_xlsx_dates(xlsx_path)
        start_date = max(dates["pine_start"], TRADE_START_FLOOR)  # ISO strings sort chronologically
        end_date = dates["range_end"]
    else:
        # First-build mode: TV XLSX doesn't exist yet
        start_date = TRADE_START_FLOOR
        end_date = "2069-12-31"

    config = BacktestConfig(
        initial_capital=1000.0,
        commission_pct=0.1,
        slippage_ticks=0,
        qty_type="percent_of_equity",
        qty_value=100.0,
        start_date=start_date,
        end_date=end_date,
    )

    kpis = run_backtest(df, config)
    print_kpis(kpis)

if __name__ == "__main__":
    main()
```

## Signal Generator Pitfall — timeCondition
When a strategy's signal generator tracks internal state (position, cooldown timer, highest-since-entry, etc.), it **must** include a time-range check matching Pine's `timeCondition`. Without it, if entry conditions happen to be True on bars **before** `start_date`, the generator enters a phantom position that the engine never fills (engine only acts within the trading range). This desyncs the generator's internal state from the engine and causes missing/wrong trades.

**Rule**: Any signal generator that uses internal position tracking must accept `start_date`/`end_date` and gate **both entry AND exit conditions** with `bar_in_range`:
```python
def generate_signals(df, start_date="2018-01-01", end_date="2069-12-31"):
    ts_start = pd.Timestamp(start_date)
    ts_end = pd.Timestamp(end_date)
    ...
    for i in range(n):
        bar_in_range = ts_start <= dates[i] <= ts_end
        close_long = (<exit conditions>) and bar_in_range  # matches Pine: closeLongCondition ... and timeCondition
        long_cond = (<entry conditions>) and bar_in_range  # matches Pine: longCondition ... and timeCondition
```

Indicators and non-trading state (chop detection thresholds, etc.) compute on ALL bars — only trading actions are gated. This matches PineScript where indicators always compute but `timeCondition` gates strategy.entry()/close_all().

**Why exits need gating too**: Exit conditions gate VWAP resets (VWAP anchors to last close signal) and affect state like `in_early_buy_signal` which modifies future close signals. Without exit gating, VWAP accumulates differently than PineScript.

**Why entries need gating**: Without it, phantom trades on early bars set `had_trades = True` (Pine's `strategy.max_contracts_held_all > 0`), enabling buyback/re-entry signals that should not be available yet.

Signal generators that don't track state (e.g. pure crossover signals without cooldown/trailing stop) are unaffected — the engine handles position tracking.

**`prevWasExitL` re-entry suppression**: When Pine code uses `prevWasExitL = (strategy.position_size[1] > 0) and (strategy.position_size == 0)` to suppress immediate re-entry after exits, the stateful signal generator must update this flag after BOTH signal exits (step 1: fills at Open) AND stop-loss exits (step 2: intrabar fills). A common bug is only checking after signal fills, missing the SL case — this allows re-entry on the bar immediately after a stop-loss exit when it should be suppressed.

## Engine Architecture
- `engine/engine.py` is **strategy-agnostic** — accepts any DataFrame with signal columns
- **Long-only (`run_backtest`):** requires `Open`, `High`, `Low`, `Close`, `long_entry`, `long_exit`
- **Long+Short (`run_backtest_long_short`):** also requires `short_entry`, `short_exit`
- **`Trade` dataclass** includes `direction` field (`"long"` or `"short"`) — set automatically by the engine
- **`print_trades()`** auto-shows a `Dir` column (LONG/SHORT) when short trades are present; long-only output stays clean
- Strategy signals are generated by separate functions in `strategies/` (e.g. `ema_cross_signals()`)
- To add a new strategy: create a file in `strategies/`, write a `*_signals(df)` function, then pass to `run_backtest(df, config)` or `run_backtest_long_short(df, config)`

## Required DataFrame Columns

**`run_backtest()` (long-only):**

| Column | Type | Required | Description |
|---|---|---|---|
| `Open` | float | Yes | Bar open price |
| `High` | float | Yes | Bar high price (used for intrabar drawdown + TP/SL) |
| `Low` | float | Yes | Bar low price (used for intrabar drawdown + TP/SL) |
| `Close` | float | Yes | Bar close price |
| `long_entry` | bool | Yes | True on bars where a long entry signal fires |
| `long_exit` | bool | Yes | True on bars where a long exit signal fires |
| `entry_qty` | float | No | Per-bar entry size in asset units (for pyramiding) |
| `tp_price` | float | No | Absolute take-profit price level |
| `sl_price` | float | No | Absolute stop-loss price level |
| `tp_offset` | float | No | TP distance from entry price (engine computes level) |
| `sl_offset` | float | No | SL distance from entry price (engine computes level) |

**`run_backtest_long_short()` — adds:**

| Column | Type | Required | Description |
|---|---|---|---|
| `short_entry` | bool | Yes | True on bars where a short entry signal fires |
| `short_exit` | bool | Yes | True on bars where a short exit signal fires |

## Available Indicators (in engine/engine.py)
- `calc_ema(series, length)` — EMA matching `ta.ema()`
- `calc_smma(series, length)` — Smoothed MA / RMA matching `ta.rma()`
- `calc_sma(series, length)` — Simple Moving Average matching `ta.sma()`
- `calc_rsi(series, length)` — RSI matching `ta.rsi()` — uses `ta.rma()` (SMMA/Wilder's) for gain/loss averaging
- `calc_atr(df, length)` — Average True Range matching `ta.atr()` = `ta.rma(ta.tr(), length)`
- `calc_macd(series, fast, slow, signal)` — MACD matching `ta.macd()`, returns `(macd_line, signal_line, histogram)`
- `calc_wma(series, length)` — Weighted MA matching `ta.wma()`
- `calc_hma(series, length)` — Hull Moving Average
- `calc_ehma(series, length)` — Exponential Hull MA
- `calc_thma(series, length)` — Triple Hull MA
- `calc_gaussian(series, length, poles)` — Gaussian filter (cascaded EMAs, 1–4 poles)
- `calc_highest(series, length)` — Highest value over N bars matching `ta.highest()`
- `calc_lowest(series, length)` — Lowest value over N bars matching `ta.lowest()`
- `calc_donchian(high, low, length)` — Donchian channel, returns `(upper, lower, mid)`
- `calc_obv(close, volume)` — On-Balance Volume matching `ta.obv`
- `calc_ichimoku(high, low, conv, base, span_b, disp)` — Ichimoku Cloud, returns dict with `conversion`, `base`, `lead_a`, `lead_b`, `displaced_lead_a`, `displaced_lead_b`
- `detect_crossover(fast, slow)` / `detect_crossunder(fast, slow)` — signal detection
- `get_source(df, source)` — price source selector (close/open/high/low/hl2/hlc3/ohlc4)
- All indicators handle NaN-leading input (safe to chain/cascade)

## Missing Indicators (manual implementation needed)
These PineScript built-ins are NOT in the engine indicator library. When converting strategies that use them, implement manually:

| Pine function | Python implementation |
|---|---|
| `ta.vwma(src, len)` | `(src * volume).rolling(len).sum() / volume.rolling(len).sum()` |
| `ta.barssince(cond)` | Iterative counter; returns `na` (use large sentinel like 99999) when condition was never true. `na >= 0` is `false` in Pine. |
| Supertrend | Stateful: adaptive upper/lower bands with ratcheting (`fuL`/`flL`) + direction tracking. Must be iterative. |
| Zero-Lag EMA | `calc_ema(close + (close - close[lag]), length)` where `lag = floor((length-1)/2)` |

## Pine Indicator Reference Library (`indicators/`)
A folder of pure-indicator Pine files (no strategy logic) kept as building blocks and inspiration. **Ships with the engine.** The folder grows over time — new `.pine` files get dropped in.

Naming convention: `Indicator - <Name> - <Timeframe>.pine` (e.g. `Indicator - Bollinger Bands - 1D.pine`).

**Workflow** — when designing/extending a strategy, or when the user asks for a filter/confluence/signal:
1. **Always `ls indicators/` first** to see what's currently on hand. Don't rely on a memorised list — the folder changes.
2. Read the relevant `.pine` to extract the indicator math.
3. If a wrapper exists in `engine/engine.py` → use it. Otherwise implement in Python following normal Pine→Python rules (rounding, NaN warmup, MTF gating).
4. The timeframe in the filename is the author's intent, not a hard constraint — reuse on other TFs at your discretion (MTF indicators may need re-tuning).

## TradingView Settings for Matching
To get identical results between this engine and TradingView, set these in your TV strategy properties:

| Setting | Value |
|---|---|
| Margin Long | **0%** |
| Margin Short | **0%** |
| Slippage | **0** |
| Commission | Match your `commission_pct` (e.g. 0.1%) |
| `calc_on_every_tick` | `false` |
| `calc_on_order_fills` | `false` |
| `fill_orders_on_standard_ohlc` | `true` |
| `use_bar_magnifier` | `false` |
| `process_orders_on_close` | Match your `BacktestConfig.process_orders_on_close` (default: `false`) |

Setting margin to 100% (TV default) causes spurious margin-call mini-trades that inflate trade count.

## TradingView Matching Behavior
- EMA: standard formula, multiplier=2/(len+1), seed with SMA of first `len` bars
- Signals: crossover/crossunder detected on bar close
- Order fill: **next bar open** (calc_on_every_tick=false, fill_orders_on_standard_ohlc=true). With `process_orders_on_close=True`: fill at **same bar Close** instead.
- **Sizing** — controlled by `BacktestConfig.qty_type` and `qty_value`:
  - `"percent_of_equity"` (default, qty_value=100.0): invest N% of equity. At 100%, commission-adjusted at fill time: `trade_value = equity / (1 + rate)`, `qty = trade_value / fill_price`. At <100%, qty computed at signal time using bar Close.
  - `"cash"` (e.g. qty_value=500): invest a fixed dollar amount. `qty = cash_value / close` computed at signal time, fills at next bar's Open. Commission is charged on top (not deducted from the cash amount).
  - `"fixed"` (e.g. qty_value=0.1): invest a fixed number of asset units per entry. Matches PineScript's `strategy.fixed` / `default_qty_type=strategy.fixed`. `qty = qty_value` regardless of equity or price. Commission is charged on top.
- Commission: pct of trade value, applied on BOTH entry and exit
- **Margin: set to 0%** for long and short in TV to match engine (100% margin causes spurious margin-call mini-trades)
- **Slippage: set to 0** — slippage simulation is not possible without expensive tick-level data; always set to 0 in both the engine and TV for matching
- **Max Drawdown: intrabar methodology** matching TV's "Max equity drawdown (intrabar)":
  - Uses bar `Low` price for worst-case equity during open long positions (not just Close)
  - Uses bar `High` price for worst-case equity during open short positions
  - Peak equity only updates when **flat** (no open position) — unrealised mark-to-market highs do NOT update the peak
  - **Peak equity on reversal fills**: In flip/always-in-market strategies, `position_qty` is never 0 at end-of-bar because exit+entry fill on the same bar. Peak equity MUST update at the instant between exit fill and entry fill (v4.0 fix).
  - Max DD ($) and max DD (%) are tracked as **independent maximums** — they may occur at different trades
  - Ref: https://www.tradingview.com/support/solutions/43000681690
  - Required columns: `High` and `Low` (in addition to `Open`, `Close`)

## Short Selling Cash Model
- `run_backtest_long_short()` supports long+short positions (never simultaneously)
- Short PnL: `qty * (entry_price - exit_price) - entry_commission - exit_commission`
- Intrabar DD for shorts uses bar `High` (worst case for short = price spike up)
- **Critical:** At short exit, settle from `cash` (not mark-to-market `equity`). Using `equity` causes drift because mark-to-market is computed at a different price (prev bar close) than the fill price (current bar open). The correct formula: `cash = cash + gross_pnl - exit_commission`
- **Bug history:** Originally used `cash = equity + gross_pnl - exit_commission` which caused a $30K cumulative error over 63 trades on a $10K account due to compounding mark-to-market drift through each short position

## Reversal Logic (Long ↔ Short)
- TV's `strategy.entry("Short")` reverses: closes any open long AND opens a short on the same bar
- Engine supports this via reversal detection in `run_backtest_long_short()`:
  - If `pending_long_exit` AND `short_entry`, both queue → exit fills first, then entry fills at same bar's Open
  - If `pending_short_exit` AND `long_entry`, same treatment
- Strategy must set cross-signals: `short_entry` should also trigger `long_exit`, and `long_entry` should also trigger `short_exit`
- **Important**: When running both long-only and L+S backtests, use **separate DataFrame copies** — reversal signal modifications (e.g. `long_exit |= short_cross_under`) corrupt the long-only signals

## Net Profit & Open P&L
- `net_profit` = sum of **closed** trade PnLs (matches TV's "Net Profit")
- `open_profit` = unrealised P&L from any position still open at data end
- `final_equity` = `initial_capital + net_profit + open_profit`
- Open trades (exit_date=None) are included in the trades list but excluded from KPI stats

## Take Profit & Stop Loss
The engine supports TP/SL exits matching PineScript's `strategy.exit()` with `limit=` and `stop=`. Three ways to set levels (priority order — first match wins):

1. **Absolute price columns** (`tp_price` / `sl_price` in DataFrame) — per-bar absolute prices. Use when TP/SL levels are known ahead of time (e.g. fixed support/resistance).
2. **Offset columns** (`tp_offset` / `sl_offset` in DataFrame) — per-bar distance from entry price. The engine computes the absolute level at fill time: long TP = entry + offset, long SL = entry - offset (reversed for shorts). Use for ATR-based or any distance-based TP/SL.
3. **Config percentages** (`take_profit_pct` / `stop_loss_pct` in BacktestConfig) — fixed percentage from entry price. Simplest approach. Set to 0.0 to disable (default).

**TP/SL fill behavior** (matches TV without Bar Magnifier):
- Fills at **exact TP/SL price** on the bar where High/Low reaches it — not at next bar Open
- **Gap-through**: if bar Open already past TP/SL level → fills at Open
- **Both TP and SL hit on same bar**: TV heuristic — Open closer to favourable extreme → that side hit first
- **Entry bar skipped**: TP/SL is not checked on the entry bar itself (matches TV's order placement timing)
- **Signal vs TP/SL on same bar**: signal exits (at Open) take priority over TP/SL (intrabar)

**Timing for offset/price columns**: In PineScript, `strategy.exit()` runs at bar close and sets levels for the next bar. So offset columns should use **shifted** values: `df["tp_offset"] = (atr * mult).shift(1)`.

**Backward compatible**: When all TP/SL mechanisms are disabled (defaults), the engine behaves identically to signal-only mode.

### Trailing Stop via strategy.exit()

PineScript's `strategy.exit()` trailing stop has three parameters that must be used together:

| Parameter | Role | Required? |
|---|---|---|
| `trail_price` | Activation level (absolute price) | One of `trail_price` OR `trail_points` is required |
| `trail_points` | Activation level (profit in ticks from entry) | One of `trail_price` OR `trail_points` is required |
| `trail_offset` | Trailing distance in ticks from highest/lowest price | Always required |

**Critical**: `trail_offset` alone does NOT activate a trailing stop — it is silently ignored.
Use `trail_points=0` for immediate activation.

**Tick conversion**: trail distance in price = `trail_offset × syminfo.mintick`.
- INDEX:BTCUSD → mintick = 1.0
- Exchange pairs (BINANCE:BTCUSDT) → mintick = 0.01

**When converting PineScript to Python**:
- If Pine has `trail_offset` without `trail_points`/`trail_price` → no trailing stop is active, skip it
- If Pine has both → implement via stateful signal generator with `sl_price` column:
  ```python
  # Set for next bar (matches strategy.exit timing)
  sl_price_arr[i + 1] = highest_since_entry - trail_offset_price
  ```
  where `trail_offset_price = trail_offset_ticks × mintick`

**Native trailing stop implementation rules**:
1. **Exclude entry bar from tracking**: TV's trail order is placed at bar close and starts tracking from the NEXT bar. Do NOT include the entry bar's High/Low in `highest_since`/`lowest_since` — only update from `i > entry_bar_idx`:
   ```python
   if position == 1 and i > entry_bar_idx:
       highest_since = max(highest_since, highs[i])
   ```
2. **Monotonic ratchet**: The trail stop only moves in the favorable direction (up for longs, down for shorts). Even when ATR changes cause the raw trail level to move unfavorably, the trail level must not retreat:
   ```python
   raw_trail = highest_since - trail_off
   trail_stop = max(prev_trail, raw_trail)  # monotonic for longs
   prev_trail = trail_stop
   ```
3. **Combined stop + trail**: When Pine uses both `stop=` and `trail_points/trail_offset`, the effective SL = `max(static_stop, trail_stop)` for longs, `min(static_stop, trail_stop)` for shorts (whichever is tighter)
4. **Pine's `math.round()` vs Python's `round()`**: Pine uses standard rounding (half rounds up), Python uses banker's rounding (half rounds to even). Use `int(x + 0.5)` for positive values
5. **Stateful generator required**: Must simulate TP/SL exits internally to keep position state in sync with the engine, including entry_price, highest_since, and entry_bar_idx tracking

## Gaussian Channel (IIR Filter) — NOT `calc_gaussian`
Many TradingView strategies use the "Gaussian Channel" indicator (by DonovanWall and others). This uses a recursive N-pole IIR filter with binomial coefficients — it is **NOT** the same as `calc_gaussian()` in the engine (which is cascaded EMAs).

The IIR filter formula (`f_filt9x` in Pine):
```
f[i] = α^N × src[i] + Σ(k=1..N) (-1)^(k+1) × C(N,k) × (1-α)^k × f[i-k]
```
where C(N,k) are binomial coefficients.

**Critical pitfall**: The alpha/beta computation uses `1.414` (truncated √2), NOT `2`:
```python
# CORRECT — matches Pine's math.pow(1.414, 2/NS)
beta = (1 - cos(2*pi / period)) / (1.414 ** (2.0 / poles) - 1)
# WRONG — using 2 instead of 1.414 gives completely different channel
beta = (1 - cos(2*pi / period)) / (2 ** (2.0 / poles) - 1)
```
This single constant difference produces a completely different alpha, different filter output, and wrong trade signals.

Python implementation:
```python
from math import comb, cos, asin, sqrt
def gaussian_npole_iir(alpha, src, n_poles):
    """N-pole Gaussian IIR filter matching Pine's f_filt9x."""
    x = 1.0 - alpha
    n = len(src)
    f = np.zeros(n)
    for i in range(n):
        s = src[i] if not np.isnan(src[i]) else 0.0
        val = alpha ** n_poles * s
        for k in range(1, n_poles + 1):
            prev = f[i - k] if i >= k else 0.0
            val += (-1) ** (k + 1) * comb(n_poles, k) * x ** k * prev
        f[i] = val
    return f
```

## Process Orders on Close
When PineScript sets `process_orders_on_close = true`, orders fill at the **same bar's Close** instead of waiting for the next bar's Open. Set `BacktestConfig(process_orders_on_close=True)` to match.

**How it works:**
- Signal detected at bar Close → fills immediately at Close price (no pending queue)
- Exits fill before entries (same ordering as default mode)
- TP/SL from `strategy.exit(stop=)` still fills intrabar on subsequent bars at exact price
- `strategy.exit(stop=X)` set at bar Close: if Close breaches X → treated as signal exit at Close. Otherwise → pending for next bar's intrabar check.
- Entry bar = signal bar; TP/SL starts checking from next bar (`i > entry_bar_idx`)

**Signal generator considerations:**
- Entry price = `closes[i]` (not `opens[i+1]` as in default mode)
- ATR stop: check if Close already breaches stop → mark as signal exit. Otherwise set `sl_price_arr[i+1]` for intrabar check.
- Stateful generator still required for position tracking

**Backward compatible:** Default is `False` — existing strategies unchanged.

### Signal Generator Pre-fill Pitfall
With `process_orders_on_close`, Pine's `strategy.position_size` is **pre-fill** during script calc. Signal generators MUST use a snapshot of position state at bar start for ALL condition checks:
- `pos = position` at start of signal block (after TP/SL simulation)
- Use `pos` (not the live `position`) for entry/management condition checks
- Update `position` only at the END of the bar (deferred update)
- Add **reversal guards**: when a reversal entry fires, suppress the old position's management block (matches Pine v1.4 pattern)
- The engine automatically warns if it detects reversal + same-bar exit conflicts

Without this, management blocks fire on reversal bars (because position was updated immediately), creating spurious exit signals that desync the generator from the engine.

## Pyramiding & Variable Position Sizing
The engine supports pyramiding (multiple simultaneous entries) and per-bar entry quantities, matching PineScript's `pyramiding=N` and `strategy.entry(qty=...)`. Both `run_backtest()` (long-only) and `run_backtest_long_short()` support pyramiding > 1.

**BacktestConfig setting:**
- `pyramiding: int = 1` — maximum number of simultaneous open sub-positions. Set to 1 (default) for single-position strategies. Set higher for pyramiding strategies (e.g. `pyramiding=100` for ease-in patterns, `pyramiding=2` for L+S pyramid adds).

**Optional DataFrame column:**
- `entry_qty` (float) — per-bar entry size in asset units. When present, the engine uses this qty instead of computing from equity. Matches PineScript's `strategy.entry(qty=...)`. When absent, the engine sizes from equity as before.

**Behavior:**
- Each sub-position is tracked as a separate `Trade` with its own entry date, price, qty, and P&L
- `long_exit=True` triggers `strategy.close_all()` — closes every open sub-position at the next bar's Open
- Each closed sub-position becomes a separate trade in the trade list
- Signal detection uses `len(open_positions) < config.pyramiding` instead of `position_qty == 0`
- TP/SL uses the **first** sub-position's entry price for level calculation and closes all positions when triggered

**Backward compatible**: When `pyramiding=1` (default) and no `entry_qty` column, behavior is identical to single-position mode.

**Ease-in pattern example** (PineScript `strategy.fixed` with `pyramiding=100`):
- Strategy signal sets `long_entry=True` and `entry_qty = equity_to_invest / close` on each bar it wants to add
- Engine fills each entry at next bar's Open with the specified qty
- On close signal, all sub-positions are closed at once

## Data Loading

**Fetcher priority order (preferred → fallback)**:
1. `fetch_tv()` — direct TradingView fetch (best TV-matching, any class)
2. `load_tv_export()` — manual TV CSV export from `data/` (gold standard)
3. `fetch_crypto()` — ccxt fallback for crypto (different exchange data, may differ from TV INDEX)
4. `fetch_btc_daily()` — Bitstamp legacy fallback (BTC/USD daily only)

When the user is missing data: prefer `fetch_tv()` per the **Data Source rule** in Key Rules — name the symbol, ask permission, fetch.

- `fetch_tv(symbol, timeframe="1D", n_bars=5000, use_cache=True, username=None, password=None)` — **direct TradingView fetcher** via the `tradingview-datafeed` library:
  - **Symbol format**: requires `EXCHANGE:SYMBOL` — e.g. `"INDEX:BTCUSD"`, `"BINANCE:BTCUSDT"`, `"AMEX:SPY"`, `"NASDAQ:NVDA"`, `"OANDA:XAUUSD"`. Colon required.
  - **Timeframes**: TV notation (`"1D"`, `"1W"`, `"240"`, `"60"`, `"15"`) or ccxt notation (`"1d"`, `"4h"`, `"15m"`).
  - **Coverage**: every symbol TV supports — stocks, ETFs, indices, FX, commodities, futures, crypto across all exchanges. Includes TV's synthesized `INDEX:*` symbols (e.g. `INDEX:BTCUSD` matches the TV chart most of our CSVs use).
  - **TV-matching verified**: `fetch_tv('INDEX:BTCUSD', '1D')` produces OHLC identical (1948/1948 values, 0.0000% diff) to the manually exported `INDEX_BTCUSD, 1D.csv`. Trade-by-trade backtest validation works as if the user did a manual CSV export.
  - **Caching**: saves to `data/cache/` as `tv_<EXCHANGE>_<SYMBOL>_<TF>_<NBARS>.csv`. Pass `use_cache=False` to refetch.
  - **Last bar dropped** (unfinished candle), same as other loaders.
  - **Authentication**: anonymous mode works for most major symbols. Pass `username`/`password` for niche or premium-only symbols if needed.
  - **Limit**: 5000 bars per request (library cap). For daily, 5000 bars ≈ 13.7 years; ample. For 1m intraday, only ~3.5 days — multi-page logic would be needed for long histories at low TFs (not implemented yet).
  - **Risk**: unofficial library — no SLA. Could break if TV changes its WebSocket endpoints. Keep `fetch_crypto`/`load_tv_export` as fallbacks.
  - **Requires**: `pip install tradingview-datafeed tzlocal` (in `requirements.txt`).
- `load_tv_export()` preserves `OnBalanceVolume` column if present in the TV CSV export — useful for strategies that use `ta.obv`
- `load_tv_export()` only drops rows where OHLC data is NaN — auxiliary columns like OBV with NaN on the first bar do NOT cause bar removal
- `fetch_crypto(symbol, timeframe, start, end)` — multi-exchange crypto fetcher via `ccxt`:
  - **Symbol formats**: `"BTC"`, `"BTCUSDT"`, `"BTC/USDT"`, `"BINANCE:SOLUSDT"` — auto-resolves quote currency (USDT→USD→BUSD→USDC)
  - **Timeframes**: TV notation (`"240"`, `"1D"`, `"W"`) or ccxt notation (`"4h"`, `"1d"`, `"1w"`)
  - **Exchange fallback chain**: binance → coinbase → kraken → bybit → kucoin → okx → gate → bitget → mexc. Uses first exchange returning ≥50 candles.
  - **Ticker discovery**: CoinGecko `/api/v3/coins/list` for error messages when symbol not found
  - **Caching**: Saves to `data/cache/` as CSV; subsequent calls load from cache. Pass `use_cache=False` to force refetch.
  - **Returns**: Same DataFrame format as other loaders (Open, High, Low, Close, Volume; DatetimeIndex)
  - **Last bar dropped** (unfinished candle), same as other loaders
  - **Note**: Exchange prices may differ slightly from TradingView INDEX prices (e.g. INDEX:BTCUSD). For exact TV matching, use `fetch_tv()` (preferred) or `load_tv_export()` with a manual TV CSV export.
- `fetch_btc_daily()` fetches from Bitstamp API as fallback (BTC/USD daily only)
- `read_tv_xlsx_dates(xlsx_path)` — reads dates from a TV XLSX export's Properties sheet. Returns dict:
  - `pine_start`, `pine_end`: Pine's Start Date / End Date input parameters (timeCondition dates)
  - `range_start`, `range_end`: Observed Trading Range (actual trade dates)
  - Handles all TV property name variants (`"Start Date"`, `"Date Start"`, `"Backtest Start Date"`)
  - **Fallback** (when Pine script has no explicit start_date input): `pine_start` falls back to `range_start - 14 days`. The 14-day buffer ensures the first trade's signal bar (one bar before fill) passes the engine's date gate. `pine_end` falls back to `"2100-12-31"`.
  - **Standard usage** (see Strategy Template): `start_date = max(pine_start, "2018-01-01")` (trading-start floor) and `end_date = range_end` (caps backtest at XLSX observation window). Pass to both signal generator and BacktestConfig.

## Multi-Timeframe (MTF) Rules
- **Fetch each timeframe separately** (one CSV per TF) — via `fetch_tv()`, manual TV export, or any of the other fetchers in priority order (see Data Loading section).
- **NEVER allow look-ahead bias** when using a higher timeframe (HTF) as a filter/signal
- A higher TF bar (e.g. 1W) is **not closed yet** while trading on a lower TF (e.g. 1D)
  → The HTF value must only update once the HTF bar actually closes
  → On a daily chart using weekly data: the weekly value only changes on the weekly close bar (e.g. Sunday/Monday), NOT mid-week
- Implementation approach: forward-fill (`ffill`) the HTF indicator onto the LTF index, shifted by one HTF bar, so each LTF bar only sees the **last completed** HTF value
- Always verify: no LTF bar should reference an HTF bar whose close date is in the future relative to that LTF bar

## Reference Backtest: EMA Cross on INDEX:BTCUSD 1D
- Data: TradingView export `INDEX_BTCUSD, 1D.csv`
- **Long-only (EMA 9/21):** Net Profit $9,180 (917.95%), 60 trades, 33.33% win rate, PF 1.813, Max DD -$3,420 (-61.01%)
- **Long+Short (EMA 9/21 long + EMA 5/13 short, with reversals):** Net Profit $1,501 (150.11%), 155 trades, PF 1.167, Open P&L ~$513
- First trade: Entry 2018-01-06 @ $16,955.45
- **TV with 100% margin**: 86 trades (59 real + 27 margin calls), Net Profit $9,736 (973.59%)
- **TV with 0% margin**: matches Python's 60 trades

## Known Precision Limitations

### Pyramiding L+S with percent_of_equity sizing — near-match only
- **Result**: 188 engine trades vs 190 TV trades (~4.8% net profit diff)
- **Root cause**: Sub-cent OHLC precision differences (e.g., engine `13340.705000` vs TV's internal float) compound through RSI's exponential smoothing (SMMA/RMA) over ~960 bars. This shifts RSI crossunders near the 40/60 thresholds by 1 bar, which cascades through percent-of-equity sizing into all subsequent trades.
- **Not a logic bug**: The engine's pyramiding logic, RSI calculation, and SMMA algorithm are all verified correct. The gap comes from floating-point accumulation in indicator calculations — unfixable without TV's exact internal float values.
- **3 missing trades pattern**: All 3 TV extras follow the same pattern — reversal from 2 fully-pyramided longs, then a SHORT pyramid add the next day. The deferred pyramid gate (`n_positions < pyramiding`) blocks these because `n_positions` still reflects the old direction's count at check time.
- **Verified tolerance**: ±3 trades, ±6% net profit, ±10% commission

## Requirements
- Python 3.10+
- See `requirements.txt` for package dependencies.

**First-time setup help**: If a new user hits `python3: command not found` or a version below the minimum listed above, **run the install for them** (don't just print instructions for them to copy-paste). Use the Bash tool to execute the commands below, verify each step, then proceed only once `python3 --version` returns a supported version.

**Goal**: `python3 --version` must satisfy the minimum listed above. The exact install command doesn't matter — only the verification step does.

**Install via the platform's standard package manager.** Examples below are starting points — if a command no longer works, fall back to the platform's current install docs:
- macOS: `brew install python` (Homebrew installs the current stable release)
- Linux: use the distro's package manager — e.g. `sudo apt install python3 python3-pip` (Debian/Ubuntu), `sudo dnf install python3` (Fedora/RHEL), `sudo pacman -S python` (Arch)
- Windows: official installer from [python.org](https://www.python.org/downloads/) with **"Add to PATH"** checked

**`python3` vs `python`**: All engine commands assume `python3` resolves. If only `python` resolves on the user's machine:
- macOS/Linux: add `alias python3=python` to `.zshrc`/`.bashrc`
- Windows: use the `py -3` launcher that ships with the python.org installer, or copy `python.exe` to `python3.exe` in the install directory
