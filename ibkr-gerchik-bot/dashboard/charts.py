"""Plotly chart builders for the dashboard."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

DASHBOARD_CHARTS_VERSION = 5

# Luminous Obsidian palette (see stitch_trading_bot_dashboard/DESIGN.md)
UP = "#c3f400"           # secondary-fixed / lime — bullish
DOWN = "#ff6b81"         # bearish (error family, tuned for contrast)
TRADE_LEVEL = "#00f0ff"  # primary-container / cyan
RAW_LEVEL = "#849495"    # outline
ZONE_FILL = "rgba(0, 240, 255, 0.08)"
ZONE_FILL_MUTED = "rgba(0, 240, 255, 0.025)"
PRICE_LINE = "#ffffff"   # last price marker (neutral)
ATTEMPT_LEVEL = "#ecb2ff"  # tertiary / magenta
FORECAST_ENTRY = "#ffffff"
FORECAST_STOP = "#ff6b81"
FORECAST_TARGET = "#c3f400"
GRID = "rgba(132, 148, 149, 0.10)"
PAPER = "rgba(0,0,0,0)"
PLOT = "rgba(0,0,0,0)"
FONT = "JetBrains Mono, monospace"
TITLE_FONT = "Hanken Grotesk, sans-serif"
MAX_LABELED_TRADE_LEVELS = 5


def _zone(level: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    price = level.get("price")
    try:
        low = float(level.get("zone_low", price))
        high = float(level.get("zone_high", price))
        return min(low, high), max(low, high)
    except (TypeError, ValueError):
        return None, None


def _level_price(level: Dict[str, Any]) -> Optional[float]:
    try:
        return float(level.get("price"))
    except (TypeError, ValueError):
        low, high = _zone(level)
        if low is None or high is None:
            return None
        return (low + high) / 2


def _focus_price(bars: pd.DataFrame, current_price: Optional[float]) -> Optional[float]:
    try:
        if current_price is not None:
            return float(current_price)
    except (TypeError, ValueError):
        pass
    if bars is not None and not bars.empty and "close" in bars:
        try:
            return float(bars.iloc[-1]["close"])
        except (TypeError, ValueError):
            return None
    return None


def _labeled_trade_level_ids(
    trade_levels: list[Dict[str, Any]],
    focus_price: Optional[float],
) -> set[int]:
    ranked: list[tuple[float, float, int]] = []
    for index, level in enumerate(trade_levels):
        price = _level_price(level)
        if price is None:
            continue
        strength = level.get("strength_score", level.get("strength", 0))
        try:
            strength_value = float(strength or 0)
        except (TypeError, ValueError):
            strength_value = 0.0
        distance = abs(price - focus_price) if focus_price is not None else 0.0
        ranked.append((distance, -strength_value, index))
    ranked.sort()
    return {index for _, _, index in ranked[:MAX_LABELED_TRADE_LEVELS]}


def candles_with_levels(
    bars: pd.DataFrame,
    raw_levels: Optional[list[Dict[str, Any]]] = None,
    trade_levels: Optional[list[Dict[str, Any]]] = None,
    *,
    current_price: Optional[float] = None,
    title: str = "",
    height: int = 600,
    show_raw: bool = True,
    show_trade: bool = True,
    show_volume: bool = True,
) -> go.Figure:
    has_volume = (
        show_volume
        and bars is not None
        and not bars.empty
        and "volume" in bars
        and bars["volume"].notna().any()
    )
    fig = make_subplots(
        rows=2 if has_volume else 1,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.78, 0.22] if has_volume else [1.0],
    )

    if bars is not None and not bars.empty:
        fig.add_trace(
            go.Candlestick(
                x=bars["date"],
                open=bars["open"],
                high=bars["high"],
                low=bars["low"],
                close=bars["close"],
                name="Price",
                increasing_line_color=UP,
                decreasing_line_color=DOWN,
                whiskerwidth=0.35,
                hovertext=[
                    (
                        f"<b>{date:%b %d, %Y}</b><br>"
                        f"Open&nbsp; ${open_:,.2f}<br>High&nbsp; ${high:,.2f}<br>"
                        f"Low&nbsp;&nbsp; ${low:,.2f}<br>Close ${close:,.2f}"
                    )
                    for date, open_, high, low, close in zip(
                        bars["date"], bars["open"], bars["high"], bars["low"], bars["close"]
                    )
                ],
                hoverinfo="text",
            ),
            row=1,
            col=1,
        )
        if has_volume:
            colors = [
                UP if close >= open_ else DOWN
                for open_, close in zip(bars["open"], bars["close"])
            ]
            fig.add_trace(
                go.Bar(
                    x=bars["date"],
                    y=bars["volume"],
                    marker_color=colors,
                    opacity=0.22,
                    name="Volume",
                    hovertemplate="%{y:,.0f}<extra>Volume</extra>",
                ),
                row=2,
                col=1,
            )

    raw_levels = raw_levels or []
    trade_levels = trade_levels or []
    focus_price = _focus_price(bars, current_price)
    labeled_level_ids = _labeled_trade_level_ids(trade_levels, focus_price)
    trade_prices: set[float] = set()
    for level in trade_levels:
        try:
            trade_prices.add(round(float(level.get("price")), 2))
        except (TypeError, ValueError):
            continue

    if show_raw:
        for level in raw_levels:
            try:
                price = float(level.get("price"))
            except (TypeError, ValueError):
                continue
            if round(price, 2) not in trade_prices:
                fig.add_hline(
                    y=price,
                    line=dict(color=RAW_LEVEL, width=1, dash="dot"),
                    opacity=0.45,
                    row=1,
                    col=1,
                )

    if show_trade:
        for index, level in enumerate(trade_levels):
            low, high = _zone(level)
            if low is None or high is None:
                continue
            price = _level_price(level)
            if price is None:
                continue
            is_labeled = index in labeled_level_ids
            if low != high:
                fig.add_hrect(
                    y0=low,
                    y1=high,
                    fillcolor=ZONE_FILL if is_labeled else ZONE_FILL_MUTED,
                    line_width=0,
                    layer="below",
                    row=1,
                    col=1,
                )
            if not is_labeled:
                continue
            details = []
            if level.get("touches") is not None:
                details.append(f"{level['touches']}T")
            strength = level.get("strength_score", level.get("strength"))
            try:
                if strength is not None:
                    details.append(f"S{float(strength):.0f}")
            except (TypeError, ValueError):
                pass
            label = f"{price:g}" + (f"  {' / '.join(details)}" if details else "")
            fig.add_hline(
                y=price,
                line=dict(color=TRADE_LEVEL, width=1.2),
                annotation_text=label,
                annotation_position="right",
                annotation_font_color=TRADE_LEVEL,
                annotation_font_size=10,
                opacity=0.82,
                row=1,
                col=1,
            )

    try:
        if current_price is not None:
            price = float(current_price)
            fig.add_hline(
                y=price,
                line=dict(color=PRICE_LINE, width=1.5, dash="dash"),
                annotation_text=f"Last {price:g}",
                annotation_position="left",
                annotation_font_color=PRICE_LINE,
                row=1,
                col=1,
            )
    except (TypeError, ValueError):
        pass

    fig.update_layout(
        title=dict(
            text=title,
            x=0.015,
            y=0.97,
            font=dict(size=16, color="#f5f3f4", family=TITLE_FONT),
        ),
        height=height,
        xaxis_rangeslider_visible=False,
        margin=dict(l=16, r=112, t=78, b=12),
        showlegend=False,
        paper_bgcolor=PAPER,
        plot_bgcolor=PLOT,
        font=dict(color="#b9cacb", family=FONT, size=11),
        hovermode="x unified",
        hoverdistance=40,
        spikedistance=-1,
        dragmode="pan",
        newshape=dict(line_color=TRADE_LEVEL),
        uirevision="operations-chart-v4",
        hoverlabel=dict(
            bgcolor="#18191b",
            bordercolor="#34383c",
            font=dict(family=FONT, color="#f5f3f4", size=11),
        ),
    )
    range_buttons = [
        dict(count=1, label="1M", step="month", stepmode="backward"),
        dict(count=3, label="3M", step="month", stepmode="backward"),
        dict(count=6, label="6M", step="month", stepmode="backward"),
        dict(count=1, label="YTD", step="year", stepmode="todate"),
        dict(count=1, label="1Y", step="year", stepmode="backward"),
        dict(label="ALL", step="all"),
    ]
    fig.update_xaxes(
        showgrid=False,
        rangeslider_visible=False,
        linecolor="rgba(132,148,149,0.14)",
        tickformat="%b\n%Y",
        showspikes=True,
        spikecolor="rgba(255,255,255,0.34)",
        spikethickness=1,
        spikedash="dot",
        spikesnap="cursor",
        rangeselector=dict(
            buttons=range_buttons,
            x=0,
            y=1.13,
            xanchor="left",
            yanchor="top",
            bgcolor="rgba(14,14,15,0.78)",
            activecolor="rgba(0,240,255,0.18)",
            bordercolor="rgba(132,148,149,0.18)",
            borderwidth=1,
            font=dict(color="#b9cacb", size=10, family=FONT),
        ),
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="rgba(132,148,149,0.065)",
        zeroline=False,
        side="right",
        tickprefix="$",
        tickformat=",.2f",
        showspikes=True,
        spikecolor="rgba(255,255,255,0.24)",
        spikethickness=1,
        spikedash="dot",
        fixedrange=False,
        row=1,
        col=1,
    )
    if has_volume:
        fig.update_yaxes(
            showgrid=False,
            side="right",
            tickformat="~s",
            fixedrange=False,
            row=2,
            col=1,
        )
    return fig


def mark_levels(
    fig: go.Figure, prices: Iterable[float], color: str = ATTEMPT_LEVEL
) -> go.Figure:
    seen: set[float] = set()
    for value in prices:
        try:
            price = round(float(value), 4)
        except (TypeError, ValueError):
            continue
        if price in seen:
            continue
        seen.add(price)
        fig.add_shape(
            type="line",
            x0=0,
            x1=1,
            xref="x domain",
            y0=price,
            y1=price,
            yref="y",
            line=dict(color=color, width=1, dash="dash"),
            opacity=0.75,
        )
    return fig


def mark_forecast_position(
    fig: go.Figure,
    *,
    entry: float,
    stop: float,
    target: float,
) -> go.Figure:
    """Shade the risk (entry→stop) and reward (entry→target) zones and label them.

    The risk box is red, the reward box is green, and the entry is a solid white
    line — an R-multiple view that reads far faster than three loose lines.
    """
    try:
        entry_f, stop_f, target_f = float(entry), float(stop), float(target)
    except (TypeError, ValueError):
        return fig
    risk = abs(entry_f - stop_f)
    reward = abs(target_f - entry_f)
    r_multiple = reward / risk if risk > 0 else 0.0

    if risk > 0:
        fig.add_hrect(
            y0=min(entry_f, stop_f), y1=max(entry_f, stop_f),
            fillcolor="rgba(255, 107, 129, 0.14)", line_width=0, layer="below", row=1, col=1,
        )
    if reward > 0:
        fig.add_hrect(
            y0=min(entry_f, target_f), y1=max(entry_f, target_f),
            fillcolor="rgba(195, 244, 0, 0.12)", line_width=0, layer="below", row=1, col=1,
        )
    markers = (
        (f"ENTRY  ${entry_f:,.2f}", entry_f, FORECAST_ENTRY, "dash"),
        (f"STOP  ${stop_f:,.2f}", stop_f, FORECAST_STOP, "dash"),
        (f"TARGET  ${target_f:,.2f}  ·  {r_multiple:.1f}R", target_f, FORECAST_TARGET, "dash"),
    )
    for label, price, color, dash in markers:
        fig.add_hline(
            y=price,
            line=dict(color=color, width=2, dash=dash),
            annotation_text=label,
            annotation_position="right",
            annotation_font_color=color,
            annotation_font_size=11,
            row=1, col=1,
        )
    return fig


def forecast_rr_scatter(
    rows: list[Dict[str, Any]],
    *,
    min_reward_risk: float,
    min_entry_atr: float,
    max_entry_atr: float,
    height: int = 360,
) -> go.Figure:
    """Reward:risk vs entry-distance (ATR) scatter, with qualifying zones shaded.

    Accepted trades are lime, filtered candidates are muted; bubble size encodes
    position value. The min-R:R line and the [min,max] ATR entry window are drawn
    so a glance shows which setups qualify and why the rest fall out.
    """
    fig = go.Figure()
    # Shaded ATR entry window (only meaningful where ATR distance is known).
    if max_entry_atr > min_entry_atr:
        fig.add_vrect(
            x0=min_entry_atr, x1=max_entry_atr,
            fillcolor="rgba(0, 240, 255, 0.07)", line_width=0, layer="below",
            annotation_text="entry window", annotation_position="top left",
            annotation_font=dict(color=TRADE_LEVEL, size=10),
        )
    fig.add_hline(
        y=min_reward_risk, line=dict(color=TRADE_LEVEL, width=1, dash="dot"),
        annotation_text=f"min R:R {min_reward_risk:g}", annotation_position="right",
        annotation_font=dict(color=TRADE_LEVEL, size=10),
    )

    def _series(accepted: bool):
        xs, ys, sizes, texts = [], [], [], []
        for row in rows:
            atr = row.get("entry_distance_atr")
            rr = row.get("reward_risk")
            if atr is None or rr is None:
                continue
            is_acc = row.get("status") == "FORECAST TRADE"
            if is_acc != accepted:
                continue
            xs.append(float(atr))
            ys.append(float(rr))
            sizes.append(max(6.0, min(40.0, (float(row.get("position_value") or 0) ** 0.5) / 6)))
            texts.append(
                f"{row.get('symbol')} · {row.get('signal')}<br>"
                f"R:R {float(rr):.2f} · {float(atr):.2f} ATR<br>"
                f"{row.get('reason', '')}"
            )
        return xs, ys, sizes, texts

    for accepted, color, name in ((False, RAW_LEVEL, "Filtered"), (True, UP, "Forecast trade")):
        xs, ys, sizes, texts = _series(accepted)
        if not xs:
            continue
        fig.add_trace(
            go.Scatter(
                x=xs, y=ys, mode="markers", name=name,
                marker=dict(size=sizes, color=color, opacity=0.85,
                            line=dict(width=1, color="#0e0e0f")),
                text=texts, hovertemplate="%{text}<extra></extra>",
            )
        )
    fig.update_layout(
        height=height, paper_bgcolor=PAPER, plot_bgcolor=PLOT,
        font=dict(color="#b9cacb", family=FONT, size=11),
        margin=dict(l=10, r=70, t=30, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
        xaxis_title="Entry distance (ATR)", yaxis_title="Reward : Risk",
        hoverlabel=dict(bgcolor="#201f20", bordercolor="#3b494b", font=dict(family=FONT)),
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False)
    return fig


def capital_gauges(summary: Dict[str, Any], height: int = 220) -> go.Figure:
    """Horizontal utilization bars: open risk, capital, and position slots."""
    new_risk = float(summary.get("new_open_risk", 0) or 0)
    total_risk = float(summary.get("total_open_risk", 0) or 0)
    max_risk = float(summary.get("max_open_risk", 0) or 0)
    capital = float(summary.get("capital_required", 0) or 0)
    cash_remaining = float(summary.get("cash_remaining", 0) or 0)
    cash_total = capital + cash_remaining
    trades = float(summary.get("forecast_trades", 0) or 0)
    pos_cap = float(summary.get("position_capacity", 0) or 0)
    binding = str(summary.get("binding_limit") or "")

    rows = [
        ("Position slots", trades, max(pos_cap, trades, 1), "maximum positions"),
        ("Capital", capital, max(cash_total, capital, 1), "cash / position value"),
        ("Open risk", total_risk, max(max_risk, total_risk, 1e-9), "maximum open risk"),
    ]
    fig = go.Figure()
    for label, used, total, binds in rows:
        pct = max(0.0, min(1.0, used / total)) * 100 if total else 0.0
        is_binding = binding == binds
        fig.add_trace(go.Bar(
            y=[label], x=[100], orientation="h", marker_color="#201f20",
            hoverinfo="skip", showlegend=False, width=0.55,
        ))
        fig.add_trace(go.Bar(
            y=[label], x=[pct], orientation="h",
            marker_color=("#ff6b81" if is_binding else TRADE_LEVEL),
            base=0, showlegend=False, width=0.55,
            text=[f"{used:,.0f} / {total:,.0f}"], textposition="inside",
            insidetextanchor="start", textfont=dict(family=FONT, color="#0e0e0f", size=11),
            hovertemplate=f"{label}: {pct:.0f}%<extra></extra>",
        ))
    fig.update_layout(
        barmode="overlay", height=height, paper_bgcolor=PAPER, plot_bgcolor=PLOT,
        font=dict(color="#b9cacb", family=FONT, size=11),
        margin=dict(l=10, r=20, t=10, b=20),
    )
    fig.update_xaxes(range=[0, 100], showgrid=False, ticksuffix="%", zeroline=False)
    fig.update_yaxes(showgrid=False)
    return fig


def forecast_sensitivity(
    points: list[Dict[str, Any]], *, live_value: Optional[float] = None, height: int = 320
) -> go.Figure:
    """Dual-axis sweep: forecast trades and total open risk vs risk-per-trade (%)."""
    xs = [float(p.get("risk_pct", 0)) for p in points]
    trades = [int(p.get("trades", 0)) for p in points]
    risk = [float(p.get("open_risk", 0)) for p in points]
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Scatter(x=xs, y=trades, mode="lines+markers", name="Forecast trades",
                   line=dict(color=TRADE_LEVEL, width=2), marker=dict(size=6)),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(x=xs, y=risk, mode="lines+markers", name="Total open risk $",
                   line=dict(color=UP, width=2, dash="dot"), marker=dict(size=5)),
        secondary_y=True,
    )
    if live_value is not None:
        fig.add_vline(
            x=float(live_value), line=dict(color="#ffffff", width=1, dash="dash"),
            annotation_text="live", annotation_position="top",
            annotation_font=dict(color="#ffffff", size=10),
        )
    fig.update_layout(
        height=height, paper_bgcolor=PAPER, plot_bgcolor=PLOT,
        font=dict(color="#b9cacb", family=FONT, size=11),
        margin=dict(l=10, r=10, t=30, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#201f20", bordercolor="#3b494b", font=dict(family=FONT)),
    )
    fig.update_xaxes(title_text="Risk per trade (%)", showgrid=True, gridcolor=GRID, zeroline=False)
    fig.update_yaxes(title_text="Trades", showgrid=True, gridcolor=GRID, zeroline=False, secondary_y=False)
    fig.update_yaxes(title_text="Open risk $", showgrid=False, zeroline=False, secondary_y=True)
    return fig
