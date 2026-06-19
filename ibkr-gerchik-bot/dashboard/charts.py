"""Plotly chart builders for the dashboard."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

DASHBOARD_CHARTS_VERSION = 3

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
PAPER = "#131314"
PLOT = "#131314"
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
        hovermode="x",
        hoverdistance=40,
        spikedistance=-1,
        dragmode="pan",
        newshape=dict(line_color=TRADE_LEVEL),
        uirevision="luminous-chart-v3",
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
            bgcolor="rgba(32,31,32,0.88)",
            activecolor="rgba(0,240,255,0.18)",
            bordercolor="rgba(132,148,149,0.18)",
            borderwidth=1,
            font=dict(color="#b9cacb", size=10, family=FONT),
        ),
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="rgba(132,148,149,0.075)",
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
    """Add labeled forecast entry, stop, and target lines to a price chart."""
    markers = (
        ("ENTRY", entry, FORECAST_ENTRY, "dash"),
        ("STOP", stop, FORECAST_STOP, "dash"),
        ("TARGET", target, FORECAST_TARGET, "dash"),
    )
    for label, raw_price, color, dash in markers:
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            continue
        fig.add_hline(
            y=price,
            line=dict(color=color, width=2, dash=dash),
            annotation_text=f"{label}  ${price:,.2f}",
            annotation_position="right",
            annotation_font_color="#ffffff",
            annotation_font_size=11,
            row=1,
            col=1,
        )
    return fig
