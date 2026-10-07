from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backtest_engine_v24.4"))

from dashboard_react.market_screener import (  # noqa: E402
    ScreenerParams,
    _add_metrics,
    _detect_signals,
)
from live_engine.engine import _web_imports  # noqa: E402


def _snapshot() -> pd.DataFrame:
    dates = pd.date_range("2026-08-12", periods=40, freq="B")
    rows = [{
        "date": date, "open": 99.5, "high": 100.0, "low": 99.0,
        "close": 99.5, "volume": 1_000_000,
    } for date in dates]
    for index, close in zip(range(34, 38), (99.0, 99.2, 99.4, 99.6)):
        rows[index].update(open=close, high=close + 0.2, low=close - 0.2, close=close)
    rows[38].update(open=99.8, high=101.2, low=99.6, close=100.8, volume=2_000_000)
    rows[39].update(open=100.7, high=101.4, low=100.4, close=101.1, volume=250_000)
    frame = _add_metrics(pd.DataFrame(rows))
    frame["atr_clean_14"] = 1.0
    frame["median_volume_20"] = 1_000_000
    return frame


def test_web_and_mcp_share_prb1_evaluator_for_same_partial_snapshot() -> None:
    frame = _snapshot()
    levels = [{"price": 100.0, "kind": "resistance", "touches": 3, "strength": 0.8, "tolerance": 0.2}]
    params = ScreenerParams(strategies=("PRB1",), side_filter="LONG")

    _, _, mcp_evaluator, _ = _web_imports()
    web_signals = _detect_signals("GSHD", frame, levels, params, entry_day_partial=True)
    mcp_signals = mcp_evaluator("GSHD", frame, levels, params, entry_day_partial=True)

    assert mcp_evaluator is _detect_signals
    assert len(web_signals) == len(mcp_signals) == 1
    web = web_signals[0]
    mcp = mcp_signals[0]
    assert (web["status"], web["side"]) == ("READY", "LONG")
    assert (mcp["status"], mcp["side"]) == ("READY", "LONG")
    for key in ("level_price", "entry_price", "stop_price", "take_profit_price", "atr_clean_14"):
        assert mcp[key] == web[key]
