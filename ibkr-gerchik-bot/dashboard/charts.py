"""Plotly chart builders for the dashboard."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

DASHBOARD_CHARTS_VERSION = 2

# Luminous Obsidian palette (see stitch_trading_bot_dashboard/DESIGN.md)
UP = "#c3f400"           # secondary-fixed / lime — bullish
DOWN = "#ff6b81"         # bearish (error family, tuned for contrast)
TRADE_LEVEL = "#00f0ff"  # primary-container / cyan
RAW_LEVEL = "#849495"    # outline
ZONE_FILL = "rgba(0, 240, 255, 0.08)"
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


def _zone(level: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    price = level.get("price")
    try:
        low = float(level.get("zone_low", price))
        high = float(level.get("zone_high", price))
        return min(low, high), max(low, high)
    except (TypeError, ValueError):
        return None, None


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
                    opacity=0.35,
                    name="Volume",
                    hovertemplate="%{y:,.0f}<extra>Volume</extra>",
                ),
                row=2,
                col=1,
            )

    raw_levels = raw_levels or []
    trade_levels = trade_levels or []
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
        for level in trade_levels:
            low, high = _zone(level)
            if low is None or high is None:
                continue
            try:
                price = float(level.get("price"))
            except (TypeError, ValueError):
                price = (low + high) / 2
            if low != high:
                fig.add_hrect(
                    y0=low,
                    y1=high,
                    fillcolor=ZONE_FILL,
                    line_width=0,
                    layer="below",
                    row=1,
                    col=1,
                )
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
                line=dict(color=TRADE_LEVEL, width=1.5),
                annotation_text=label,
                annotation_position="right",
                annotation_font_color=TRADE_LEVEL,
                annotation_font_size=10,
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
        title=dict(text=title, font=dict(size=15, color="#e5e2e3", family=TITLE_FONT)),
        height=height,
        xaxis_rangeslider_visible=False,
        margin=dict(l=8, r=88, t=44, b=8),
        showlegend=False,
        paper_bgcolor=PAPER,
        plot_bgcolor=PLOT,
        font=dict(color="#b9cacb", family=FONT, size=11),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="#201f20", bordercolor="#3b494b", font=dict(family=FONT, color="#e5e2e3")
        ),
    )
    fig.update_xaxes(showgrid=False, rangeslider_visible=False, linecolor=GRID)
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False)
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
