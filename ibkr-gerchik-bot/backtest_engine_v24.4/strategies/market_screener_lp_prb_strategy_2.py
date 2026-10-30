"""Market Screener LP/PRB Strategy 2: long-only S&P 500 validation harness.

This is a sanitized conversion of ``market_screener_lp_prb_strategy_1.pine``.
The simulator preserves the Pine strategy's important execution details:
confirmed pivot levels, one-bar-delayed pattern signals, stop-entry orders,
attached stop/limit exits, opposite-signal close/reversal, fixed share sizing,
0.1% commission, and zero slippage.

The research section deliberately tests a small number of named, hypothesis-
driven variants rather than an exhaustive parameter sweep.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import Trade, compute_kpis, load_tv_export  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "cache" / "tv_NASDAQ_ASND_1D_5000.csv"
RESEARCH_DIR = ROOT / "research"
START_DATE = "2018-01-01"
END_DATE = "2100-01-01"
INITIAL_CAPITAL = 1_000.0
COMMISSION_PCT = 0.1
TOP20 = ["AAPL", "NVDA", "MSFT", "AMZN", "GOOGL", "AVGO", "META", "TSLA", "LLY", "BRK.B", "JPM", "MU", "WMT", "AMD", "V", "JNJ", "XOM", "MA", "ABBV", "CSCO"]


@dataclass(frozen=True)
class Params:
    lp1: bool = True
    lp2: bool = True
    prb1: bool = True
    prb2: bool = True
    side: str = "Long Only"
    pivot_k: int = 5
    min_touches: int = 2
    touch_lookback: int = 120
    level_tol_atr: float = 0.10
    open_tol_atr: float = 0.15
    allow_role_flip: bool = True
    atr_length: int = 14
    volume_length: int = 20
    entry_atr_pct: float = 0.50
    lp1_delta_break: float = 0.05
    lp1_delta_close: float = 0.03
    lp2_delta_break: float = 0.05
    lp2_delta_close: float = 0.03
    prb1_delta_break: float = 0.05
    prb1_delta_close: float = 0.05
    prb2_delta_break: float = 0.05
    prb2_delta_close: float = 0.05
    prb2_hold_epsilon_atr: float = 0.05
    range_cap: float = 2.5
    two_bar_range_cap: float = 4.0
    lp1_volume_mult: float = 1.0
    lp2_volume_mult: float = 1.0
    prb_volume_mult: float = 1.2
    approach_lookback: int = 4
    approach_min_atr: float = 0.05
    lp2_overextended_atr: float = 1.2
    rr: float = 2.0
    stop_buffer_atr: float = 0.25
    stop_variant: str = "Strategy Default"
    gap_max_atr: float = 1.2
    chase_max_atr: float = 0.30
    sizing_equity: float = 100_000.0
    risk_pct: float = 0.005
    max_shares: int = 10_000
    min_score: float = 0.0
    require_trigger: bool = True
    close_on_opposite: bool = True
    # Improvement switches; defaults reproduce the sanitized baseline.
    ema_regime: str = "Off"  # Off, EMA200, EMA50/200
    rsi_filter: str = "Off"  # Off, Long>50/Short<50
    max_signal_atr: float = 0.0  # 0 = disabled; otherwise signal range cap
    trend_exit: bool = False  # close long on a completed close below EMA50
    prb1_regime: str = "Off"  # Off, EMA200, EMA50/200
    exclude_long_prb2: bool = False
    exclude_long_prb1: bool = False
    exclude_short_prb1: bool = False
    exclude_short_prb2: bool = False


BASELINE = Params()

# Explainability metadata mirrors the already-selected candidate path. These
# names and bar offsets are audit output only; they are never consulted when
# choosing a candidate or calculating a trade plan.
_PATTERN_CONDITIONS = {
    ("LP1", 1): (
        "level_available", "approach_from_above", "prior_close_at_or_above_level",
        "volume_valid", "signal_open_at_or_above_level", "penetrated_below_level",
        "closed_back_above_level", "signal_range_valid", "lower_wick_at_least_body",
        "close_position_valid",
    ),
    ("LP1", -1): (
        "level_available", "approach_from_below", "prior_close_at_or_below_level",
        "volume_valid", "signal_open_at_or_below_level", "penetrated_above_level",
        "closed_back_below_level", "signal_range_valid", "upper_wick_at_least_body",
        "close_position_valid",
    ),
    ("LP2", 1): (
        "level_available", "approach_from_above", "pre_pattern_close_at_or_above_level",
        "volume_valid", "penetrated_below_level", "penetration_close_below_level",
        "reclaim_close_above_level", "two_bar_range_valid",
    ),
    ("LP2", -1): (
        "level_available", "approach_from_below", "pre_pattern_close_at_or_below_level",
        "volume_valid", "penetrated_above_level", "penetration_close_above_level",
        "reclaim_close_below_level", "two_bar_range_valid",
    ),
    ("PRB1", 1): (
        "level_available", "approach_from_below", "prior_close_at_or_below_level",
        "volume_valid", "breakout_open_at_or_below_level", "broke_above_level",
        "breakout_close_above_level", "signal_open_holds_level", "prb1_regime_valid",
    ),
    ("PRB1", -1): (
        "level_available", "approach_from_above", "prior_close_at_or_above_level",
        "volume_valid", "breakout_open_at_or_above_level", "broke_below_level",
        "breakout_close_below_level", "signal_open_holds_level",
    ),
    ("PRB2", 1): (
        "level_available", "approach_from_below", "pre_breakout_close_at_or_below_level",
        "breakout_volume_valid", "two_bar_range_valid", "breakout_open_at_or_below_level",
        "broke_above_level", "breakout_close_above_level", "hold_low_valid",
        "hold_close_valid", "signal_open_holds_level",
    ),
    ("PRB2", -1): (
        "level_available", "approach_from_above", "pre_breakout_close_at_or_above_level",
        "breakout_volume_valid", "two_bar_range_valid", "breakout_open_at_or_above_level",
        "broke_below_level", "breakout_close_below_level", "hold_high_valid",
        "hold_close_valid", "signal_open_holds_level",
    ),
}

_PATTERN_BAR_OFFSETS = {
    ("LP1", 1): (("bar_1", -1, "penetration_reclaim_bar"), ("signal_bar", 0, "trigger_bar")),
    ("LP1", -1): (("bar_1", -1, "penetration_reject_bar"), ("signal_bar", 0, "trigger_bar")),
    ("LP2", 1): (("bar_1", -2, "penetration_bar"), ("bar_2", -1, "reclaim_bar"), ("signal_bar", 0, "trigger_bar")),
    ("LP2", -1): (("bar_1", -2, "penetration_bar"), ("bar_2", -1, "reject_bar"), ("signal_bar", 0, "trigger_bar")),
    ("PRB1", 1): (("bar_1", -1, "breakout_bar"), ("signal_bar", 0, "trigger_bar")),
    ("PRB1", -1): (("bar_1", -1, "breakdown_bar"), ("signal_bar", 0, "trigger_bar")),
    ("PRB2", 1): (("bar_1", -2, "breakout_bar"), ("bar_2", -1, "hold_bar"), ("signal_bar", 0, "trigger_bar")),
    ("PRB2", -1): (("bar_1", -2, "breakdown_bar"), ("bar_2", -1, "hold_bar"), ("signal_bar", 0, "trigger_bar")),
}

_REJECTION_EVIDENCE = {
    "score": "score_below_minimum",
    "qty": "invalid_quantity",
    "gap": "gap_filter_failed",
    "chase": "chase_filter_failed",
    "overextended": "overextended",
    "no_trigger": "entry_trigger_missing",
    "regime": "regime_filter_failed",
    "rsi": "rsi_filter_failed",
}


def _rma(series: pd.Series, length: int) -> pd.Series:
    """TradingView ta.rma/ta.atr smoothing with an SMA seed."""
    values = series.to_numpy(dtype=float)
    out = np.full(len(values), np.nan, dtype=float)
    valid = np.flatnonzero(~np.isnan(values))
    if len(valid) < length:
        return pd.Series(out, index=series.index)
    seed_end = valid[length - 1]
    out[seed_end] = values[valid[:length]].mean()
    for i in range(seed_end + 1, len(values)):
        if np.isnan(values[i]):
            out[i] = out[i - 1]
        else:
            out[i] = (out[i - 1] * (length - 1) + values[i]) / length
    return pd.Series(out, index=series.index)


def _atr(df: pd.DataFrame, length: int) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev_close).abs(), (df["Low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return _rma(tr, length)


def _ema(series: pd.Series, length: int) -> pd.Series:
    values = series.to_numpy(dtype=float)
    out = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return pd.Series(out, index=series.index)
    out[length - 1] = values[:length].mean()
    alpha = 2.0 / (length + 1.0)
    for i in range(length, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return pd.Series(out, index=series.index)


def prepare(df: pd.DataFrame, p: Params = BASELINE) -> pd.DataFrame:
    """Compute Pine-equivalent indicator and pattern columns."""
    out = df.copy().sort_index()
    n = len(out)
    o = out["Open"].to_numpy(float)
    h = out["High"].to_numpy(float)
    l = out["Low"].to_numpy(float)
    c = out["Close"].to_numpy(float)
    v = out["Volume"].to_numpy(float)
    atr = _atr(out, p.atr_length).to_numpy(float)
    med = out["Volume"].rolling(p.volume_length, min_periods=p.volume_length).median().to_numpy(float)
    avg = out["Volume"].rolling(p.volume_length, min_periods=p.volume_length).mean().to_numpy(float)
    dollar = (out["Close"] * out["Volume"]).rolling(p.volume_length, min_periods=p.volume_length).mean().to_numpy(float)

    # ta.pivotlow/high reports a pivot only after p.pivot_k right bars close.
    pl = np.full(n, np.nan)
    ph = np.full(n, np.nan)
    k = p.pivot_k
    for i in range(2 * k, n):
        pivot_i = i - k
        window_l = l[i - 2 * k : i + 1]
        window_h = h[i - 2 * k : i + 1]
        if l[pivot_i] == np.nanmin(window_l):
            pl[i] = l[pivot_i]
        if h[pivot_i] == np.nanmax(window_h):
            ph[i] = h[pivot_i]
    support = pd.Series(pl, index=out.index).ffill().to_numpy(float)
    resistance = pd.Series(ph, index=out.index).ffill().to_numpy(float)

    support_level = np.full(n, np.nan)
    resistance_level = np.full(n, np.nan)
    lp_support_level = np.full(n, np.nan)
    lp_resistance_level = np.full(n, np.nan)
    support_touches = np.zeros(n, dtype=int)
    resistance_touches = np.zeros(n, dtype=int)
    tolerance = np.maximum(p.level_tol_atr * atr, c * 0.0005)
    open_tolerance = np.maximum(tolerance, p.open_tol_atr * atr)
    for i in range(n):
        if np.isnan(atr[i]) or np.isnan(support[i]) and np.isnan(resistance[i]):
            continue
        lo = max(0, i - p.touch_lookback)
        prior = np.arange(lo, i)
        if len(prior):
            if not np.isnan(support[i]):
                support_touches[i] = int(np.sum((l[prior] <= support[i] + tolerance[i]) & (h[prior] >= support[i] - tolerance[i])))
            if not np.isnan(resistance[i]):
                resistance_touches[i] = int(np.sum((l[prior] <= resistance[i] + tolerance[i]) & (h[prior] >= resistance[i] - tolerance[i])))
        if support_touches[i] >= p.min_touches:
            support_level[i] = support[i]
        if resistance_touches[i] >= p.min_touches:
            resistance_level[i] = resistance[i]
        lp_support_level[i] = support_level[i] if not np.isnan(support_level[i]) else (resistance_level[i] if p.allow_role_flip else np.nan)
        lp_resistance_level[i] = resistance_level[i] if not np.isnan(resistance_level[i]) else (support_level[i] if p.allow_role_flip else np.nan)

    ema50 = _ema(out["Close"], 50).to_numpy(float)
    ema200 = _ema(out["Close"], 200).to_numpy(float)
    # RSI 14, using the same RMA convention as Pine ta.rsi.
    delta = out["Close"].diff()
    gain = _rma(delta.clip(lower=0), 14).to_numpy(float)
    loss = _rma((-delta.clip(upper=0)), 14).to_numpy(float)
    rsi = 100.0 - 100.0 / (1.0 + gain / np.where(loss == 0, np.nan, loss))

    records: list[dict] = []
    for i in range(n):
        rec = {"signal": False, "pattern": "", "side": 0, "level": np.nan, "planned_entry": np.nan,
               "stop": np.nan, "target": np.nan, "qty": 0.0, "triggered": False, "reject": "", "trend_exit": False,
               "signal_bar_index": None, "level_type": None, "pattern_bar_indices": {},
               "pattern_rule_evidence": {}, "trigger_evidence": {}, "rejection_evidence": []}
        if i < max(p.approach_lookback + 3, p.volume_length + 2, 2 * k + 2) or np.isnan(atr[i]):
            records.append(rec)
            continue
        tol = tolerance[i]
        otol = open_tolerance[i]
        vol_sig = med[i - 1] if not np.isnan(med[i - 1]) else (avg[i - 1] if not np.isnan(avg[i - 1]) else v[i - 1])
        vol_day1 = med[i - 2] if not np.isnan(med[i - 2]) else (avg[i - 2] if not np.isnan(avg[i - 2]) else v[i - 2])
        def approach(start: int) -> int:
            old = i - (start + p.approach_lookback - 1)
            new = i - start
            ch = (c[new] - c[old]) / max(atr[i], 1e-12)
            return 1 if ch >= p.approach_min_atr else (-1 if ch <= -p.approach_min_atr else 0)
        lp1_app = approach(2)
        multi_app = approach(3)
        sig_body = abs(c[i - 1] - o[i - 1])
        sig_lower = min(o[i - 1], c[i - 1]) - l[i - 1]
        sig_upper = h[i - 1] - max(o[i - 1], c[i - 1])
        sig_pos = (c[i - 1] - l[i - 1]) / max(h[i - 1] - l[i - 1], 1e-12)
        first_pos = (c[i - 2] - l[i - 2]) / max(h[i - 2] - l[i - 2], 1e-12)
        signal_range_ok = (h[i - 1] - l[i - 1]) <= p.range_cap * atr[i]
        if p.max_signal_atr > 0:
            signal_range_ok = signal_range_ok and (h[i - 1] - l[i - 1]) <= p.max_signal_atr * atr[i]
        two_range_ok = max(h[i - 2], h[i - 1]) - min(l[i - 2], l[i - 1]) <= p.two_bar_range_cap * atr[i]
        prb2_range_ok = (h[i - 2] - l[i - 2] <= p.two_bar_range_cap * atr[i] and h[i - 1] - l[i - 1] <= p.two_bar_range_cap * atr[i])
        lp_long = lp_support_level[i]
        lp_short = lp_resistance_level[i]
        prb_long = resistance_level[i]
        prb_short = support_level[i]
        allow_long = p.side != "Short Only"
        allow_short = p.side != "Long Only"
        prb1_regime_ok = True
        if p.prb1_regime == "EMA200":
            prb1_regime_ok = c[i] > ema200[i]
        elif p.prb1_regime == "EMA50/200":
            prb1_regime_ok = c[i] > ema50[i] > ema200[i]
        candidates = [
            (p.lp1 and allow_long and not np.isnan(lp_long) and lp1_app == -1 and c[i - 2] >= lp_long - otol and v[i - 1] >= p.lp1_volume_mult * vol_sig and o[i - 1] >= lp_long - otol and l[i - 1] < lp_long - p.lp1_delta_break * atr[i] and c[i - 1] > lp_long + p.lp1_delta_close * atr[i] and signal_range_ok and sig_lower >= sig_body and sig_pos >= 0.60, "LP1", 1, lp_long, sig_pos, False, False),
            (p.lp1 and allow_short and not np.isnan(lp_short) and lp1_app == 1 and c[i - 2] <= lp_short + otol and v[i - 1] >= p.lp1_volume_mult * vol_sig and o[i - 1] <= lp_short + otol and h[i - 1] > lp_short + p.lp1_delta_break * atr[i] and c[i - 1] < lp_short - p.lp1_delta_close * atr[i] and signal_range_ok and sig_upper >= sig_body and sig_pos <= 0.40, "LP1", -1, lp_short, 1.0 - sig_pos, False, False),
            (p.lp2 and allow_long and not np.isnan(lp_long) and multi_app == -1 and c[i - 3] >= lp_long - otol and v[i - 1] >= p.lp2_volume_mult * vol_sig and l[i - 2] < lp_long - p.lp2_delta_break * atr[i] and c[i - 2] < lp_long and c[i - 1] > lp_long + p.lp2_delta_close * atr[i] and two_range_ok, "LP2", 1, lp_long, sig_pos, True, False),
            (p.lp2 and allow_short and not np.isnan(lp_short) and multi_app == 1 and c[i - 3] <= lp_short + otol and v[i - 1] >= p.lp2_volume_mult * vol_sig and h[i - 2] > lp_short + p.lp2_delta_break * atr[i] and c[i - 2] > lp_short and c[i - 1] < lp_short - p.lp2_delta_close * atr[i] and two_range_ok, "LP2", -1, lp_short, 1.0 - sig_pos, True, False),
            (p.prb1 and not p.exclude_long_prb1 and prb1_regime_ok and allow_long and not np.isnan(prb_long) and lp1_app == 1 and c[i - 2] <= prb_long + otol and v[i - 1] >= p.prb_volume_mult * vol_sig and o[i - 1] <= prb_long + otol and h[i - 1] > prb_long + p.prb1_delta_break * atr[i] and c[i - 1] > prb_long + p.prb1_delta_close * atr[i] and o[i] >= prb_long - tol, "PRB1", 1, prb_long, sig_pos, False, False),
            (p.prb1 and not p.exclude_short_prb1 and allow_short and not np.isnan(prb_short) and lp1_app == -1 and c[i - 2] >= prb_short - otol and v[i - 1] >= p.prb_volume_mult * vol_sig and o[i - 1] >= prb_short - otol and l[i - 1] < prb_short - p.prb1_delta_break * atr[i] and c[i - 1] < prb_short - p.prb1_delta_close * atr[i] and o[i] <= prb_short + tol, "PRB1", -1, prb_short, 1.0 - sig_pos, False, False),
            (p.prb2 and not p.exclude_long_prb2 and allow_long and not np.isnan(prb_long) and multi_app == 1 and c[i - 3] <= prb_long + otol and v[i - 2] >= p.prb_volume_mult * vol_day1 and prb2_range_ok and o[i - 2] <= prb_long + otol and h[i - 2] > prb_long + p.prb2_delta_break * atr[i] and c[i - 2] > prb_long + p.prb2_delta_close * atr[i] and l[i - 1] >= prb_long - tol and c[i - 1] >= prb_long - p.prb2_hold_epsilon_atr * atr[i] and o[i] >= prb_long - tol, "PRB2", 1, prb_long, first_pos, False, True),
            (p.prb2 and not p.exclude_short_prb2 and allow_short and not np.isnan(prb_short) and multi_app == -1 and c[i - 3] >= prb_short - otol and v[i - 2] >= p.prb_volume_mult * vol_day1 and prb2_range_ok and o[i - 2] >= prb_short - otol and l[i - 2] < prb_short - p.prb2_delta_break * atr[i] and c[i - 2] < prb_short - p.prb2_delta_close * atr[i] and h[i - 1] <= prb_short + tol and c[i - 1] <= prb_short + p.prb2_hold_epsilon_atr * atr[i] and o[i] <= prb_short + tol, "PRB2", -1, prb_short, 1.0 - first_pos, False, True),
        ]
        chosen = next((x for x in candidates if x[0]), None)
        if chosen is None:
            records.append(rec)
            continue
        _, name, side, level, quality, structure, day1 = chosen
        pattern_bar_indices = {
            bar_key: {"index": i + offset, "role": role}
            for bar_key, offset, role in _PATTERN_BAR_OFFSETS[(name, side)]
        }
        pattern_rule_evidence = {
            condition: True for condition in _PATTERN_CONDITIONS[(name, side)]
        }
        if name.startswith("LP"):
            if side == 1:
                level_type = "support" if np.isfinite(support_level[i]) and np.isclose(level, support_level[i]) else "resistance_role_flip"
            else:
                level_type = "resistance" if np.isfinite(resistance_level[i]) and np.isclose(level, resistance_level[i]) else "support_role_flip"
        else:
            level_type = "resistance" if side == 1 else "support"
        planned = level + side * p.entry_atr_pct * atr[i]
        triggered = h[i] >= planned if side == 1 else l[i] <= planned
        effective = max(planned, o[i]) if side == 1 and triggered else min(planned, o[i]) if side == -1 and triggered else planned
        if name == "LP1":
            pattern_quality = min(1.0, 0.45 + 0.35 * quality + 0.20 * min(1.0, (sig_lower if side == 1 else sig_upper) / max(sig_body, 1e-12)))
            sig_high, sig_low = h[i - 1], l[i - 1]
        elif name == "LP2":
            pattern_quality = min(1.0, 0.55 + 0.20 * (quality if side == 1 else 1.0 - quality) + 0.25 * min(1.0, ((level - l[i - 2]) if side == 1 else (h[i - 2] - level)) / max(atr[i], 1e-12)))
            sig_high, sig_low = max(h[i - 2], h[i - 1]), min(l[i - 2], l[i - 1])
        elif name == "PRB1":
            pattern_quality = min(1.0, 0.60 + 0.20 * (quality if side == 1 else 1.0 - quality) + 0.20 * min(1.0, ((c[i - 1] - level) if side == 1 else (level - c[i - 1])) / max(atr[i], 1e-12)))
            sig_high, sig_low = h[i - 1], l[i - 1]
        else:
            pattern_quality = min(1.0, 0.62 + 0.18 * (quality if side == 1 else 1.0 - quality) + 0.20 * min(1.0, ((l[i - 1] - (level - tol)) if side == 1 else ((level + tol) - h[i - 1])) / max(tol, 1e-12)))
            sig_high, sig_low = h[i - 2], l[i - 2]
        if p.stop_variant == "Strategy Default":
            requested = {"LP1": "Behind Signal Bar", "LP2": "Behind Two-Bar Structure", "PRB1": "Behind Level", "PRB2": "Behind Day 1"}[name]
        else:
            requested = p.stop_variant
        behind_level = level - side * p.stop_buffer_atr * atr[i]
        behind_signal = sig_low - p.stop_buffer_atr * atr[i] if side == 1 else sig_high + p.stop_buffer_atr * atr[i]
        behind_structure = sig_low - p.stop_buffer_atr * atr[i] if side == 1 else sig_high + p.stop_buffer_atr * atr[i]
        behind_day1 = l[i - 1] - p.stop_buffer_atr * atr[i] if side == 1 else h[i - 1] + p.stop_buffer_atr * atr[i]
        stop = {"Behind Level": behind_level, "Behind Signal Bar": behind_signal, "Behind Two-Bar Structure": behind_structure, "Behind Day 1": behind_day1}.get(requested, behind_level)
        if not (stop < effective if side == 1 else stop > effective):
            stop = min(behind_level, behind_signal) if side == 1 else max(behind_level, behind_signal)
        risk = abs(effective - stop)
        target = effective + side * p.rr * risk
        sized_risk = risk + 0.01 + 0.005
        qty = min(math.floor(p.sizing_equity * p.risk_pct / sized_risk) if sized_risk > 0 else 0, p.max_shares)
        distance_quality = max(0.0, 1.0 - abs(effective - c[i]) / max(atr[i] * 2.0, 1e-12))
        level_strength = min(1.0, 0.35 + 0.12 * max(support_touches[i], resistance_touches[i]))
        rel_vol = min(1.0, (v[i - 2] if name == "PRB2" else v[i - 1]) / max(vol_day1 if name == "PRB2" else vol_sig, 1.0) / 2.0)
        liquidity_quality = min(1.0, dollar[i] / 50_000_000.0) if not np.isnan(dollar[i]) else 0.0
        score = max(0.0, min(100.0, (0.30 * level_strength + 0.20 * rel_vol + 0.20 * distance_quality + 0.15 * pattern_quality + 0.10 * liquidity_quality + 0.05) * 100.0))
        gap_atr = abs(o[i] - c[i - 1]) / max(atr[i], 1e-12)
        chase_atr = side * (o[i] - planned) / max(atr[i], 1e-12)
        overextended = (c[i - 1] - level > p.lp2_overextended_atr * atr[i]) if side == 1 else (level - c[i - 1] > p.lp2_overextended_atr * atr[i])
        regime_ok = True
        if p.ema_regime == "EMA200":
            regime_ok = (c[i] > ema200[i]) if side == 1 else (c[i] < ema200[i])
        elif p.ema_regime == "EMA50/200":
            regime_ok = (c[i] > ema50[i] > ema200[i]) if side == 1 else (c[i] < ema50[i] < ema200[i])
        rsi_ok = True if p.rsi_filter == "Off" else ((rsi[i] > 50) if side == 1 else (rsi[i] < 50))
        ok = score >= p.min_score and np.isfinite(stop) and qty >= 1 and gap_atr <= p.gap_max_atr and chase_atr <= p.chase_max_atr and not overextended and (not p.require_trigger or triggered) and regime_ok and rsi_ok
        trigger_evidence = dict(pattern_rule_evidence)
        trigger_evidence.update({
            "pattern_confirmation_valid": True,
            "entry_triggered": bool(triggered),
            "score_threshold_passed": bool(score >= p.min_score),
            "stop_valid": bool(np.isfinite(stop)),
            "quantity_valid": bool(qty >= 1),
            "gap_filter_passed": bool(gap_atr <= p.gap_max_atr),
            "chase_filter_passed": bool(chase_atr <= p.chase_max_atr),
            "overextended": bool(overextended),
            "overextended_filter_passed": bool(not overextended),
            "trigger_requirement_passed": bool(not p.require_trigger or triggered),
            "regime_ok": bool(regime_ok),
            "rsi_ok": bool(rsi_ok),
            "gap_atr": float(gap_atr),
            "chase_atr": float(chase_atr),
        })
        rec.update({
            "signal": bool(ok), "candidate": True, "pattern": name, "side": side,
            "level": level, "level_type": level_type, "planned_entry": planned,
            "stop": stop, "target": target, "qty": float(qty),
            "triggered": bool(triggered), "score": score, "gap_atr": gap_atr,
            "chase_atr": chase_atr, "overextended": overextended,
            "regime_ok": regime_ok, "rsi_ok": rsi_ok,
            "trend_exit": bool(p.trend_exit and c[i] < ema50[i]),
            "signal_date": out.index[i], "signal_bar_index": i,
            "pattern_bar_indices": pattern_bar_indices,
            "pattern_rule_evidence": pattern_rule_evidence,
            "trigger_evidence": trigger_evidence,
        })
        if not ok:
            reasons = []
            if score < p.min_score: reasons.append("score")
            if qty < 1: reasons.append("qty")
            if gap_atr > p.gap_max_atr: reasons.append("gap")
            if chase_atr > p.chase_max_atr: reasons.append("chase")
            if overextended: reasons.append("overextended")
            if p.require_trigger and not triggered: reasons.append("no_trigger")
            if not regime_ok: reasons.append("regime")
            if not rsi_ok: reasons.append("rsi")
            rec["reject"] = ",".join(reasons)
            rec["rejection_evidence"] = [_REJECTION_EVIDENCE[reason] for reason in reasons]
        records.append(rec)
    signals = pd.DataFrame(records, index=out.index)
    if p.trend_exit:
        signals["trend_exit"] = (out["Close"].to_numpy(float) < ema50)
    return pd.concat([out, signals, pd.DataFrame({"atr": atr, "ema50": ema50, "ema200": ema200, "rsi": rsi}, index=out.index)], axis=1)


def _settle(position: Trade, price: float, date: pd.Timestamp, cash: float) -> tuple[float, Trade]:
    rate = COMMISSION_PCT / 100.0
    value = position.entry_qty * price
    exit_commission = value * rate
    if position.direction == "long":
        gross = position.entry_qty * (price - position.entry_price)
        cash += value - exit_commission
    else:
        gross = position.entry_qty * (position.entry_price - price)
        cash += gross - exit_commission
    position.exit_date = date
    position.exit_price = price
    position.exit_commission = exit_commission
    position.pnl = gross - position.entry_commission - exit_commission
    position.pnl_pct = position.pnl / (position.entry_qty * position.entry_price) * 100.0
    return cash, position


def _hit(open_p: float, high: float, low: float, side: str, target: float, stop: float) -> tuple[float | None, str]:
    if side == "long":
        if open_p >= target: return open_p, "target"
        if open_p <= stop: return open_p, "stop"
        tp, sl = high >= target, low <= stop
        if tp and sl:
            return (target, "target") if (high - open_p) <= (open_p - low) else (stop, "stop")
        if tp: return target, "target"
        if sl: return stop, "stop"
    else:
        if open_p <= target: return open_p, "target"
        if open_p >= stop: return open_p, "stop"
        tp, sl = low <= target, high >= stop
        if tp and sl:
            return (target, "target") if (open_p - low) <= (high - open_p) else (stop, "stop")
        if tp: return target, "target"
        if sl: return stop, "stop"
    return None, ""


def backtest(df: pd.DataFrame, p: Params = BASELINE, start: str = START_DATE, end: str = END_DATE) -> tuple[dict, pd.DataFrame]:
    """Run the stop-order simulation and return engine-compatible KPIs + signal audit."""
    x = prepare(df, p)
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    cash = INITIAL_CAPITAL
    position: Trade | None = None
    stop = target = np.nan
    pending: dict | None = None
    pending_close = False
    trades: list[Trade] = []
    equity_rows: list[dict] = []
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    max_dd_pct = 0.0
    rate = COMMISSION_PCT / 100.0
    for i, (date, row) in enumerate(x.iterrows()):
        in_range = start_ts <= date <= end_ts
        op, hi, lo, cl = map(float, (row.Open, row.High, row.Low, row.Close))
        if pending_close and position is not None:
            cash, closed = _settle(position, op, date, cash)
            trades.append(closed)
            position = None
            pending_close = False
            stop = target = np.nan
        if pending is not None and position is None:
            side = pending["side"]
            fills = op >= pending["planned_entry"] if side == 1 else op <= pending["planned_entry"]
            crosses = hi >= pending["planned_entry"] if side == 1 else lo <= pending["planned_entry"]
            if fills or crosses:
                fill = op if fills else pending["planned_entry"]
                value = pending["qty"] * fill
                entry_commission = value * rate
                position = Trade(entry_date=date, entry_price=fill, entry_qty=pending["qty"], direction="long" if side == 1 else "short", entry_commission=entry_commission)
                position.pattern = pending["pattern"]
                position.signal_date = pending["signal_date"]
                if side == 1: cash -= value + entry_commission
                else: cash -= entry_commission
                stop, target = pending["stop"], pending["target"]
                pending = None
        if position is not None and in_range:
            fill, _ = _hit(op, hi, lo, position.direction, target, stop)
            if fill is not None:
                cash, closed = _settle(position, fill, date, cash)
                trades.append(closed)
                position = None
                stop = target = np.nan
        if position is not None:
            if position.direction == "long":
                worst_equity = cash + position.entry_qty * lo
                equity = cash + position.entry_qty * cl
            else:
                worst_equity = cash + position.entry_qty * (position.entry_price - hi)
                equity = cash + position.entry_qty * (position.entry_price - cl)
            dd = worst_equity - peak
            dd_pct = dd / peak * 100.0 if peak else 0.0
            max_dd, max_dd_pct = min(max_dd, dd), min(max_dd_pct, dd_pct)
        else:
            equity = cash
            if in_range:
                peak = max(peak, equity)
        if in_range:
            equity_rows.append({"date": date, "equity": equity})

        # Pine evaluates the signal at this bar's close; orders activate next bar.
        if in_range and position is not None and position.direction == "long" and p.trend_exit and date != position.entry_date and bool(row.trend_exit):
            pending_close = True
        if in_range and bool(row.signal):
            if position is not None and p.close_on_opposite and ((position.direction == "long" and row.side == -1) or (position.direction == "short" and row.side == 1)):
                pending_close = True
            if position is None or ((position.direction == "long" and row.side == -1) or (position.direction == "short" and row.side == 1)):
                pending = {"side": int(row.side), "planned_entry": float(row.planned_entry), "stop": float(row.stop), "target": float(row.target), "qty": float(row.qty), "pattern": str(row.pattern), "signal_date": date}
        # A same-direction signal while an order is pending updates the order.
    for position in ([] if position is None else [position]):
        # Keep open trades open, as TradingView does; compute_kpis reports open P&L.
        trades.append(position)
    equity_df = pd.DataFrame(equity_rows).set_index("date") if equity_rows else pd.DataFrame(columns=["equity"])
    cfg = type("Config", (), {"initial_capital": INITIAL_CAPITAL})()
    kpis = compute_kpis(trades, equity_df, cfg, max_dd, max_dd_pct)
    return kpis, x


def _summary(k: dict, label: str) -> dict:
    return {"variant": label, "trades": k.get("total_trades", 0), "net_profit_pct": k.get("net_profit_pct", 0.0), "total_pnl_pct": k.get("total_pnl_pct", 0.0), "max_drawdown_pct": k.get("max_drawdown_pct", 0.0), "profit_factor": k.get("profit_factor", 0.0), "win_rate": k.get("win_rate", 0.0), "first_order": k.get("first_order_date"), "last_order": k.get("last_order_date")}


def _print_trade_analysis(k: dict, x: pd.DataFrame) -> None:
    closed = [t for t in k["trades"] if t.exit_date is not None]
    if not closed:
        print("No closed trades to analyze.")
        return
    rows = []
    for t in closed:
        rows.append({"entry": t.entry_date, "exit": t.exit_date, "dir": t.direction, "pnl": t.pnl, "pnl_pct": t.pnl_pct, "pattern": getattr(t, "pattern", "")})
    trades = pd.DataFrame(rows)
    print("\nBaseline closed trades by pattern/direction:")
    print(trades.groupby(["pattern", "dir"]).agg(trades=("pnl", "size"), profit=("pnl", "sum"), avg_pct=("pnl_pct", "mean"), wins=("pnl", lambda s: int((s > 0).sum()))).to_string())
    print("\nBaseline losses by pattern/direction:")
    print(trades[trades.pnl <= 0].groupby(["pattern", "dir"]).agg(losses=("pnl", "size"), loss_dollars=("pnl", "sum"), avg_loss=("pnl", "mean")).to_string())
    candidates = x[x.get("candidate", False) == True].copy()  # noqa: E712
    if not candidates.empty:
        print("\nRejected candidate reasons:")
        print(candidates[candidates.signal == False]["reject"].str.split(",").explode().value_counts().to_string())  # noqa: E712
        missed = []
        close = x["Close"]
        for date, r in candidates[candidates.signal == False].iterrows():  # noqa: E712
            j = x.index.get_loc(date)
            if j + 10 < len(x):
                fwd = (close.iloc[j + 10] / close.iloc[j] - 1.0) * (1 if r.side == 1 else -1) * 100.0
                missed.append({"pattern": r.pattern, "side": r.side, "reject": r.reject, "fwd10_pct": fwd})
        if missed:
            md = pd.DataFrame(missed)
            print("\nRejected-candidate forward 10-bar outcome by reason:")
            print(md.groupby("reject").agg(count=("fwd10_pct", "size"), mean_fwd10=("fwd10_pct", "mean"), win_rate=("fwd10_pct", lambda s: (s > 0).mean() * 100)).sort_values("mean_fwd10", ascending=False).head(20).to_string())


def main() -> None:
    # fetch_tv cache files already use the engine's normalized OHLCV schema.
    # load_tv_export is for TradingView's manual export schema, so read this
    # cached fetch directly while still using the engine's Trade/KPI routines.
    df = pd.read_csv(DATA_FILE, index_col=0, parse_dates=True)
    df.index.name = "Date"
    df = df.sort_index()
    selected = replace(BASELINE, entry_atr_pct=0.50, prb2=False, rr=2.5)
    variants = [
        ("Long-only baseline", BASELINE),
        ("Long-only, PRB2 disabled", selected),
    ]
    print("ASND 1D | long-only | TradingView data: 2015-01-28 to 2026-07-28 | warmup retained | commission 0.1% | slippage 0")
    results = []
    cache = {}
    for label, p in variants:
        k, x = backtest(df, p)
        cache[label] = (k, x)
        results.append(_summary(k, label))
        print(f"{label:<24} trades={k.get('total_trades', 0):>3} net={k.get('net_profit_pct', 0.0):>8.2f}% total={k.get('total_pnl_pct', 0.0):>8.2f}% DD={k.get('max_drawdown_pct', 0.0):>8.2f}% PF={k.get('profit_factor', 0.0):>6.2f}")
    baseline_k, baseline_x = cache["Long-only baseline"]
    _print_trade_analysis(baseline_k, baseline_x)
    result_df = pd.DataFrame(results)
    sensitivity_rows = []
    for label, p in [
        ("PRB2 enabled", replace(selected, prb2=True)), ("PRB2 disabled", selected),
        ("Entry offset 0.40", replace(selected, entry_atr_pct=0.40)), ("Entry offset 0.50", replace(selected, entry_atr_pct=0.50)), ("Entry offset 0.60", replace(selected, entry_atr_pct=0.60)),
        ("Target R 1.5", replace(selected, rr=1.5)), ("Target R 2.0", replace(selected, rr=2.0)), ("Target R 2.5", replace(selected, rr=2.5)),
    ]:
        k, _ = backtest(df, p)
        sensitivity_rows.append(_summary(k, label))
    sensitivity_df = pd.DataFrame(sensitivity_rows)

    validation_rows = []
    periods = [
        ("IS 2018-2022", "2018-01-01", "2022-12-31"),
        ("OOS 2023-present", "2023-01-01", END_DATE),
        ("Fold 2018-2020", "2018-01-01", "2020-12-31"),
        ("Fold 2021-2023", "2021-01-01", "2023-12-31"),
        ("Fold 2024-present", "2024-01-01", END_DATE),
    ]
    for label, p in [("Long-only baseline", BASELINE), ("Long-only, PRB2 disabled", selected)]:
        for period, start, end in periods:
            k, _ = backtest(df, p, start=start, end=end)
            validation_rows.append({"variant": label, "period": period, **_summary(k, label)})
    validation_df = pd.DataFrame(validation_rows)

    top20_rows = []
    missing = []
    for symbol in TOP20:
        matches = list((ROOT / "data" / "cache").glob(f"tv_*_{symbol}_1D_5000.csv"))
        if not matches:
            missing.append(symbol)
            continue
        symbol_df = pd.read_csv(matches[0], index_col=0, parse_dates=True).sort_index()
        base_k, _ = backtest(symbol_df, BASELINE)
        selected_k, _ = backtest(symbol_df, selected)
        top20_rows.append({
            "symbol": symbol,
            "data_start": symbol_df.index[0],
            "data_end": symbol_df.index[-1],
            "baseline_trades": base_k.get("total_trades", 0),
            "baseline_net_profit_pct": base_k.get("net_profit_pct", 0.0),
            "baseline_max_drawdown_pct": base_k.get("max_drawdown_pct", 0.0),
            "selected_trades": selected_k.get("total_trades", 0),
            "selected_net_profit_pct": selected_k.get("net_profit_pct", 0.0),
            "selected_max_drawdown_pct": selected_k.get("max_drawdown_pct", 0.0),
            "selected_profit_factor": selected_k.get("profit_factor", 0.0),
        })
    top20_df = pd.DataFrame(top20_rows)
    RESEARCH_DIR.mkdir(exist_ok=True)
    result_df.to_csv(RESEARCH_DIR / "market_screener_asnd_1d_variants.csv", index=False)
    sensitivity_df.to_csv(RESEARCH_DIR / "market_screener_asnd_1d_sensitivity.csv", index=False)
    validation_df.to_csv(RESEARCH_DIR / "market_screener_asnd_1d_validation.csv", index=False)
    top20_df.to_csv(RESEARCH_DIR / "market_screener_top20_long_only.csv", index=False)
    baseline_x.to_csv(RESEARCH_DIR / "market_screener_asnd_1d_signal_audit.csv")
    print(f"\nSaved {RESEARCH_DIR / 'market_screener_asnd_1d_variants.csv'}")
    print(f"Saved {RESEARCH_DIR / 'market_screener_asnd_1d_sensitivity.csv'}")
    print(f"Saved {RESEARCH_DIR / 'market_screener_asnd_1d_validation.csv'}")
    print(f"Saved {RESEARCH_DIR / 'market_screener_top20_long_only.csv'}")
    print(f"Saved {RESEARCH_DIR / 'market_screener_asnd_1d_signal_audit.csv'}")
    if not top20_df.empty:
        print("\nTop-20 long-only aggregate:")
        print("  Symbols tested:       %d/%d" % (len(top20_df), len(TOP20)))
        print("  Baseline mean/median: %.2f%% / %.2f%%" % (top20_df.baseline_net_profit_pct.mean(), top20_df.baseline_net_profit_pct.median()))
        print("  Selected mean/median: %.2f%% / %.2f%%" % (top20_df.selected_net_profit_pct.mean(), top20_df.selected_net_profit_pct.median()))
        print("  Positive symbols:     %d/%d" % ((top20_df.selected_net_profit_pct > 0).sum(), len(top20_df)))
        print("  Missing symbols:       %s" % (", ".join(missing) if missing else "none"))


if __name__ == "__main__":
    main()
