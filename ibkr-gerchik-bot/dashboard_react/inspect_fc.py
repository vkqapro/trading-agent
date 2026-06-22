import sys; sys.path.insert(0, '.')
from dashboard import forecast as fc, data_access as da
scenario = fc.ForecastScenario(
    account_equity=100000, cash_available=100000, daily_realized_pnl=0,
    assumed_spread_pct=0.0005, risk_per_trade=0.01, max_daily_loss_pct=0.02,
    min_reward_risk_ratio=3.0, max_positions=5, max_spread_pct=0.002,
    max_open_risk_pct=0.06, max_position_value=50000,
    min_projected_entry_distance_atr=1.0, max_projected_entry_distance_atr=2.0,
)
watchlist = da.load_watchlist() or {}
positions = da.load_tracked_positions() or []
projected = fc.projected_level_candidates(watchlist)
active, warns = fc.replay_active_candidates(watchlist, da.get_bars)
candidates = active + projected
result = fc.run_forecast(candidates, scenario, positions)
rows = result.get("rows", [])
print(f"Total rows: {len(rows)}")
if rows:
    print("Keys:", list(rows[0].keys()))
    for k, v in rows[0].items():
        print(f"  {k}: {repr(v)[:80]}")
