# Trading Operations Dashboard

Interactive Streamlit operations dashboard for Vitaly's Trading Bot. It is a
read-only viewer over files under `memory/`. It never connects to IBKR and never
mutates trading state.

The interface uses the **Luminous Obsidian** design system (see
`stitch_trading_bot_dashboard/DESIGN.md`): a glassmorphic obsidian canvas with
cyan / lime / magenta accents and Hanken Grotesk + Inter + JetBrains Mono
typography. The theme and reusable presentation helpers live in
[`components.py`](components.py); chart styling lives in [`charts.py`](charts.py).

## Install

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements-dashboard.txt
```

## Run

```powershell
streamlit run dashboard/app.py
```

You can also run `run_dashboard.cmd`. The local URL is
http://localhost:8501.

## Workspaces

- **Dashboard** - at-a-glance command view: live metric cards (watchlist,
  trade-ready, news-blocked, attempts, action signals, execution rate),
  decision-bias / risk-management / trade-ready-ratio panels, the daily market
  structure chart, an opportunity queue, and a recent attempt log.
- **Pre-Open** - searchable opportunity queue, data-quality status, and
  per-ticker candles with optional raw levels, optimized trade zones, and
  volume.
- **Intraday** - session metrics, attempted levels, strategy summaries, signal
  filters, and a normalized decision log.
- **Trades & Positions** - tracked positions, latest executed/skipped
  candidates, and recent trade-journal entries.
- **Reports** - reports grouped by type and trading date, optional ticker
  filtering, and workbook download.

The header reports source freshness. Missing or stale sources are visible
instead of silently appearing as empty data. Large source files, bars, trade
logs, and workbooks are cached by modification time, so widget changes do not
reparse unchanged files.

## Candle data

Charts read OHLCV bars persisted under `memory/bars/`. To populate candles
without waiting for the next scheduled scan, run the backfill with TWS or IB
Gateway open:

```powershell
python backfill_bars.py
python backfill_bars.py --symbols AMZN,AAPL
```

The backfill uses a separate API client ID by default. Refresh the dashboard
after it completes.
