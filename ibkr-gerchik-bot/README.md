# IBKR Gerchik Bot

Production-oriented Python trading system for Interactive Brokers paper trading using deterministic Gerchik-style level strategies: false breakout, rebound, and third touch.

## Features

- Interactive Brokers connectivity through `ib_insync`
- Deterministic strategy engine with validation gates before execution
- Risk controls for position sizing, open risk, daily loss, and kill switch checks
- Slack alerts for trade, stop, error, and daily summary events
- Markdown-based local memory logs under `memory/`
- Job-oriented design that maps cleanly to Windows Task Scheduler

## Project Structure

```text
ibkr-gerchik-bot/
├── src/
├── memory/
├── tests/
├── requirements.txt
├── .env.template
├── .gitignore
└── README.md
```

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Copy `.env.template` to `.env` and fill in your IBKR and Slack settings.
4. Start Interactive Brokers TWS or IB Gateway with API access enabled.
5. Use paper trading first. Keep `PAPER_TRADING=true` in your environment.

## IBKR Configuration

- TWS paper trading default port is commonly `7497`
- IB Gateway paper trading default port is commonly `4002`
- Ensure API access is enabled in TWS: `Edit > Global Configuration > API > Settings`
- Match the `clientId` in `.env` to a unique integer for this bot

## Running Jobs

Run any job from the project root:

```powershell
python -m src.main --job premarket
python -m src.main --job open
python -m src.main --job intraday
python -m src.main --job eod
python -m src.main --job weekly
```

## Windows Task Scheduler

Create separate tasks that call the same commands above on your preferred schedule.

Suggested schedule:

- `premarket`: weekdays around 08:30 ET
- `open`: weekdays around 09:35 ET
- `intraday`: weekdays every 15-30 minutes between 10:30 ET and 15:30 ET
- `eod`: weekdays around 16:00 ET
- `weekly`: Friday after market close

Set:

- `Program/script`: full path to `python.exe`
- `Add arguments`: `-m src.main --job open`
- `Start in`: full path to the `ibkr-gerchik-bot` folder

## Logging

- Trades are appended to `memory/TRADE_LOG.md`
- Research and daily notes are appended to `memory/RESEARCH_LOG.md`
- Weekly metrics are appended to `memory/WEEKLY_LOG.md`
- Runtime logs and persisted state are stored under `memory/runtime/`

## Tests

Run the test suite from the project root:

```powershell
python -m unittest discover -s tests
```

## Notes

- Trading decisions are fully deterministic and rule-based
- The code assumes U.S. equities via SMART routing
- Broker/network-dependent paths require a live TWS or IB Gateway connection
- Git initialization is expected after Git is available on your Windows `PATH`
