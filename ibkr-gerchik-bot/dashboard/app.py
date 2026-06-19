"""Luminous Obsidian — read-only operations dashboard for the Gerchik bot.

A professional, glassmorphic trading control surface built on the design system
in ``stitch_trading_bot_dashboard``. It only reads the files the bot writes under
``memory/``; it never connects to IBKR and never mutates state.
"""

from __future__ import annotations

import html
import sys
from collections import Counter
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

from dashboard import charts, components as ui, data_access as da
from src.config import SETTINGS

# Streamlit keeps imported modules alive between reruns. Validate modules before
# binding individual helpers so a stale module cannot fail during ``from import``.
_COMPONENT_API = (
    "apply_theme", "bias_card", "blocked_news_panel", "feature_card", "fmt",
    "hero", "html_table", "human_age", "market_session", "metric_card_html",
    "metric_grid", "minibar", "panel_header", "show_df", "signal_pill",
    "source_health_bar", "stat_grid_card", "status_pill", "topbar",
)
if (
    not all(hasattr(ui, name) for name in _COMPONENT_API)
    or getattr(ui, "DASHBOARD_COMPONENTS_VERSION", 0) < 5
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
panel_header = ui.panel_header
show_df = ui.show_df
signal_pill = ui.signal_pill
source_health_bar = ui.source_health_bar
stat_grid_card = ui.stat_grid_card
status_pill = ui.status_pill
topbar = ui.topbar

_DATA_ACCESS_API = (
    "source_health", "clear_caches", "all_decision_attempts", "list_report_info",
    "blocked_news_summary",
)
if not all(hasattr(da, name) for name in _DATA_ACCESS_API) or getattr(
    da, "DASHBOARD_DATA_ACCESS_VERSION", 0
) < 4:
    da = reload(da)

_CHART_ARGUMENTS = {"show_raw", "show_trade", "show_volume"}
try:
    _chart_parameters = set(signature(charts.candles_with_levels).parameters)
except (AttributeError, TypeError, ValueError):
    _chart_parameters = set()
if not _CHART_ARGUMENTS.issubset(_chart_parameters):
    charts = reload(charts)

st.set_page_config(
    page_title="Gerchik Bot · Luminous Obsidian",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
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
# Header
# --------------------------------------------------------------------------- #
def render_header() -> dict[str, Any]:
    now = datetime.now(ZoneInfo(SETTINGS.trading_hours.timezone))
    session, session_detail = market_session(now)
    health = da.source_health()
    state_health = next((item for item in health if item.name == "Runtime state"), None)
    data_age = human_age(state_health.age_seconds) if state_health else "unknown"
    online = session in {"OPEN", "PRE-MARKET", "AFTER-HOURS"}

    market_chip = ("check_circle", f"Market {session}", "ok" if session == "OPEN" else "")
    topbar(
        f"{'Active' if online else 'Idle'} · Gerchik level engine · "
        f"{'DRY-RUN' if SETTINGS.dry_run_mode else 'LIVE'}",
        [
            ("schedule", f"Data {data_age}", "cyan"),
            ("dns", f"{len(da.load_watchlist())} symbols", ""),
            market_chip,
        ],
    )

    left, right = st.columns([6, 1])
    with left:
        hero(
            "Unified Execution Engine",
            online,
            f"{now:%A, %B %d · %H:%M:%S %Z}  ·  {session} ({session_detail})  ·  Read-only view",
        )
    with right:
        st.write("")
        if st.button("⟳ Refresh", width="stretch"):
            da.clear_caches()
            st.rerun()

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
         "sub": "risk filtered"},
        {"label": "Attempts Today", "value": len(attempts), "icon": "history",
         "sub": str(decisions.get("date", "-"))},
        {"label": "Action Signals", "value": buys + sells, "icon": "bolt",
         "accent": "cyan", "sub": f"{buys} buy / {sells} sell",
         "sub_dir": "up" if buys >= sells else "down"},
        {"label": "Execution Rate", "value": f"{fill_rate:.0%}", "icon": "swap_horiz",
         "progress": fill_rate, "progress_label": f"{len(executed)}/{fill_total} filled"},
    ]
    columns = st.columns(6, gap="small")
    for column, card in zip(columns, cards):
        with column:
            st.markdown(metric_card_html(card), unsafe_allow_html=True)

    if blocked:
        with st.popover(
            f"News-blocked detail · {blocked} symbols",
            icon=":material/shield:",
            help="Review symbols excluded by the news-risk filter",
        ):
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
            st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
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
            st.plotly_chart(fig, width="stretch", config={"displaylogo": False})

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
            st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
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
def render_trades() -> None:
    positions = da.load_tracked_positions()
    snapshot = da.load_intraday_snapshot()
    executed = _as_list(snapshot.get("executed"))
    skipped = _as_list(snapshot.get("skipped"))
    fill_total = len(executed) + len(skipped)

    metric_grid(
        [
            {"label": "Open Tracked Positions", "value": len(positions), "accent": "cyan"},
            {"label": "Executed (latest run)", "value": len(executed), "accent": "lime"},
            {"label": "Skipped (latest run)", "value": len(skipped)},
            {"label": "Execution Rate", "value": f"{(len(executed) / max(1, fill_total)):.0%}",
             "progress": len(executed) / max(1, fill_total),
             "progress_label": f"{len(executed)}/{fill_total} filled"},
        ],
        columns=4,
    )

    panel_header("Tracked Positions", icon="account_balance_wallet")
    show_df(pd.DataFrame(positions), empty="No tracked positions are present in runtime state.")

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
render_header()
tab_dash, tab_preopen, tab_intraday, tab_trades, tab_reports = st.tabs(
    ["📊 Dashboard", "🌅 Pre-Open", "⚡ Intraday", "💼 Trades & Positions", "📑 Reports"]
)
with tab_dash:
    _render_guard("Dashboard", render_dashboard)
with tab_preopen:
    _render_guard("Pre-Open", render_preopen)
with tab_intraday:
    _render_guard("Intraday", render_intraday)
with tab_trades:
    _render_guard("Trades & Positions", render_trades)
with tab_reports:
    _render_guard("Reports", render_reports)
