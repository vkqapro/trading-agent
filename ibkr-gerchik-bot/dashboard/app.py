"""Luminous Obsidian — read-only operations dashboard for Vitaly's Trading Bot.

A professional, glassmorphic trading control surface built on the design system
in ``stitch_trading_bot_dashboard``. It only reads the files the bot writes under
``memory/``; it never connects to IBKR and never mutates state.
"""

from __future__ import annotations

import html
import sys
from collections import Counter
from dataclasses import replace
from datetime import datetime
from importlib import reload
from inspect import signature
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from dashboard import charts, components as ui, data_access as da, forecast as fc
from src.config import SETTINGS
from src.execution import order_requests as oq

# Streamlit keeps imported modules alive between reruns. Validate modules before
# binding individual helpers so a stale module cannot fail during ``from import``.
_COMPONENT_API = (
    "apply_theme", "bias_card", "blocked_news_panel", "feature_card", "fmt",
    "hero", "html_table", "human_age", "market_session", "metric_card_html",
    "metric_grid", "minibar", "operations_header", "panel_header", "show_df",
    "sidebar_brand", "sidebar_safety", "signal_pill", "source_health_bar",
    "stat_grid_card", "status_pill", "topbar",
)
if (
    not all(hasattr(ui, name) for name in _COMPONENT_API)
    or getattr(ui, "DASHBOARD_COMPONENTS_VERSION", 0) < 8
):
    ui = reload(ui)

apply_theme = ui.apply_theme
bias_card = ui.bias_card
blocked_news_panel = ui.blocked_news_panel
feature_card = ui.feature_card
fmt = ui.fmt
hero = ui.hero
html_table = ui.html_table
human_age = ui.human_age
market_session = ui.market_session
metric_card_html = ui.metric_card_html
metric_grid = ui.metric_grid
minibar = ui.minibar
operations_header = ui.operations_header
panel_header = ui.panel_header
show_df = ui.show_df
sidebar_brand = ui.sidebar_brand
sidebar_safety = ui.sidebar_safety
signal_pill = ui.signal_pill
source_health_bar = ui.source_health_bar
stat_grid_card = ui.stat_grid_card
status_pill = ui.status_pill
topbar = ui.topbar

_DATA_ACCESS_API = (
    "source_health", "clear_caches", "all_decision_attempts", "list_report_info",
    "blocked_news_summary", "load_order_requests",
)
if not all(hasattr(da, name) for name in _DATA_ACCESS_API) or getattr(
    da, "DASHBOARD_DATA_ACCESS_VERSION", 0
) < 5:
    da = reload(da)

_FORECAST_API = (
    "ForecastScenario", "projected_level_candidates",
    "replay_active_candidates", "run_forecast",
)
if (
    not all(hasattr(fc, name) for name in _FORECAST_API)
    or getattr(fc, "FORECAST_ENGINE_VERSION", 0) < 6
):
    fc = reload(fc)

_CHART_API = (
    "candles_with_levels", "mark_levels", "mark_forecast_position",
    "forecast_rr_scatter", "capital_gauges", "forecast_sensitivity",
)
_CHART_ARGUMENTS = {"show_raw", "show_trade", "show_volume"}
try:
    _chart_parameters = set(signature(charts.candles_with_levels).parameters)
except (AttributeError, TypeError, ValueError):
    _chart_parameters = set()
if (
    not all(hasattr(charts, name) for name in _CHART_API)
    or not _CHART_ARGUMENTS.issubset(_chart_parameters)
    or getattr(charts, "DASHBOARD_CHARTS_VERSION", 0) < 5
):
    charts = reload(charts)

# Reload a cached order-request module that predates the current schema/API
# (heartbeat helpers, quantity-carrying requests) so submitted orders match the
# forecast and the worker-status check works.
if (
    not all(hasattr(oq, name) for name in ("worker_age_seconds", "worker_is_alive", "submit_place"))
    or getattr(oq, "ORDER_REQUESTS_VERSION", 0) < 2
):
    oq = reload(oq)

CHART_CONFIG = {
    "displaylogo": False,
    "scrollZoom": True,
    "doubleClick": "reset+autosize",
    "modeBarButtonsToRemove": [
        "select2d", "lasso2d", "autoScale2d", "toggleSpikelines",
    ],
    "toImageButtonOptions": {
        "format": "png",
        "filename": "trading-chart",
        "scale": 2,
    },
}

