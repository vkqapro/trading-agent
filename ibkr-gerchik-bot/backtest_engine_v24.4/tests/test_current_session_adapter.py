from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live_engine.adapter import PersistedSessionAdapter


def _loader_factory(daily, intraday):
    def loader(symbol, timeframe, **kwargs):
        return {"candles": daily if timeframe == "1D" else intraday}
    return loader


def _daily_row(closed=False):
    return {"timestamp": "2026-09-30T00:00:00-04:00", "open": 136.54, "high": 150.0, "low": 130.0, "close": 137.0, "volume": 999, "closed": closed}


def test_intraday_current_session_wins_over_incomplete_daily_row():
    intraday = [
        {"timestamp": "2026-09-30T14:30:00-04:00", "open": 136.54, "high": 138.15, "low": 136.38, "close": 136.74, "volume": 846176, "closed": True},
        {"timestamp": "2026-09-30T14:35:00-04:00", "open": 136.72, "high": 137.15, "low": 135.91, "close": 136.39, "volume": 876275, "closed": True},
        {"timestamp": "2026-09-30T14:40:00-04:00", "open": 136.39, "high": 136.39, "low": 134.75, "close": 136.09, "volume": 597268, "closed": True},
    ]
    state = PersistedSessionAdapter(_loader_factory([_daily_row()], intraday)).load("ORCL", as_of="2026-09-30T14:45:00-04:00")
    assert state.source == "intraday_5m_aggregate"
    assert state.session_date == "2026-09-30"
    assert state.as_of == "2026-09-30T14:40:00-04:00"
    assert state.persisted_intraday_timestamp == state.as_of


def test_intraday_ohlcv_aggregation_is_exact():
    intraday = [
        {"timestamp": "2026-09-30T14:30:00-04:00", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "closed": True},
        {"timestamp": "2026-09-30T14:35:00-04:00", "open": 11, "high": 15, "low": 10, "close": 14, "volume": 250, "closed": True},
    ]
    state = PersistedSessionAdapter(_loader_factory([_daily_row()], intraday)).load("ORCL", as_of="2026-09-30T14:40:00-04:00")
    assert (state.open, state.high, state.low, state.close, state.volume) == (10.0, 15.0, 9.0, 14.0, 350.0)
    assert state.source == "intraday_5m_aggregate"
    assert state.timeframe == "5m"
    assert state.complete is False


def test_incomplete_daily_row_is_only_fallback_without_usable_intraday():
    state = PersistedSessionAdapter(_loader_factory([_daily_row()], [])).load("ORCL", as_of="2026-09-30T14:45:00-04:00")
    assert state.source == "stored_daily_current_session"
    assert state.session_date == "2026-09-30"
    assert state.as_of == "2026-09-30T00:00:00-04:00"
    assert state.persisted_intraday_timestamp == state.as_of
    assert state.freshness_seconds == 53100.0


def test_closed_intraday_bars_are_required():
    intraday = [{"timestamp": "2026-09-30T14:40:00-04:00", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 10, "closed": False}]
    state = PersistedSessionAdapter(_loader_factory([_daily_row()], intraday)).load("ORCL", as_of="2026-09-30T14:45:00-04:00")
    assert state.source == "stored_daily_current_session"


def test_orcl_persisted_adapter_prefers_intraday_source():
    state = PersistedSessionAdapter().load("ORCL", as_of="2026-09-30T14:45:00-04:00")
    assert state.source == "intraday_5m_aggregate"
    assert state.as_of.endswith("-04:00")
    assert state.persisted_intraday_timestamp == state.as_of
    assert state.freshness_seconds is not None
    assert state.freshness_seconds >= 0
