"""
BMSB Strategy 1: Bull Market Support Band conversion.

Long-only strategy on INDEX:BTCUSD 1D:
- Weekly 21 EMA crosses above weekly 20 SMA: enter long.
- Weekly 21 EMA crosses below weekly 20 SMA: exit long.

Chart data: INDEX:BTCUSD 1D and 1W TradingView exports.
Slippage: NOT simulated (set to 0).
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import (  # noqa: E402
    BacktestConfig,
    calc_ema,
    calc_sma,
    detect_crossover,
    detect_crossunder,
    load_tv_export,
    print_kpis,
    print_trades,
    run_backtest,
)


START_DATE = "2018-01-01"
SMA_LENGTH = 20
EMA_LENGTH = 21


def add_bmsb_signals(daily: pd.DataFrame, weekly: pd.DataFrame) -> pd.DataFrame:
    """Add daily signals from completed weekly BMSB values."""
    df = daily.copy()
    weekly_band = pd.DataFrame(index=weekly.index + pd.Timedelta(days=6))
    weekly_band["bmsb_sma"] = calc_sma(weekly["Close"], SMA_LENGTH).to_numpy()
    weekly_band["bmsb_ema"] = calc_ema(weekly["Close"], EMA_LENGTH).to_numpy()

    aligned_index = df.index.union(weekly_band.index)
    aligned = weekly_band.reindex(aligned_index).sort_index().ffill().reindex(df.index)

    df["bmsb_sma"] = aligned["bmsb_sma"]
    df["bmsb_ema"] = aligned["bmsb_ema"]
    df["long_entry"] = detect_crossover(df["bmsb_ema"], df["bmsb_sma"])
    df["long_exit"] = detect_crossunder(df["bmsb_ema"], df["bmsb_sma"])
    return df


def main() -> None:
    daily = load_tv_export("INDEX_BTCUSD, 1D.csv")
    weekly = load_tv_export("INDEX_BTCUSD, 1W.csv")

    end_date = daily.index[-1].strftime("%Y-%m-%d")
    df = add_bmsb_signals(daily, weekly)

    config = BacktestConfig(
        initial_capital=1000.0,
        commission_pct=0.1,
        slippage_ticks=0,
        qty_type="percent_of_equity",
        qty_value=100.0,
        pyramiding=1,
        start_date=START_DATE,
        end_date=end_date,
    )

    kpis = run_backtest(df, config)
    total_pnl = kpis["net_profit"] + kpis["open_profit"]
    total_pnl_pct = total_pnl / config.initial_capital * 100.0

    print("\n" + "=" * 60)
    print("  BMSB STRATEGY 1 BACKTEST CONFIGURATION")
    print("=" * 60)
    print("  Chart Data:       INDEX:BTCUSD 1D (TradingView export)")
    print("  Weekly Data:      INDEX:BTCUSD 1W (TradingView export)")
    print(f"  Strategy Dates:   {START_DATE} to {end_date}")
    print(f"  Daily Data Range: {daily.index[0].date()} to {daily.index[-1].date()}")
    print(f"  Weekly Data Range:{weekly.index[0].date()} to {weekly.index[-1].date()}")
    print(f"  Initial Capital:  ${config.initial_capital:,.0f}")
    print(f"  Order Size:       {config.qty_value:.0f}% of equity")
    print(f"  Commission:       {config.commission_pct}%")
    print("  Slippage:         0 (NOT simulated; requires tick/order-book data)")
    print("  Margin Long/Short: 0%")
    print(f"  Strategy:         Weekly {EMA_LENGTH} EMA / {SMA_LENGTH} SMA cross, long-only")
    print("=" * 60)

    print("\n--- BMSB STRATEGY 1 (LONG ONLY) ---")
    print(f"  Total P&L (incl. open): ${total_pnl:,.2f} ({total_pnl_pct:.2f}%)")
    print_kpis(kpis)
    print_trades(kpis["trades"], max_trades=20)


if __name__ == "__main__":
    main()
