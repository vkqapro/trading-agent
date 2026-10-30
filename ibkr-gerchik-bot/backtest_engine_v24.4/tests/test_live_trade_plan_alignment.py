from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live_engine import CurrentSessionState, DailySetup, SetupStatus, evaluate_live_setup


ROOT = Path(__file__).resolve().parents[2]


def _setup(strategy: str = "lp2", level: float = 137.2475) -> DailySetup:
    pattern = {
        "bar_1": {"date": "2026-09-28", "role": "penetration_bar", "open": 132.84, "high": 135.69, "low": 131.58, "close": 132.60, "volume": 17_569_986},
        "bar_2": {"date": "2026-09-29", "role": "reclaim_bar" if strategy == "lp2" else "hold_bar", "open": 132.86, "high": 143.67, "low": 132.53, "close": 137.79, "volume": 31_558_965},
    }
    return DailySetup(
        "1.0", f"orcl_{strategy}", "ORCL", strategy, "LONG", "VALID_SETUP",
        {"price": level, "type": "support" if strategy in {"lp1", "lp2"} else "resistance"},
        pattern,
        {"entry": 0.0, "stop": 0.0, "target": 0.0},
        {"atr": 6.921, "strategy_params": {"gap_max_atr": 1.2, "chase_max_atr": 0.3, "rr": 2.0, "level_tolerance_atr": 0.1}},
        {"historical_data_through": "2026-09-29", "strategy_params": {"gap_max_atr": 1.2, "chase_max_atr": 0.3, "rr": 2.0, "level_tolerance_atr": 0.1}},
    )


def _historical() -> pd.DataFrame:
    return pd.read_csv(ROOT / "memory" / "bars" / "ORCL__daily.csv")


def _session(cutoff: str) -> CurrentSessionState:
    bars = pd.read_csv(ROOT / "memory" / "bars" / "ORCL__intraday_5m.csv")
    bars["date"] = pd.to_datetime(bars["date"])
    bars = bars[(bars["date"].dt.date == pd.Timestamp("2026-09-30").date()) & (bars["date"].dt.strftime("%H:%M") <= cutoff)]
    return CurrentSessionState(
        "2026-09-30", float(bars.iloc[0].open), float(bars.high.max()), float(bars.low.min()),
        float(bars.iloc[-1].close), float(bars.volume.sum()), f"2026-09-30T{cutoff}:00-04:00",
        "in_progress", "fixture", "5m", str(bars.iloc[-1].date), 1.0,
    )


def test_orcl_live_atr_and_plan_evolution_matches_web() -> None:
    setup = _setup()
    expected = {
        "09:35": (6.838181, 140.666590, 129.870455),
        "09:45": (6.921038, 140.708019, 129.849740),
        "10:30": (6.921038, 140.708019, 129.849740),
        "14:00": (6.929609, 140.712305, 129.847598),
    }
    results = [evaluate_live_setup(setup, _session(cutoff), previous_close=137.79, historical_daily_bars=_historical()) for cutoff in expected]
    assert [result.setup.setup_id for result in results] == [setup.setup_id] * 4
    assert len({result.live_plan.plan_hash for result in results}) == 4
    for cutoff, result in zip(expected, results):
        atr, entry, stop = expected[cutoff]
        assert result.live_plan.atr == pytest.approx(atr, abs=1e-6)
        assert result.live_plan.entry == pytest.approx(entry, abs=1e-6)
        assert result.live_plan.stop == pytest.approx(stop, abs=1e-6)
        assert result.live_plan.target == pytest.approx(entry + 2 * (entry - stop), abs=1e-5)


def test_orcl_setup_b_exact_plan_values() -> None:
    result = evaluate_live_setup(_setup("lp2", 134.91), _session("09:45"), previous_close=137.79, historical_daily_bars=_historical())
    assert result.live_plan.entry == pytest.approx(138.370519, abs=1e-6)
    assert result.live_plan.stop == pytest.approx(129.849740, abs=1e-6)
    assert result.live_plan.target == pytest.approx(155.412076, abs=1e-6)


def test_current_session_is_not_used_as_pattern_input() -> None:
    setup = _setup()
    extreme = _session("14:00")
    result = evaluate_live_setup(setup, extreme, previous_close=137.79, historical_daily_bars=_historical())
    assert all(row["date"] != extreme.session_date for row in result.setup.pattern_bars.values())
    assert result.live_plan.plan_evidence["atr_source"] == "developing_entry_day"


def test_all_supported_long_strategies_use_live_plan_formulas() -> None:
    historical = _historical()
    session = _session("09:45")
    atr = 6.921038069957007
    for strategy in ("lp1", "lp2", "prb1", "prb2"):
        result = evaluate_live_setup(_setup(strategy), session, previous_close=137.79, historical_daily_bars=historical)
        assert result.live_plan.entry == pytest.approx(137.2475 + 0.5 * atr, abs=1e-6)
        expected_reference = {"lp1": 131.58, "lp2": 131.58, "prb1": 137.2475, "prb2": 132.53}[strategy]
        assert result.live_plan.stop == pytest.approx(expected_reference - 0.25 * atr, abs=1e-6)
        assert result.live_plan.target == pytest.approx(result.live_plan.entry + 2 * (result.live_plan.entry - result.live_plan.stop), abs=1e-6)


def test_prb1_and_prb2_partial_session_hold_gates() -> None:
    historical = _historical()
    session = _session("09:45")
    for strategy in ("prb1", "prb2"):
        setup = _setup(strategy, 137.2475)
        result = evaluate_live_setup(setup, session, previous_close=137.79, historical_daily_bars=historical)
        assert result.status in {SetupStatus.WATCH, SetupStatus.READY, SetupStatus.REJECTED}
        bad = CurrentSessionState(session.session_date, session.open, session.high, 136.0, session.close, session.volume, session.as_of, session.completeness, session.source, session.timeframe, session.persisted_intraday_timestamp, session.freshness_seconds)
        rejected = evaluate_live_setup(setup, bad, previous_close=137.79, historical_daily_bars=historical)
        if strategy == "prb2":
            assert "ENTRY_DAY_HOLD_FAILED" in rejected.trigger.rejection_evidence


# Imported late to keep the test's public setup helpers easy to inspect.
import pytest