st.set_page_config(
    page_title="Vitaly's Trading Bot · Luminous Obsidian",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_theme()


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _render_guard(name: str, renderer: Callable[[], None]) -> None:
    try:
        renderer()
    except Exception as exc:  # never let one panel take down the dashboard
        st.error(f"{name} could not be rendered: {exc}")
        with st.expander("Technical details"):
            st.exception(exc)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _symbol_summary(symbol: str, plan: dict[str, Any]) -> dict[str, Any]:
    spacing = _as_dict(plan.get("level_spacing"))
    levels = _as_list(plan.get("levels"))
    blocked = bool(plan.get("news_blocked"))
    return {
        "Symbol": symbol,
        "Status": "BLOCKED" if blocked else ("READY" if levels else "NO LEVELS"),
        "Price": spacing.get("current_price"),
        "Daily ATR": plan.get("daily_atr"),
        "Technical ATR": plan.get("technical_atr"),
        "Trade Levels": len(levels),
        "Room": spacing.get("reason"),
    }


def _level_frame(levels: list[dict[str, Any]]) -> pd.DataFrame:
    if not levels:
        return pd.DataFrame()
    frame = pd.DataFrame(levels)
    preferred = [
        "price", "zone_low", "zone_high", "type", "touches",
        "false_breakouts", "strength_score", "families",
    ]
    return frame[[column for column in preferred if column in frame.columns]]


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


# --------------------------------------------------------------------------- #
# Navigation + header
# --------------------------------------------------------------------------- #
PAGE_META = {
    "Dashboard": ("Command Overview", "Systems, portfolio constraints, opportunities, and recent decisions."),
    "Pre-Open": ("Pre-Open Briefing", "Watchlist quality, mapped levels, news risk, and opening opportunities."),
    "Intraday": ("Intraday Monitor", "Session decisions, attempted levels, and execution context."),
    "Trades & Positions": ("Trades & Positions", "Paper-order requests, tracked positions, and the trade journal."),
    "Forecast": ("Risk Forecast Calculator", "Scenario-only replay of saved market data and conditional setups."),
    "Reports": ("Reports", "Generated session workbooks and trading records."),
}
_NAV_ICONS = {
    "Dashboard": "space_dashboard",
    "Pre-Open": "wb_twilight",
    "Intraday": "monitoring",
    "Trades & Positions": "swap_horiz",
    "Forecast": "calculate",
    "Reports": "description",
}


def render_navigation() -> str:
    with st.sidebar:
        sidebar_brand(dry_run=SETTINGS.dry_run_mode, paper=SETTINGS.paper_trading)
        pages = list(PAGE_META)
        labels = [
            f":material/{_NAV_ICONS.get(p, 'circle')}: {p}"
            for p in pages
        ]
        idx = st.radio(
            "Operations",
            range(len(pages)),
            format_func=lambda i: labels[i],
            label_visibility="collapsed",
            key="dashboard_page",
        )
        selected = pages[idx]
        sidebar_safety(dry_run=SETTINGS.dry_run_mode, paper=SETTINGS.paper_trading)
    return selected


def render_header(page: str) -> dict[str, Any]:
    now = datetime.now(ZoneInfo(SETTINGS.trading_hours.timezone))
    session, session_detail = market_session(now)
    health = da.source_health()
    state_health = next((item for item in health if item.name == "Runtime state"), None)
    data_age = human_age(state_health.age_seconds) if state_health else "unknown"
    online = session in {"OPEN", "PRE-MARKET", "AFTER-HOURS"}

    title, subtitle = PAGE_META[page]
    left, right = st.columns([8, 1])
    with left:
        operations_header(
            page,
            clock=now.strftime("%H:%M:%S"),
            session=session,
            paper=SETTINGS.paper_trading,
            online=online,
            title=title,
            subtitle=(
                f"{subtitle} · {session_detail} · data {data_age} · "
                f"{len(da.load_watchlist())} symbols"
            ),
        )
    with right:
        st.write("")
        st.write("")
        if st.button("Refresh", icon=":material/refresh:", width="stretch"):
            da.clear_caches()
            st.rerun()

    # Order action feedback (Place/Close from any tab) shown at the top so it is
    # visible regardless of which tab is active when the action fires.
    feedback = st.session_state.pop("order_feedback", None)
    if feedback:
        st.success(feedback)

    source_health_bar(health)
    degraded = [item for item in health if item.status in {"stale", "missing"}]
    if degraded:
        names = ", ".join(f"{item.name} ({item.status})" for item in degraded)
        st.warning(f"Data health needs attention: {names}. Values may be incomplete or outdated.")
    with st.expander("Data source diagnostics"):
        show_df(
            pd.DataFrame(
                [
                    {
                        "Source": item.name,
                        "Status": item.status.upper(),
                        "Updated": item.updated_at.strftime("%Y-%m-%d %H:%M:%S %Z")
                        if item.updated_at else "-",
                        "Age (min)": round(item.age_seconds / 60, 1)
                        if item.age_seconds is not None else None,
                        "Size (MB)": round(item.size_bytes / 1_048_576, 2),
                        "Path": str(item.path),
                    }
                    for item in health
                ]
            )
        )
    return {"now": now, "session": session}


# --------------------------------------------------------------------------- #
# Dashboard (overview) tab
# --------------------------------------------------------------------------- #
def render_dashboard() -> None:
    watchlist = da.load_watchlist()
    decisions = da.load_daily_decisions()
    attempts = da.all_decision_attempts(decisions)
    positions = da.load_tracked_positions()
    snapshot = da.load_intraday_snapshot()
    executed = _as_list(snapshot.get("executed"))
    skipped = _as_list(snapshot.get("skipped"))

    signals = Counter(str(item.get("signal", "NONE")).upper() for item in attempts)
    buys, sells = signals["BUY"], signals["SELL"]
    total_attempts = max(1, len(attempts))
    tradable = sum(
        1 for plan in watchlist.values()
        if isinstance(plan, dict) and plan.get("levels") and not plan.get("news_blocked")
    )
    blocked = sum(1 for plan in watchlist.values() if isinstance(plan, dict) and plan.get("news_blocked"))
    fill_total = len(executed) + len(skipped)
    fill_rate = len(executed) / fill_total if fill_total else 0.0
    confidences = [
        float(a.get("confidence") or 0)
        for a in attempts
        if str(a.get("signal", "")).upper() in {"BUY", "SELL"}
    ]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

    news_items = da.blocked_news_summary(watchlist)
    cards = [
        {"label": "Watchlist", "value": len(watchlist), "icon": "visibility",
         "sub": f"{len(watchlist)} symbols tracked"},
        {"label": "Trade-Ready", "value": tradable, "icon": "model_training",
         "accent": "lime", "sub": "conditions met"},
        {"label": "News Blocked", "value": blocked, "icon": "gpp_bad",
         "accent": "error" if blocked else "",
         "sub": "click to view ▾" if blocked else "risk filtered",
         "clickable": bool(blocked)},
        {"label": "Attempts Today", "value": len(attempts), "icon": "history",
         "sub": str(decisions.get("date", "-"))},
        {"label": "Action Signals", "value": buys + sells, "icon": "bolt",
         "accent": "cyan", "sub": f"{buys} buy / {sells} sell",
         "sub_dir": "up" if buys >= sells else "down"},
        {"label": "Execution Rate", "value": f"{fill_rate:.0%}", "icon": "swap_horiz",
         "progress": fill_rate, "progress_label": f"{len(executed)}/{fill_total} filled"},
    ]
    st.session_state.setdefault("show_news", False)
    blocks = []
    for card in cards:
        blocks.append(metric_card_html(card))
    st.markdown(f'<div class="lx-grid c4">{"".join(blocks)}</div>', unsafe_allow_html=True)

    if cards[2].get("clickable"):  # News-blocked card
        if st.button("view news detail", key="news_toggle"):
            st.session_state.show_news = not st.session_state.show_news

    if st.session_state.show_news and news_items:
        with st.container(border=True):
            head_left, head_right = st.columns([6, 1])
            head_left.markdown("**🛡 Symbols filtered out by the news-risk filter**")
            if head_right.button("Close", key="news_close"):
                st.session_state.show_news = False
                st.rerun()
            blocked_news_panel(news_items)

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.markdown(
            bias_card(
                "Decision Summary",
                [
                    ("Buy bias", 100 * buys / total_attempts, "lime"),
                    ("Sell bias", 100 * sells / total_attempts, "error"),
                    ("No-trade / wait", 100 * signals["NONE"] / total_attempts, "muted"),
                ],
            ),
            unsafe_allow_html=True,
        )
    with col_b:
        risk = SETTINGS.risk
        st.markdown(
            stat_grid_card(
                "Risk Management",
                [
                    ("Risk / trade", _pct(risk.risk_per_trade), ""),
                    ("Max daily loss", _pct(risk.max_daily_loss_pct), "lime"),
                    ("Min reward:risk", f"1 : {risk.min_reward_risk_ratio:g}", ""),
                    ("Max positions", str(risk.max_positions), ""),
                    ("Max spread", _pct(risk.max_spread_pct), ""),
                    ("Max open risk", _pct(risk.max_open_risk_pct), "cyan"),
                ],
            ),
            unsafe_allow_html=True,
        )
    with col_c:
        ratio = tradable / max(1, len(watchlist))
        st.markdown(
            feature_card(
                "Trade-Ready Ratio",
                f"{ratio:.0%}",
                "cyan",
                "of watchlist",
                footer=("Avg signal confidence", f"{avg_conf:.2f}" if confidences else "-", "lime"),
            ),
            unsafe_allow_html=True,
        )

    # Main chart
    panel_header("Daily Market Structure", icon="candlestick_chart", badge="LEVEL ENGINE ACTIVE")
    if watchlist:
        symbols = sorted(watchlist.keys())
        attempts_by_symbol = Counter(a.get("symbol") for a in attempts)
        default = max(symbols, key=lambda s: attempts_by_symbol.get(s, 0)) if attempts else symbols[0]
        chosen = st.selectbox(
            "Symbol", symbols, index=symbols.index(default), key="dash_symbol",
            label_visibility="collapsed",
        )
        plan = _as_dict(watchlist.get(chosen))
        spacing = _as_dict(plan.get("level_spacing"))
        with st.container(border=True):
            fig = charts.candles_with_levels(
                da.get_bars(chosen, "daily"),
                raw_levels=_as_list(plan.get("raw_levels")),
                trade_levels=_as_list(plan.get("levels")),
                current_price=spacing.get("current_price"),
                title=f"{chosen} · Daily structure",
                height=420,
                show_raw=False,
                show_trade=True,
                show_volume=True,
            )
            st.plotly_chart(fig, width="stretch", config=CHART_CONFIG)
    else:
        st.info("No watchlist available yet. Run the premarket job to populate it.")

    # Opportunity queue
    panel_header("Opportunity Queue", icon="queue_play_next")
    queue_rows = []
    for symbol, plan in sorted(watchlist.items()):
        plan = _as_dict(plan)
        summary = _symbol_summary(symbol, plan)
        levels = summary["Trade Levels"]
        dot = "#c3f400" if summary["Status"] == "READY" else "#ff6b81" if summary["Status"] == "BLOCKED" else "#849495"
        queue_rows.append(
            [
                f'<span class="lx-dot" style="background:{dot}"></span>{html.escape(symbol)}',
                status_pill(summary["Status"]),
                f'{minibar(min(100, levels * 25), "cyan")} {levels}',
                html.escape(str(summary["Room"] or "-")),
                fmt(summary["Daily ATR"]),
                f'${fmt(summary["Price"])}' if summary["Price"] is not None else "-",
            ]
        )
    html_table(
        ["Asset", "Status", "Trade Levels", "Clean Room", "Daily ATR", "Last Price"],
        queue_rows[:25],
    )

    # Attempt log
    panel_header("Recent Attempt Log", icon="receipt_long")
    recent = sorted(attempts, key=lambda a: str(a.get("timestamp", "")), reverse=True)[:14]
    log_rows = []
    for a in recent:
        reason = a.get("reason")
        reason = "; ".join(map(str, reason)) if isinstance(reason, list) else str(reason or "-")
        log_rows.append(
            [
                html.escape(str(a.get("timestamp", "-")).replace("T", " ")),
                html.escape(str(a.get("symbol", "-"))),
                html.escape(str(a.get("strategy", "-"))),
                signal_pill(a.get("signal")),
                fmt(a.get("level")),
                f'<span style="color:#849495">{html.escape(reason)}</span>',
            ]
        )
    html_table(["Time", "Asset", "Strategy", "Signal", "Level", "Reason"], log_rows)


# --------------------------------------------------------------------------- #
# Pre-Open tab
# --------------------------------------------------------------------------- #
def render_preopen() -> None:
    watchlist = da.load_watchlist()
    if not watchlist:
        st.info("No pre-market watchlist is available. Run the premarket job first.")
        return

    all_rows = pd.DataFrame([_symbol_summary(s, _as_dict(p)) for s, p in sorted(watchlist.items())])
    filter_a, filter_b, filter_c = st.columns([2, 1, 1])
    search = filter_a.text_input("Find ticker", placeholder="AAPL", key="pre_search").strip().upper()
    status = filter_b.selectbox("Status", ["All", "Ready", "Blocked", "No levels"], key="pre_status")
    min_levels = filter_c.number_input("Minimum trade levels", 0, 20, 0, key="pre_levels")

    overview = all_rows.copy()
    if search:
        overview = overview[overview["Symbol"].str.contains(search, regex=False)]
    if status != "All":
        overview = overview[overview["Status"] == status.upper()]
    overview = overview[overview["Trade Levels"] >= min_levels]

    list_col, chart_col = st.columns([1.05, 2.35])
    with list_col:
        panel_header("Opportunity Queue", icon="format_list_bulleted")
        show_df(
            overview, height=430,
            column_config={
                "Price": st.column_config.NumberColumn(format="$%.2f"),
                "Daily ATR": st.column_config.NumberColumn(format="%.2f"),
                "Technical ATR": st.column_config.NumberColumn(format="%.2f"),
            },
        )
        symbols = overview["Symbol"].tolist()
        if not symbols:
            st.info("No symbols match the current filters.")
            return
        selected = st.selectbox("Inspect ticker", symbols, key="preopen_symbol")

    plan = _as_dict(watchlist.get(selected))
    spacing = _as_dict(plan.get("level_spacing"))
    raw_levels = _as_list(plan.get("raw_levels"))
    trade_levels = _as_list(plan.get("levels"))
    current_price = spacing.get("current_price")

    with chart_col:
        metric_grid(
            [
                {"label": "Last Price", "value": fmt(current_price), "accent": "cyan"},
                {"label": "Daily ATR", "value": fmt(plan.get("daily_atr"))},
                {"label": "Technical ATR", "value": fmt(plan.get("technical_atr"))},
                {"label": "Trade Levels", "value": len(trade_levels)},
                {"label": "News", "value": "BLOCKED" if plan.get("news_blocked") else "CLEAR",
                 "accent": "error" if plan.get("news_blocked") else "lime"},
            ],
            columns=5,
        )
        control_a, control_b, control_c = st.columns(3)
        show_raw = control_a.toggle("Raw levels", value=False, key=f"raw_{selected}")
        show_trade = control_b.toggle("Trade zones", value=True, key=f"trade_{selected}")
        show_volume = control_c.toggle("Volume", value=True, key=f"volume_{selected}")
        bars = da.get_bars(selected, "daily")
        if bars.empty:
            st.warning(f"No valid daily bars are persisted for {selected}.")
        with st.container(border=True):
            fig = charts.candles_with_levels(
                bars, raw_levels=raw_levels, trade_levels=trade_levels,
                current_price=current_price, title=f"{selected} · Daily structure",
                height=520, show_raw=show_raw, show_trade=show_trade, show_volume=show_volume,
            )
            st.plotly_chart(fig, width="stretch", config=CHART_CONFIG)

    details_a, details_b, details_c = st.columns([1, 1.4, 1])
    with details_a:
        panel_header("Trade Context")
        show_df(
            pd.DataFrame(
                [
                    {"Field": "Clean room", "Value": spacing.get("reason", "-")},
                    {"Field": "News status", "Value": "Blocked" if plan.get("news_blocked") else "Clear"},
                    {"Field": "Security type", "Value": plan.get("security_type", "-")},
                    {"Field": "Raw levels", "Value": len(raw_levels)},
                ]
            )
        )
        earnings = _as_dict(plan.get("earnings_event"))
        if earnings:
            with st.expander("Earnings event"):
                st.json(earnings)
    with details_b:
        panel_header("Optimized Trade Levels")
        show_df(_level_frame(trade_levels))
    with details_c:
        panel_header("Risk Headlines")
        headlines = _as_list(plan.get("matched_headlines"))
        if headlines:
            for headline in headlines:
                st.markdown(f"- {headline}")
        else:
            st.caption("No matched risk headlines.")
        with st.expander(f"All detected levels ({len(raw_levels)})"):
            show_df(_level_frame(raw_levels))


# --------------------------------------------------------------------------- #
# Intraday tab
# --------------------------------------------------------------------------- #
def render_intraday() -> None:
    snapshot = da.load_intraday_snapshot()
    decisions = da.load_daily_decisions()
    decision_map = _as_dict(decisions.get("decisions"))
    symbols = sorted(decision_map)
    all_attempts = da.all_decision_attempts(decisions)
    signal_counts = Counter(str(item.get("signal", "NONE")).upper() for item in all_attempts)
    scans = _as_list(snapshot.get("scans"))
    latest_scan = scans[-1] if scans else {}

    metric_grid(
        [
            {"label": "Decision Date", "value": str(decisions.get("date", "-"))},
            {"label": "Symbols Evaluated", "value": len(symbols)},
            {"label": "Attempts", "value": len(all_attempts), "accent": "cyan"},
            {"label": "Buy / Sell", "value": f"{signal_counts['BUY']} / {signal_counts['SELL']}",
             "accent": "lime"},
            {"label": "Latest Scan Signals", "value": str(latest_scan.get("signals_detected", "-"))},
        ],
        columns=5,
    )

    if not symbols:
        st.info("No intraday decisions have been recorded for the current session.")
        return

    selector_a, selector_b = st.columns([1, 2])
    symbol = selector_a.selectbox("Ticker", symbols, key="intraday_symbol")
    attempts = da.decisions_for_symbol(symbol, decisions)
    frame = da.decisions_to_frame(attempts)
    available_signals = (
        sorted(frame["signal"].dropna().astype(str).unique().tolist()) if not frame.empty else []
    )
    chosen_signals = selector_b.multiselect(
        "Signals", available_signals, default=available_signals, key="intraday_signals"
    )

    plan = _as_dict(da.load_watchlist().get(symbol))
    bars = da.get_bars(symbol, "intraday_5m")
    timeframe = "5 minute"
    if bars.empty:
        bars = da.get_bars(symbol, "intraday_15m")
        timeframe = "15 minute"

    chart_col, timeline_col = st.columns([2.25, 1])
    with chart_col:
        if bars.empty:
            st.warning(f"No valid intraday bars are persisted for {symbol}.")
        with st.container(border=True):
            fig = charts.candles_with_levels(
                bars, raw_levels=_as_list(plan.get("raw_levels")),
                trade_levels=_as_list(plan.get("levels")),
                title=f"{symbol} · {timeframe} execution view",
                height=500, show_raw=False, show_trade=True, show_volume=True,
            )
            charts.mark_levels(
                fig, [a.get("level") for a in attempts if a.get("level") is not None]
            )
            st.plotly_chart(fig, width="stretch", config=CHART_CONFIG)
    with timeline_col:
        panel_header("Decision Summary")
        summary = (
            frame.groupby(["signal", "strategy"], dropna=False).size()
            .reset_index(name="Attempts").sort_values("Attempts", ascending=False)
            if not frame.empty else pd.DataFrame()
        )
        show_df(summary)
        st.caption("Magenta dashed lines mark unique levels evaluated this session.")

    panel_header(f"Attempt Log · {symbol}", icon="receipt_long")
    filtered = frame
    if chosen_signals:
        filtered = frame[frame["signal"].astype(str).isin(chosen_signals)]
    show_df(
        filtered, height=420,
        column_config={
            "entry": st.column_config.NumberColumn(format="$%.2f"),
            "stop": st.column_config.NumberColumn(format="$%.2f"),
            "target": st.column_config.NumberColumn(format="$%.2f"),
            "confidence": st.column_config.NumberColumn(format="%.2f"),
        },
    )


# --------------------------------------------------------------------------- #
# Trades & positions tab
# --------------------------------------------------------------------------- #
def _order_request_frame(requests: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for request in requests:
        result = request.get("result") if isinstance(request.get("result"), dict) else {}
        rows.append({
            "Time": str(request.get("updated_at", "")).replace("T", " "),
            "Action": str(request.get("action", "")).upper(),
            "Symbol": request.get("symbol"),
            "Side": request.get("signal"),
            "Mode": "LIVE" if request.get("live") else "AUTO",
            "Status": str(request.get("status", "")).upper(),
            "Detail": request.get("message"),
            "Order ID": result.get("market_order_id") or result.get("order_id"),
        })
    return pd.DataFrame(rows)


def render_trades() -> None:
    positions = da.load_tracked_positions()
    snapshot = da.load_intraday_snapshot()
    executed = _as_list(snapshot.get("executed"))
    skipped = _as_list(snapshot.get("skipped"))
    fill_total = len(executed) + len(skipped)
    requests = da.load_order_requests()
    open_requests = sum(1 for r in requests if str(r.get("status")) in {"pending", "processing"})

    metric_grid(
        [
            {"label": "Open Tracked Positions", "value": len(positions), "accent": "cyan"},
            {"label": "Executed (latest run)", "value": len(executed), "accent": "lime"},
            {"label": "Skipped (latest run)", "value": len(skipped)},
            {"label": "Pending Order Requests", "value": open_requests,
             "accent": "error" if open_requests else ""},
        ],
        columns=4,
    )

    panel_header("Dashboard Order Requests", icon="send", badge="PAPER" if SETTINGS.paper_trading else "LIVE BLOCKED")
    worker_age = oq.worker_age_seconds()
    if oq.worker_is_alive():
        st.success(f"Execute worker online · last heartbeat {int(worker_age)}s ago.")
    else:
        last = f"last heartbeat {int(worker_age)}s ago" if worker_age is not None else "no heartbeat found"
        st.warning(
            f"Execute worker offline ({last}). Requests stay PENDING until it runs. "
            "Start it via run_dashboard.cmd, and make sure only one worker window is open."
        )
    if st.button("⟳ Refresh order status", key="orders_refresh"):
        da.clear_caches()
        st.rerun()
    if requests:
        show_df(
            _order_request_frame(requests), height=240,
            empty="No order requests have been submitted from the dashboard.",
        )
        st.caption(
            "Submitted from the Forecast tab. Statuses: PENDING/PROCESSING (worker is "
            "handling it), DONE (submitted to paper), SIMULATED (DRY_RUN), REJECTED "
            "(risk checks), ERROR. Run the worker: `python -m src.main --job execute_requests`."
        )
    else:
        st.caption(
            "No order requests yet. Use Place Order / Close Position on the Forecast "
            "tab, and run the bot worker: `python -m src.main --job execute_requests`."
        )

    panel_header(
        "Tracked Positions", icon="account_balance_wallet",
        badge="PAPER" if SETTINGS.paper_trading else "LIVE BLOCKED",
    )
    if not positions:
        st.caption("No tracked positions are present in runtime state.")
    else:
        weights = [0.8, 0.6, 0.8, 0.8, 0.8, 0.6, 0.9, 1.4, 1.1]
        labels = ["Symbol", "Qty", "Entry", "Stop", "Target", "Side", "Source", "Opened", ""]
        header = st.columns(weights)
        for col, label in zip(header, labels):
            if label:
                col.markdown(f"**:gray[{label}]**")
        for index, pos in enumerate(positions):
            symbol = str(pos.get("symbol", "")).upper()
            side = str(pos.get("side", "")).upper()
            cols = st.columns(weights)
            cols[0].markdown(f"**{symbol}**")
            cols[1].markdown(f"{pos.get('quantity', '-')}")
            cols[2].markdown(f"${fmt(pos.get('entry'))}")
            cols[3].markdown(f"${fmt(pos.get('stop_loss'))}")
            cols[4].markdown(f"${fmt(pos.get('target'))}")
            cols[5].markdown(
                f":green[{side}]" if side == "BUY" else (f":red[{side}]" if side == "SELL" else side or "-")
            )
            cols[6].markdown(f"{pos.get('source', '-')}")
            cols[7].markdown(f"{str(pos.get('opened_at', '-')).replace('T', ' ')}")
            if cols[8].button("Close Position", key=f"closepos_{index}_{symbol}", width="stretch",
                              help=f"Flatten the open {symbol} position via a market order"):
                _confirm_close_position(symbol)
        with st.expander("Full positions table (all columns)"):
            show_df(pd.DataFrame(positions))

    executed_col, skipped_col = st.columns(2)
    with executed_col:
        panel_header("Executed Candidates", icon="task_alt")
        show_df(pd.DataFrame(executed), empty="No candidates were executed in the latest run.")
    with skipped_col:
        panel_header("Skipped Candidates", icon="block")
        show_df(pd.DataFrame(skipped), empty="No candidates were skipped in the latest run.")

    panel_header("Recent Trade Journal", icon="history")
    sections = da.latest_trade_log_sections(limit=12)
    if not sections:
        st.caption("No trade log sections are available.")
    for section in sections:
        with st.expander(section["heading"]):
            st.code(section["body"] or "(empty)", language="markdown")


# --------------------------------------------------------------------------- #
# Forecast calculator tab
# --------------------------------------------------------------------------- #
def _forecast_defaults() -> dict[str, float | int]:
    risk = SETTINGS.risk
    return {
        "forecast_equity": 100_000.0,
        "forecast_cash": 100_000.0,
        "forecast_daily_pnl": 0.0,
        "forecast_assumed_spread": min(0.15, risk.max_spread_pct * 100),
        "forecast_risk_trade": risk.risk_per_trade * 100,
        "forecast_daily_loss": risk.max_daily_loss_pct * 100,
        "forecast_min_rr": risk.min_reward_risk_ratio,
        "forecast_max_positions": risk.max_positions,
        "forecast_max_spread": risk.max_spread_pct * 100,
        "forecast_open_risk": risk.max_open_risk_pct * 100,
        "forecast_max_pos_value": float(risk.max_position_value),
        "forecast_min_entry_atr": 1.0,
        "forecast_max_entry_atr": 2.0,
    }


def _reset_forecast_inputs() -> None:
    for key, value in _forecast_defaults().items():
        st.session_state[key] = value
    st.session_state.pop("forecast_result", None)


def _forecast_result_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    preferred = [
        "symbol", "source", "strategy", "signal", "current_price", "entry",
        "entry_distance_pct", "entry_distance_atr", "stop", "target",
        "reward_risk", "quantity", "position_value", "risk_amount",
        "potential_reward", "status", "reason",
    ]
    frame = frame[[column for column in preferred if column in frame.columns]]
    if "entry_distance_pct" in frame:
        frame["entry_distance_pct"] = frame["entry_distance_pct"] * 100
    return frame.rename(
        columns={
            "symbol": "Symbol",
            "source": "Source",
            "strategy": "Strategy",
            "signal": "Side",
            "current_price": "Current",
            "entry": "Entry",
            "entry_distance_pct": "Distance %",
            "entry_distance_atr": "Distance ATR",
            "stop": "Stop",
            "target": "Target",
            "reward_risk": "R:R",
            "quantity": "Shares",
            "position_value": "Position Value",
            "risk_amount": "Risk $",
            "potential_reward": "Potential Reward",
            "status": "Status",
            "reason": "Decision",
        }
    )


def _forecast_trade_identity(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("symbol"),
        row.get("strategy"),
        row.get("signal"),
        row.get("entry"),
        row.get("stop"),
        row.get("target"),
    )


def _order_mode_notice() -> None:
    """Surface the bot's safety posture inside an order confirmation dialog."""
    if not SETTINGS.paper_trading:
        st.error(
            "PAPER_TRADING is disabled — the bot will refuse this order. "
            "Enable paper trading in the bot before submitting."
        )
    elif SETTINGS.dry_run_mode:
        st.info(
            "DRY_RUN_MODE is on: this records a simulated fill unless you enable "
            "live submission below."
        )
    else:
        st.info("The bot will submit a live order to the paper account.")


@st.dialog("Confirm order placement")
def _confirm_place_order(setup: dict[str, Any]) -> None:
    symbol = str(setup.get("symbol", "")).upper()
    st.markdown(f"### {symbol} · {setup.get('signal')}")
    show_df(
        pd.DataFrame([{
            "Entry": setup.get("entry"), "Stop": setup.get("stop"),
            "Target": setup.get("target"), "R:R": setup.get("reward_risk"),
            "Strategy": setup.get("strategy"),
        }])
    )
    _order_mode_notice()
    live = st.toggle(
        "Submit live to the paper account",
        value=False, key="confirm_place_live",
        help="Off respects DRY_RUN (simulated when DRY_RUN_MODE is on). "
             "On submits a live order to the paper account.",
    )
    send, cancel = st.columns(2)
    if send.button("Send to bot", type="primary", width="stretch",
                   disabled=not SETTINGS.paper_trading, key="confirm_place_send"):
        request_id = oq.submit_place(setup, live=live)
        st.session_state.order_feedback = (
            f"Queued place order for {symbol} (request {request_id}). "
            "The bot worker will execute it; watch Trades & Positions."
        )
        st.rerun()
    if cancel.button("Cancel", width="stretch", key="confirm_place_cancel"):
        st.rerun()


@st.dialog("Confirm close position")
def _confirm_close_position(symbol: str) -> None:
    symbol = str(symbol).upper()
    st.markdown(f"### Close {symbol}")
    st.write(f"Flatten the open position in **{symbol}** with a market order.")
    _order_mode_notice()
    live = st.toggle(
        "Submit live to the paper account", value=False, key="confirm_close_live",
        help="Off respects DRY_RUN. On submits a live closing order to the paper account.",
    )
    send, cancel = st.columns(2)
    if send.button("Close position", type="primary", width="stretch",
                   disabled=not SETTINGS.paper_trading, key="confirm_close_send"):
        request_id = oq.submit_close(symbol, live=live)
        st.session_state.order_feedback = (
            f"Queued close for {symbol} (request {request_id})."
        )
        st.rerun()
    if cancel.button("Cancel", width="stretch", key="confirm_close_cancel"):
        st.rerun()


def _render_forecast_position_chart(row: dict[str, Any]) -> None:
    symbol = str(row.get("symbol", "")).upper()
    if not symbol:
        return
    plan = _as_dict(da.load_watchlist().get(symbol))
    spacing = _as_dict(plan.get("level_spacing"))
    bars = da.get_bars(symbol, "daily")
    timeframe_label = "Daily"
    if bars.empty:
        bars = da.get_bars(symbol, "intraday_5m")
        timeframe_label = "Intraday 5m"
    if bars.empty:
        st.info(f"No saved chart data is available for {symbol}.")
        return

    side = str(row.get("signal", "")).upper()
    panel_header(
        f"{symbol} Forecast Position",
        icon="candlestick_chart",
        badge=f"{side} · {row.get('source', 'FORECAST')}",
    )
    control_a, control_b, control_c = st.columns(3)
    show_raw = control_a.toggle(
        "Raw levels", value=False, key=f"forecast_raw_{symbol}"
    )
    show_trade = control_b.toggle(
        "Trade zones", value=True, key=f"forecast_trade_{symbol}"
    )
    show_volume = control_c.toggle(
        "Volume", value=True, key=f"forecast_volume_{symbol}"
    )
    with st.container(border=True):
        fig = charts.candles_with_levels(
            bars,
            raw_levels=_as_list(plan.get("raw_levels")),
            trade_levels=_as_list(plan.get("levels")),
            current_price=spacing.get("current_price"),
            title=f"{symbol} · {timeframe_label} forecast position",
            height=540,
            show_raw=show_raw,
            show_trade=show_trade,
            show_volume=show_volume,
        )
        charts.mark_forecast_position(
            fig,
            entry=float(row.get("entry", 0) or 0),
            stop=float(row.get("stop", 0) or 0),
            target=float(row.get("target", 0) or 0),
        )
        st.plotly_chart(fig, width="stretch", config=CHART_CONFIG)


def render_forecast() -> None:
    for key, value in _forecast_defaults().items():
        st.session_state.setdefault(key, value)

    panel_header("Risk Forecast Calculator", icon="calculate", badge="SCENARIO ONLY")
    st.markdown(
        """
        <div class="lx-scenario-note">
          <span class="material-symbols-outlined">shield_lock</span>
          <span>This calculator replays saved market data and models conditional level setups.
          It does not change live bot settings, connect to IBKR, or place orders.</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.form("forecast_calculator"):
        account_a, account_b, account_c, account_d = st.columns(4)
        account_equity = account_a.number_input(
            "Account equity ($)", min_value=1_000.0, step=5_000.0,
            key="forecast_equity",
        )
        cash_available = account_b.number_input(
            "Available cash ($)", min_value=0.0, step=5_000.0,
            key="forecast_cash",
        )
        daily_realized_pnl = account_c.number_input(
            "Daily realized P&L ($)", step=100.0,
            key="forecast_daily_pnl",
        )
        assumed_spread = account_d.number_input(
            "Assumed spread (%)", min_value=0.0, max_value=10.0, step=0.01,
            format="%.2f", key="forecast_assumed_spread",
        )

        risk_a, risk_b, risk_c = st.columns(3)
        risk_per_trade = risk_a.number_input(
            "Risk / trade (%)", min_value=0.01, max_value=10.0, step=0.05,
            format="%.2f", key="forecast_risk_trade",
        )
        max_daily_loss = risk_b.number_input(
            "Max daily loss (%)", min_value=0.01, max_value=25.0, step=0.10,
            format="%.2f", key="forecast_daily_loss",
        )
        min_reward_risk = risk_c.number_input(
            "Minimum reward:risk", min_value=0.5, max_value=10.0, step=0.25,
            format="%.2f", key="forecast_min_rr",
        )

        limit_a, limit_b, limit_c = st.columns(3)
        max_positions = limit_a.number_input(
            "Maximum positions", min_value=1, max_value=50, step=1,
            key="forecast_max_positions",
        )
        max_spread = limit_b.number_input(
            "Maximum spread (%)", min_value=0.01, max_value=10.0, step=0.01,
            format="%.2f", key="forecast_max_spread",
        )
        max_open_risk = limit_c.number_input(
            "Maximum open risk (%)", min_value=0.01, max_value=25.0, step=0.10,
            format="%.2f", key="forecast_open_risk",
        )

        value_col, distance_a, distance_b = st.columns(3)
        max_pos_value = value_col.number_input(
            "Max position value ($)", min_value=100.0, step=1_000.0,
            format="%.2f", key="forecast_max_pos_value",
            help="Largest notional allowed per position in this scenario.",
        )
        min_entry_atr = distance_a.number_input(
            "Minimum entry distance (ATR)",
            min_value=0.0, max_value=20.0, step=0.25,
            format="%.2f", key="forecast_min_entry_atr",
            help="Projected entries closer than this distance are filtered.",
        )
        max_entry_atr = distance_b.number_input(
            "Maximum entry distance (ATR)",
            min_value=0.0, max_value=20.0, step=0.25,
            format="%.2f", key="forecast_max_entry_atr",
            help="Projected entries farther than this distance are filtered.",
        )
        calculate = st.form_submit_button(
            "Calculate forecast", icon=":material/query_stats:", width="stretch",
        )

    st.button(
        "Reset to live settings",
        icon=":material/restart_alt:",
        on_click=_reset_forecast_inputs,
        key="forecast_reset",
    )

    if calculate:
        if max_entry_atr < min_entry_atr:
            st.error("Maximum entry distance must be greater than or equal to the minimum.")
            return
        scenario = fc.ForecastScenario(
            account_equity=float(account_equity),
            cash_available=float(cash_available),
            daily_realized_pnl=float(daily_realized_pnl),
            assumed_spread_pct=float(assumed_spread) / 100,
            risk_per_trade=float(risk_per_trade) / 100,
            max_daily_loss_pct=float(max_daily_loss) / 100,
            min_reward_risk_ratio=float(min_reward_risk),
            max_positions=int(max_positions),
            max_spread_pct=float(max_spread) / 100,
            max_open_risk_pct=float(max_open_risk) / 100,
            max_position_value=float(max_pos_value),
            min_projected_entry_distance_atr=float(min_entry_atr),
            max_projected_entry_distance_atr=float(max_entry_atr),
        )
        live_scenario = fc.ForecastScenario(
            account_equity=float(account_equity),
            cash_available=float(cash_available),
            daily_realized_pnl=float(daily_realized_pnl),
            assumed_spread_pct=float(assumed_spread) / 100,
            risk_per_trade=SETTINGS.risk.risk_per_trade,
            max_daily_loss_pct=SETTINGS.risk.max_daily_loss_pct,
            min_reward_risk_ratio=SETTINGS.risk.min_reward_risk_ratio,
            max_positions=SETTINGS.risk.max_positions,
            max_spread_pct=SETTINGS.risk.max_spread_pct,
            max_open_risk_pct=SETTINGS.risk.max_open_risk_pct,
            max_position_value=SETTINGS.risk.max_position_value,
            min_projected_entry_distance_atr=float(min_entry_atr),
            max_projected_entry_distance_atr=float(max_entry_atr),
        )
        watchlist = da.load_watchlist()
        positions = da.load_tracked_positions()
        with st.spinner("Replaying saved bars and calculating risk allocation..."):
            active, warnings = fc.replay_active_candidates(watchlist, da.get_bars)
            projected = fc.projected_level_candidates(watchlist)
            seen: set[tuple[Any, ...]] = set()
            candidates: list[dict[str, Any]] = []
            for candidate in [*active, *projected]:
                identity = (
                    candidate.get("symbol"), candidate.get("strategy"),
                    candidate.get("signal"), candidate.get("entry"),
                    candidate.get("stop"), candidate.get("target"),
                )
                if identity not in seen:
                    seen.add(identity)
                    candidates.append(candidate)
            result = fc.run_forecast(candidates, scenario, positions)
            baseline = fc.run_forecast(candidates, live_scenario, positions)
            # Sensitivity sweep: how do trades / open risk respond to risk-per-trade?
            sweep = []
            for pct in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0):
                swept = fc.run_forecast(
                    candidates, replace(scenario, risk_per_trade=pct / 100), positions
                )
                sweep.append({
                    "risk_pct": pct,
                    "trades": swept["summary"]["forecast_trades"],
                    "open_risk": swept["summary"]["total_open_risk"],
                })
        st.session_state.forecast_result = {
            "result": result,
            "baseline": baseline,
            "warnings": warnings,
            "sweep": sweep,
            "live_risk_pct": float(risk_per_trade),
            "calculated_at": datetime.now(
                ZoneInfo(SETTINGS.trading_hours.timezone)
            ).isoformat(timespec="seconds"),
        }

    stored = st.session_state.get("forecast_result")
    if not isinstance(stored, dict):
        st.caption("Set the scenario parameters and calculate to generate a forecast.")
        return

    result = _as_dict(stored.get("result"))
    baseline = _as_dict(stored.get("baseline"))
    summary = _as_dict(result.get("summary"))
    baseline_summary = _as_dict(baseline.get("summary"))
    calculated_at = str(stored.get("calculated_at", "-")).replace("T", " ")

    panel_header("Forecast Summary", icon="query_stats", badge=calculated_at)
    metric_grid(
        [
            {"label": "ATR-Qualified Setups", "value": summary.get("setup_qualified", 0),
             "sub": f'of {summary.get("possible_signals", 0)} modeled signals'},
            {"label": "Forecast Trades", "value": summary.get("forecast_trades", 0),
             "accent": "lime",
             "sub": (
                 f'limited by {summary.get("binding_limit")}'
                 if summary.get("binding_limit")
                 else "eligible under scenario"
             )},
            {"label": "Capital Required", "value": f'${summary.get("capital_required", 0):,.0f}',
             "accent": "cyan", "sub": "forecast allocation"},
            {"label": "New Open Risk", "value": f'${summary.get("new_open_risk", 0):,.0f}',
             "sub": f'max ${summary.get("max_open_risk", 0):,.0f}'},
            {"label": "Max Daily Loss", "value": f'${summary.get("max_daily_loss", 0):,.0f}',
             "accent": "error", "sub": f'${summary.get("remaining_daily_loss", 0):,.0f} remaining'},
            {"label": "Potential Reward", "value": f'${summary.get("potential_reward", 0):,.0f}',
             "accent": "lime", "sub": "at modeled targets"},
        ],
        columns=6,
    )
    if summary.get("binding_limit"):
        st.info(
            f"{summary.get('setup_qualified', 0)} setups pass the signal and ATR filters, "
            f"but the portfolio receives {summary.get('forecast_trades', 0)} trades. "
            f"Binding constraint: {summary.get('binding_limit')}. "
            f"Risk budget per trade is ${summary.get('risk_budget_per_trade', 0):,.0f}; "
            f"the remaining open-risk capacity supports "
            f"{summary.get('open_risk_capacity', 0)} full-sized position(s), subject to "
            f"the {summary.get('position_capacity', 0)} available position slot(s)."
        )

    stale_count = int(summary.get("stale_signals", 0) or 0)
    if stale_count:
        st.warning(
            f"{stale_count} active-replay signal(s) were computed on stale saved bars "
            "(older than 24h). Treat those rows as indicative until fresh bars are captured."
        )

    all_rows = _as_list(result.get("rows"))
    scenario_dict = _as_dict(result.get("scenario"))
    gauge_col, scatter_col = st.columns([1, 1.4])
    with gauge_col:
        panel_header("Capital & Risk Utilization", icon="speed")
        with st.container(border=True):
            st.plotly_chart(
                charts.capital_gauges(summary), width="stretch", config=CHART_CONFIG
            )
    with scatter_col:
        panel_header("Setup Qualification Map", icon="scatter_plot")
        with st.container(border=True):
            st.plotly_chart(
                charts.forecast_rr_scatter(
                    all_rows,
                    min_reward_risk=float(scenario_dict.get("min_reward_risk_ratio") or 3.0),
                    min_entry_atr=float(scenario_dict.get("min_projected_entry_distance_atr") or 1.0),
                    max_entry_atr=float(scenario_dict.get("max_projected_entry_distance_atr") or 2.0),
                ),
                width="stretch", config=CHART_CONFIG,
            )

    panel_header("Scenario vs Live Settings", icon="compare_arrows")
    comparison = pd.DataFrame(
        [
            {
                "Metric": "Forecast trades",
                "Live settings": baseline_summary.get("forecast_trades", 0),
                "Scenario": summary.get("forecast_trades", 0),
                "Change": summary.get("forecast_trades", 0) - baseline_summary.get("forecast_trades", 0),
            },
            {
                "Metric": "Capital required",
                "Live settings": baseline_summary.get("capital_required", 0),
                "Scenario": summary.get("capital_required", 0),
                "Change": summary.get("capital_required", 0) - baseline_summary.get("capital_required", 0),
            },
            {
                "Metric": "New open risk",
                "Live settings": baseline_summary.get("new_open_risk", 0),
                "Scenario": summary.get("new_open_risk", 0),
                "Change": summary.get("new_open_risk", 0) - baseline_summary.get("new_open_risk", 0),
            },
            {
                "Metric": "Potential reward",
                "Live settings": baseline_summary.get("potential_reward", 0),
                "Scenario": summary.get("potential_reward", 0),
                "Change": summary.get("potential_reward", 0) - baseline_summary.get("potential_reward", 0),
            },
        ]
    )
    show_df(comparison, height=180)

    sweep = stored.get("sweep")
    if isinstance(sweep, list) and sweep:
        panel_header("Risk-per-Trade Sensitivity", icon="ssid_chart")
        with st.container(border=True):
            st.plotly_chart(
                charts.forecast_sensitivity(sweep, live_value=stored.get("live_risk_pct")),
                width="stretch", config=CHART_CONFIG,
            )

    panel_header(
        "Forecast Trade Ledger",
        icon="table_view",
        badge="PAPER" if SETTINGS.paper_trading else "LIVE BLOCKED",
    )
    st.caption(
        "Each row has Place Order (sends the setup to the execute_requests worker — "
        "paper-only, respects DRY_RUN) and Close Position (flattens the symbol). "
        "Results appear in Trades & Positions. Click a symbol to load its chart."
    )
    rows = result.get("rows", [])
    rows = rows if isinstance(rows, list) else []
    view = st.segmented_control(
        "Forecast rows",
        ["Eligible trades", "All signals", "Filtered only"],
        default="Eligible trades",
        label_visibility="collapsed",
        key="forecast_row_filter",
    )
    if view == "Eligible trades":
        visible = [row for row in rows if row.get("status") == "FORECAST TRADE"]
    elif view == "Filtered only":
        visible = [row for row in rows if row.get("status") == "FILTERED"]
    else:
        visible = rows

    selected_row = None
    if not visible:
        st.caption("No forecast rows match this view.")
    else:
        weights = [0.85, 0.6, 0.75, 0.75, 0.75, 0.55, 0.7, 0.95, 0.85, 0.9]
        labels = ["Symbol", "Side", "Entry", "Stop", "Target", "R:R", "Shares", "Status", "", ""]
        header = st.columns(weights)
        for col, label in zip(header, labels):
            if label:
                col.markdown(f"**:gray[{label}]**")
        for index, row in enumerate(visible[:50]):
            symbol = str(row.get("symbol", "")).upper()
            is_trade = row.get("status") == "FORECAST TRADE"
            side = str(row.get("signal", "")).upper()
            cols = st.columns(weights)
            if cols[0].button(symbol, key=f"sym_{index}_{symbol}", width="stretch",
                              help="Load this symbol's forecast chart below"):
                st.session_state.forecast_selected_trade = _forecast_trade_identity(row)
            cols[1].markdown(f":green[{side}]" if side == "BUY" else f":red[{side}]")
            cols[2].markdown(f"${fmt(row.get('entry'))}")
            cols[3].markdown(f"${fmt(row.get('stop'))}")
            cols[4].markdown(f"${fmt(row.get('target'))}")
            cols[5].markdown(f"{fmt(row.get('reward_risk'))}")
            cols[6].markdown(f"{row.get('quantity', '-')}")
            cols[7].markdown(":green[Eligible]" if is_trade else ":gray[Filtered]")
            if cols[8].button(
                "Place", key=f"place_{index}_{symbol}", disabled=not is_trade, width="stretch",
                help=None if is_trade else "Only setups that pass all scenario filters can be placed.",
            ):
                _confirm_place_order(row)
            if cols[9].button("Close", key=f"close_{index}_{symbol}", width="stretch",
                              help=f"Flatten any open {symbol} position"):
                _confirm_close_position(symbol)

        selected_identity = st.session_state.get("forecast_selected_trade")
        selected_row = next(
            (row for row in visible if _forecast_trade_identity(row) == selected_identity),
            visible[0],
        )
        st.session_state.forecast_selected_trade = _forecast_trade_identity(selected_row)

        with st.expander("Full ledger table (all columns, sortable)"):
            show_df(
                _forecast_result_frame(visible),
                height=300,
                column_config={
                    "Entry": st.column_config.NumberColumn(format="$%.2f"),
                    "Current": st.column_config.NumberColumn(format="$%.2f"),
                    "Distance %": st.column_config.NumberColumn(format="%.1f%%"),
                    "Distance ATR": st.column_config.NumberColumn(format="%.2f"),
                    "Stop": st.column_config.NumberColumn(format="$%.2f"),
                    "Target": st.column_config.NumberColumn(format="$%.2f"),
                    "Position Value": st.column_config.NumberColumn(format="$%.2f"),
                    "Risk $": st.column_config.NumberColumn(format="$%.2f"),
                    "Potential Reward": st.column_config.NumberColumn(format="$%.2f"),
                    "R:R": st.column_config.NumberColumn(format="%.2f"),
                },
            )

    if selected_row is not None:
        _render_forecast_position_chart(selected_row)

    warnings = stored.get("warnings")
    if isinstance(warnings, list) and warnings:
        with st.expander(f"Replay warnings ({len(warnings)})"):
            st.code("\n".join(map(str, warnings)))
    st.caption(
        "Conditional setups are modeled from saved levels and bars. Active-replay "
        "rows use saved intraday bars and are flagged when stale. Results are "
        "estimates, not orders or guarantees."
    )


# --------------------------------------------------------------------------- #
# Reports tab
# --------------------------------------------------------------------------- #
def render_reports() -> None:
    reports = da.list_report_info()
    if not reports:
        st.info("No Excel reports are available under memory/reports.")
        return

    kinds = sorted({item.kind for item in reports})
    dates = sorted({item.report_date for item in reports}, reverse=True)
    filter_a, filter_b = st.columns(2)
    kind = filter_a.selectbox("Report type", ["All"] + kinds, key="report_kind")
    date = filter_b.selectbox("Trading date", ["Latest"] + dates, key="report_date")
    filtered = [
        item for item in reports
        if (kind == "All" or item.kind == kind)
        and (date == "Latest" or item.report_date == date)
    ]
    if date == "Latest" and filtered:
        latest_date = filtered[0].report_date
        filtered = [item for item in filtered if item.report_date == latest_date]
    if not filtered:
        st.info("No reports match the selected type and date.")
        return

    labels = [
        f"{item.updated_at:%H:%M:%S} · {item.path.name} · {item.size_bytes / 1024:.0f} KB"
        for item in filtered
    ]
    choice = st.selectbox("Report run", labels, key="report_choice")
    selected = filtered[labels.index(choice)]
    frame = da.read_report(selected.path)

    metric_grid(
        [
            {"label": "Rows", "value": len(frame)},
            {"label": "Columns", "value": len(frame.columns)},
            {"label": "Updated", "value": selected.updated_at.strftime("%H:%M:%S"), "accent": "cyan"},
        ],
        columns=3,
    )

    if frame.empty:
        st.warning("The report is empty, locked, malformed, or could not be read.")
    else:
        ticker_column = next(
            (c for c in ("Ticker", "ticker", "Symbol", "symbol") if c in frame), None
        )
        if ticker_column:
            tickers = sorted(frame[ticker_column].dropna().astype(str).unique().tolist())
            selected_tickers = st.multiselect(
                "Filter tickers", tickers, default=[], placeholder="All tickers"
            )
            if selected_tickers:
                frame = frame[frame[ticker_column].astype(str).isin(selected_tickers)]
        show_df(frame, height=540)

    try:
        payload = selected.path.read_bytes()
    except OSError as exc:
        st.error(f"Report download is unavailable: {exc}")
    else:
        st.download_button(
            "Download selected report", payload, file_name=selected.path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


# --------------------------------------------------------------------------- #
# Compose
# --------------------------------------------------------------------------- #
page = render_navigation()
render_header(page)

RENDERERS: dict[str, Callable[[], None]] = {
    "Dashboard": render_dashboard,
    "Pre-Open": render_preopen,
    "Intraday": render_intraday,
    "Trades & Positions": render_trades,
    "Forecast": render_forecast,
    "Reports": render_reports,
}
_render_guard("Forecast Calculator" if page == "Forecast" else page, RENDERERS[page])
