from __future__ import annotations

import pandas as pd
import pytest

from dashboard_react import server


def _daily_frame(count: int = 35) -> pd.DataFrame:
    dates = pd.date_range("2026-09-01", periods=count, freq="B")
    return pd.DataFrame(
        {
            "date": dates,
            "open": range(100, 100 + count),
            "high": range(101, 101 + count),
            "low": range(99, 99 + count),
            "close": range(100, 100 + count),
            "volume": [1000] * count,
        }
    )


def test_price_sparklines_returns_last_30_completed_bars_and_respects_cutoff(monkeypatch):
    calls: list[tuple[str, str]] = []
    frame = _daily_frame()

    def load(symbol: str, timeframe: str):
        calls.append((symbol, timeframe))
        return frame

    monkeypatch.setattr(server, "load_bars", load)
    monkeypatch.setattr(server, "_latest_completed_daily_cutoff", lambda: pd.Timestamp("2026-10-30"))

    payload = server._price_sparkline_payload(["AAPL", "AAPL", "MSFT"], "2026-10-15")

    assert payload["cutoff_date"] == "2026-10-15"
    assert list(payload["bars"]) == ["AAPL", "MSFT"]
    assert len(payload["bars"]["AAPL"]["bars"]) == 30
    assert payload["bars"]["AAPL"]["bars"][-1]["date"] == "2026-10-15"
    assert calls == [("AAPL", "daily"), ("MSFT", "daily")]


def test_price_sparklines_drops_invalid_values_and_deduplicates_dates(monkeypatch):
    frame = pd.DataFrame(
        {
            "date": ["2026-10-01", "2026-10-02", "2026-10-02", "2026-10-03", "bad"],
            "close": [10, 0, 12, float("nan"), 15],
        }
    )
    monkeypatch.setattr(server, "load_bars", lambda *_: frame)
    monkeypatch.setattr(server, "_latest_completed_daily_cutoff", lambda: pd.Timestamp("2026-10-10"))

    bars = server._price_sparkline_payload(["BRK.B"], "2026-10-10")["bars"]["BRK.B"]["bars"]

    assert bars == [{"date": "2026-10-01", "close": 10.0}, {"date": "2026-10-02", "close": 12.0}]


def test_price_sparklines_marks_missing_symbol_unavailable(monkeypatch):
    monkeypatch.setattr(server, "load_bars", lambda *_: pd.DataFrame())
    monkeypatch.setattr(server, "_latest_completed_daily_cutoff", lambda: pd.Timestamp("2026-10-10"))

    payload = server.api_price_sparklines("AAPL,AAPL,MISSING", "2026-10-10")

    assert set(payload["bars"]) == {"AAPL", "MISSING"}
    assert payload["bars"]["MISSING"] == {
        "bars": [],
        "latest_available_date": None,
        "unavailable": True,
    }


def test_price_sparklines_validates_bounds_and_symbols():
    with pytest.raises(Exception):
        server.api_price_sparklines("BAD SYMBOL", "2026-10-10")
    with pytest.raises(Exception):
        server.api_price_sparklines(",".join(f"AAPL{i}" for i in range(501)), "2026-10-10")
