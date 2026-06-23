"""One-off backfill of OHLCV bars for the dashboard.

Connects to TWS / IB Gateway and fetches historical bars for the current
watchlist (or an explicit symbol list), saving them under ``memory/bars/`` via
``src.data.bar_store`` — the same store the premarket/intraday jobs write to.
After this runs, the dashboard's candle charts populate immediately instead of
waiting for the next scheduled scan.

Requires TWS / IB Gateway running with the API enabled.

Usage (from the project root):

    python backfill_bars.py                       # whole current watchlist
    python backfill_bars.py --symbols AMZN,AAPL   # specific tickers
    python backfill_bars.py --client-id 23        # override API client id

A distinct client id (default 17) is used so this never collides with a live
bot session running on its own client id.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill dashboard bars from IBKR.")
    parser.add_argument("--symbols", default="", help="Comma-separated tickers (default: current watchlist).")
    parser.add_argument("--client-id", type=int, default=17, help="IBKR API client id to use (default: 17).")
    parser.add_argument("--daily-days", type=int, default=None, help="Daily lookback in days (default: config value).")
    args = parser.parse_args()

    # Must be set before importing src.config so SETTINGS picks it up.
    os.environ["IBKR_CLIENT_ID"] = str(args.client_id)

    from src.config import LOGGER, SETTINGS
    from src.brokers.ibkr import IBKRClient
    from src.data.bar_store import save_bars
    from src.data.market_data import MarketDataService
    from dashboard.data_access import load_watchlist

    if args.symbols.strip():
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = sorted(load_watchlist().keys())

    if not symbols:
        LOGGER.warning("No symbols to backfill (watchlist empty and --symbols not given).")
        return 1

    daily_days = args.daily_days or SETTINGS.strategy.premarket_daily_lookback_days
    LOGGER.info("Backfilling %d symbol(s) using client_id=%s", len(symbols), args.client_id)

    broker = IBKRClient()
    broker.connect()
    market_data = MarketDataService(broker)

    ok = 0
    failed = 0
    try:
        for i, symbol in enumerate(symbols, 1):
            try:
                daily = market_data.get_daily_bars(symbol, duration=f"{daily_days} D")
                intraday_15m = market_data.get_intraday_bars(symbol, duration="2 D", bar_size="15 mins")
                intraday_5m = market_data.get_intraday_bars(
                    symbol,
                    duration=SETTINGS.strategy.intraday_bar_duration,
                    bar_size=SETTINGS.strategy.intraday_bar_size,
                )
                save_bars(symbol, "daily", daily)
                save_bars(symbol, "intraday_15m", intraday_15m)
                save_bars(symbol, "intraday_5m", intraday_5m)
                LOGGER.info(
                    "[%d/%d] %s: daily=%d 15m=%d 5m=%d",
                    i, len(symbols), symbol, len(daily), len(intraday_15m), len(intraday_5m),
                )
                ok += 1
            except Exception as exc:  # keep going on per-symbol failures
                failed += 1
                LOGGER.warning("[%d/%d] %s failed: %s", i, len(symbols), symbol, exc)
    finally:
        broker.disconnect()

    LOGGER.info("Backfill complete: %d ok, %d failed.", ok, failed)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
