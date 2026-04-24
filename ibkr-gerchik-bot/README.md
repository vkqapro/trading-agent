# IBKR Gerchik Bot

Production-ready Python trading system for Interactive Brokers paper trading using deterministic Gerchik-style strategies: false breakout, rebound, and third touch.

## Core Rules

- Python executes all trades deterministically.
- AI is only used during development and is not part of live trade decisions.
- News is a filter and risk blocker, not a primary signal.
- No trade is sent without validation, sizing, and a stop loss.
- The system is built for Windows and external scheduling through Task Scheduler.

## Project Structure

```text
ibkr-gerchik-bot/
├── src/
│   ├── main.py
│   ├── config.py
│   ├── scheduler.py
│   ├── brokers/ibkr.py
│   ├── strategy/
│   ├── risk/
│   ├── execution/order_manager.py
│   ├── data/
│   ├── alerts/slack.py
│   └── jobs/
├── memory/
├── tests/
├── requirements.txt
├── .env.template
├── .gitignore
└── README.md
```

## Setup

1. Create and activate a virtual environment.
2. Install dependencies.

```powershell
pip install -r requirements.txt
```

3. Copy `.env.template` to `.env`.
4. Fill in `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`, `NEWS_API_KEY`, and `SLACK_WEBHOOK`.
5. Start TWS or IB Gateway with API access enabled.
6. Keep paper trading enabled until the full workflow has been validated.

## TWS Connection

- TWS paper port is commonly `7497`.
- IB Gateway paper port is commonly `4002`.
- Enable API access in `Edit > Global Configuration > API > Settings`.
- Use a unique `clientId` for this bot.
- The broker adapter includes reconnect logic and connection health checks.

## Workflow

1. `premarket`
   Detect levels, fetch earnings and headline context, and build the watchlist.
2. `open`
   Evaluate Gerchik setups, block risky symbols or macro conditions, validate hard risk rules, and place market plus stop orders.
3. `intraday`
   Monitor positions, exit on breaking news, and move stops to break-even after a 1R move.
4. `eod`
   Record the day summary and notify Slack.
5. `weekly`
   Aggregate weekly performance metrics.

## Run Jobs

Run from the `ibkr-gerchik-bot` folder:

```powershell
python -m src.main --job premarket
python -m src.main --job open
python -m src.main --job intraday
python -m src.main --job eod
python -m src.main --job weekly
```

## Windows Task Scheduler

Create one task per job and point each task to the same Python executable.

- `Program/script`: full path to `python.exe`
- `Add arguments`: `-m src.main --job open`
- `Start in`: full path to `ibkr-gerchik-bot`

Suggested schedule:

- `premarket`: weekdays around 08:30 ET
- `open`: weekdays around 09:35 ET
- `intraday`: weekdays every 15-30 minutes from 10:30 ET to 15:30 ET
- `eod`: weekdays around 16:00 ET
- `weekly`: Friday after market close

## Logging

- Trades are appended to `memory/TRADE_LOG.md`
- Research, scans, and intraday notes are appended to `memory/RESEARCH_LOG.md`
- Weekly summaries are appended to `memory/WEEKLY_LOG.md`
- Runtime logs and state are stored under `memory/runtime/`

## Tests

```powershell
python -m unittest discover -s tests
```

The tests cover:

- strategy signals
- validator rules
- news filtering
- position sizing

## Notes

- The code assumes U.S. equities routed through SMART.
- Live broker and market data calls require a running TWS or IB Gateway session.
- News endpoints are configurable and return empty results gracefully when `NEWS_API_KEY` is missing.
