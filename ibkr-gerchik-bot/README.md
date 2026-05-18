# IBKR Gerchik Bot

Deterministic paper-trading bot for Interactive Brokers TWS / IB Gateway using Gerchik-style level strategies. Python executes the trades. NewsAPI.ai is used only as a risk filter, not as a signal generator.

## Install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configure `.env`

Create `.env` from `.env.template` and fill in at least:

```env
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=1
PAPER_TRADING=true
NEWS_API_KEY=
SLACK_WEBHOOK=
RISK_PER_TRADE=0.01
MAX_DAILY_LOSS=0.02
MAX_OPEN_POSITIONS=5
```

Important:
- `PAPER_TRADING=true` is the default safety mode.
- `DRY_RUN_MODE=true` prevents real order submission even against paper TWS.
- `AUTO_GIT_PUSH=true` is optional and will commit/push workflow memory updates to `Test`.

## TWS Connection

1. Launch TWS or IB Gateway.
2. In TWS open `Edit > Global Configuration > API > Settings`.
3. Enable socket/API clients.
4. Confirm the socket port matches your `.env`.
5. Use paper trading first.

## Project Layout

```text
ibkr-gerchik-bot/
├── src/
│   ├── main.py
│   ├── config.py
│   ├── scheduler.py
│   ├── brokers/ibkr.py
│   ├── data/
│   ├── strategy/
│   ├── risk/
│   ├── execution/order_manager.py
│   ├── alerts/slack.py
│   └── jobs/
├── memory/
│   ├── TRADE_LOG.md
│   ├── RESEARCH_LOG.md
│   ├── LEVELS_LOG.md
│   └── WEEKLY_LOG.md
├── tests/
├── requirements.txt
├── .env.template
├── .gitignore
└── README.md
```

## Gerchik Strategies

### Rebound From Level
- Price approaches a strong level and rejects it.
- Confirmation candle must close back in the intended direction.
- Target is the next strong level.
- Minimum reward:risk is `3:1`.

### Breakout
- Price compresses under resistance or above support.
- Breakout must confirm with a close through the level.
- Overextended breakout candles are rejected.
- ATR room and spread filters must pass.

### One-Bar False Breakout
- A single candle pierces the level with a wick.
- It closes back inside the prior range.
- Entry is taken only after confirmation.

### Two-Bar False Breakout
- First candle creates the illusion of a real breakout.
- Second candle closes back through the level.
- Entry happens only on that recovery/failure confirmation.

### Complex False Breakout
- Price spends 3+ candles beyond the level.
- No clean impulse continuation appears.
- Price returns back through the level and confirms.

## Risk Controls

- Paper trading is enforced by default.
- No trade without stop-loss and target.
- Risk per trade is capped by `RISK_PER_TRADE`.
- Daily loss guardrail defaults to `2%`.
- Max open positions defaults to `5`.
- Weak levels, high spread, insufficient ATR room, duplicate positions, and high-risk news all block trades.

## Running Jobs

```powershell
python -m src.main --job premarket
python -m src.main --job open
python -m src.main --job intraday
python -m src.main --job eod
python -m src.main --job weekly
python -m src.main --job manual_watch --symbol SANM --entry 241.97 --stop 237.09 --target 255.22
python -m src.main --job manual_watch --symbol SANM --entry 241.97 --stop 237.09 --target 255.22 --execute
```

`manual_watch` is a one-shot validation / replay command for a single setup.
- By default it runs in simulation mode, even if `DRY_RUN_MODE=false`.
- Add `--execute` to actually submit the order to your connected paper account.
- If `--signal` is omitted, the bot infers `BUY` or `SELL` from the entry / stop / target relationship.

## Windows Task Scheduler

Use:
- `Program/script`: full path to `python.exe`
- `Add arguments`: `-m src.main --job premarket`
- `Start in`: full path to `ibkr-gerchik-bot`

Suggested cadence:
- `premarket`: weekday early morning
- `open`: shortly after the open
- `intraday`: repeated during market hours
- `eod`: after the close
- `weekly`: Friday after the close

## Tests

```powershell
python -m unittest discover -s tests
```

The suite covers ATR, levels, level strength, breakout/false-breakout logic, stop calculation, reward:risk validation, position sizing, news blocking, and kill-switch behavior.
