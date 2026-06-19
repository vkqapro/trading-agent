"""Luminous Obsidian presentation system for the dashboard.

A glassmorphic dark design language (deep obsidian canvas, cyan / lime / magenta
accents, Hanken Grotesk + Inter + JetBrains Mono) implemented as reusable
Streamlit helpers. All values rendered here come from the read-only bot data
layer; nothing in this module mutates state.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterable, Sequence

import pandas as pd
import streamlit as st

if TYPE_CHECKING:
    from dashboard.data_access import SourceHealth


# --------------------------------------------------------------------------- #
# Palette (mirrors stitch_trading_bot_dashboard/DESIGN.md — "Luminous Obsidian")
# --------------------------------------------------------------------------- #
BG = "#131314"
SURFACE_LOWEST = "#0e0e0f"
SURFACE_LOW = "#1c1b1c"
SURFACE = "#201f20"
SURFACE_HIGH = "#2a2a2b"
SURFACE_VARIANT = "#353436"
OUTLINE = "#849495"
OUTLINE_VARIANT = "#3b494b"
ON_SURFACE = "#e5e2e3"
ON_SURFACE_VARIANT = "#b9cacb"
CYAN = "#7df4ff"            # primary-fixed
CYAN_BRIGHT = "#00f0ff"     # primary-container
CYAN_DIM = "#00dbe9"        # primary-fixed-dim
LIME = "#c3f400"            # secondary-fixed
LIME_DIM = "#abd600"
MAGENTA = "#ecb2ff"         # tertiary-fixed-dim
WHITE = "#ffffff"
ERROR = "#ffb4ab"

ACCENTS = {
    "cyan": CYAN,
    "lime": LIME,
    "magenta": MAGENTA,
    "white": WHITE,
    "error": ERROR,
    "muted": OUTLINE,
}


CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=swap');

:root {{
    --bg: {BG}; --surface: {SURFACE}; --surface-low: {SURFACE_LOW};
    --surface-lowest: {SURFACE_LOWEST}; --surface-high: {SURFACE_HIGH};
    --outline: {OUTLINE}; --outline-variant: {OUTLINE_VARIANT};
    --on-surface: {ON_SURFACE}; --on-variant: {ON_SURFACE_VARIANT};
    --cyan: {CYAN}; --lime: {LIME}; --magenta: {MAGENTA}; --error: {ERROR};
}}

/* ---- Canvas + ambient glow -------------------------------------------- */
.stApp {{
    background:
        radial-gradient(1100px 500px at 88% -8%, rgba(0,240,255,0.07), transparent 60%),
        radial-gradient(900px 480px at 5% 108%, rgba(195,244,0,0.05), transparent 55%),
        {BG};
    color: {ON_SURFACE};
    font-family: 'Inter', -apple-system, sans-serif;
}}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stToolbar"] {{ right: 1rem; }}
.block-container {{ padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1480px; }}

/* ---- Typography ------------------------------------------------------- */
h1, h2, h3, h4, [data-testid="stHeading"] {{
    font-family: 'Hanken Grotesk', sans-serif !important;
    color: {ON_SURFACE}; letter-spacing: -0.01em;
}}
h1 {{ font-weight: 600; }}
[data-testid="stMarkdownContainer"] p {{ font-family: 'Inter', sans-serif; }}
code, kbd, pre, .mono {{ font-family: 'JetBrains Mono', monospace !important; }}
.material-symbols-outlined {{
    font-family: 'Material Symbols Outlined';
    font-weight: normal; font-style: normal; line-height: 1;
    vertical-align: middle; -webkit-font-feature-settings: 'liga';
}}

/* ---- Command bar / header -------------------------------------------- */
.lx-topbar {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 12px 20px; margin-bottom: 18px; border-radius: 14px;
    background: rgba(14,14,15,0.72); backdrop-filter: blur(18px);
    border: 1px solid rgba(132,148,149,0.18);
}}
.lx-brand {{ display: flex; align-items: center; gap: 12px; }}
.lx-brand .dot {{
    width: 30px; height: 30px; border-radius: 9px;
    background: linear-gradient(150deg, {CYAN}, {CYAN_DIM});
    box-shadow: 0 0 16px rgba(0,240,255,0.55);
    display: flex; align-items: center; justify-content: center; color: #00242a;
}}
.lx-brand .name {{ font-family:'Hanken Grotesk'; font-weight:600; font-size:18px; color:{CYAN}; }}
.lx-brand .sub {{ font-family:'JetBrains Mono'; font-size:10px; color:{OUTLINE};
    letter-spacing:.12em; text-transform:uppercase; }}
.lx-topbar .right {{ display:flex; align-items:center; gap:10px; }}
.lx-chip {{
    display:inline-flex; align-items:center; gap:6px; padding:6px 12px;
    border-radius:999px; border:1px solid rgba(132,148,149,0.28);
    background:{SURFACE}; font-family:'JetBrains Mono'; font-size:11px;
    color:{ON_SURFACE_VARIANT}; letter-spacing:.04em;
}}
.lx-chip .material-symbols-outlined {{ font-size:15px; }}
.lx-chip.ok {{ color:{LIME}; border-color:rgba(195,244,0,0.35); }}
.lx-chip.warn {{ color:{ERROR}; border-color:rgba(255,180,171,0.35); }}
.lx-chip.cyan {{ color:{CYAN}; border-color:rgba(125,244,255,0.35); }}

/* ---- Hero diagnostics ------------------------------------------------- */
.lx-kicker {{ font-family:'JetBrains Mono'; font-size:11px; color:{CYAN};
    letter-spacing:.2em; text-transform:uppercase; }}
.lx-h1 {{ font-family:'Hanken Grotesk'; font-weight:600; font-size:32px;
    color:{WHITE}; margin:2px 0 4px; letter-spacing:-0.01em; }}
.lx-status {{ display:flex; align-items:center; gap:8px; font-size:13px; color:{ON_SURFACE_VARIANT}; }}
.lx-pulse {{ width:9px; height:9px; border-radius:999px; background:{LIME};
    box-shadow:0 0 9px {LIME}; animation:lxpulse 2s infinite; }}
.lx-pulse.red {{ background:{ERROR}; box-shadow:0 0 9px {ERROR}; }}
@keyframes lxpulse {{ 0%,100%{{opacity:1;}} 50%{{opacity:.35;}} }}

/* ---- Metric grid (glass cards) --------------------------------------- */
.lx-grid {{ display:grid; gap:16px; margin:4px 0 6px; }}
.lx-grid.c6 {{ grid-template-columns: repeat(6, 1fr); }}
.lx-grid.c5 {{ grid-template-columns: repeat(5, 1fr); }}
.lx-grid.c4 {{ grid-template-columns: repeat(4, 1fr); }}
.lx-grid.c3 {{ grid-template-columns: repeat(3, 1fr); }}
.lx-grid.c2 {{ grid-template-columns: repeat(2, 1fr); }}
@media (max-width:1300px) {{ .lx-grid.c6,.lx-grid.c5 {{ grid-template-columns: repeat(3,1fr); }} }}
@media (max-width:1100px) {{ .lx-grid.c4,.lx-grid.c3 {{ grid-template-columns: repeat(2,1fr); }} }}
.lx-card {{
    background: rgba(32,31,32,0.40); backdrop-filter: blur(16px);
    border:1px solid rgba(132,148,149,0.20); border-radius:16px; padding:18px 20px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);
    transition: border-color .25s ease, transform .25s ease;
}}
.lx-card:hover {{ border-color: rgba(125,244,255,0.35); transform: translateY(-1px); }}
.lx-card .head {{ display:flex; justify-content:space-between; align-items:flex-start; }}
.lx-card .label {{ font-family:'JetBrains Mono'; font-size:11px; color:{ON_SURFACE_VARIANT};
    letter-spacing:.06em; text-transform:uppercase; }}
.lx-card .icon {{ color:{OUTLINE}; }}
.lx-card .value {{ font-family:'Hanken Grotesk'; font-weight:600; font-size:30px;
    color:{ON_SURFACE}; margin-top:12px; line-height:1; }}
.lx-card .value.cyan {{ color:{CYAN}; text-shadow:0 0 12px rgba(125,244,255,0.45); }}
.lx-card .value.lime {{ color:{LIME}; text-shadow:0 0 12px rgba(195,244,0,0.40); }}
.lx-card .value.magenta {{ color:{MAGENTA}; }}
.lx-card .value.error {{ color:{ERROR}; }}
.lx-card .sub {{ font-family:'JetBrains Mono'; font-size:11px; color:{ON_SURFACE_VARIANT}; margin-top:8px; }}
.lx-card .sub.up {{ color:{LIME}; }}
.lx-card .sub.down {{ color:{ERROR}; }}

/* progress bar inside card / rows */
.lx-bar {{ width:100%; height:6px; border-radius:999px; background:{SURFACE_HIGH};
    overflow:hidden; margin-top:10px; }}
.lx-bar > span {{ display:block; height:100%; border-radius:999px;
    background:{CYAN}; box-shadow:0 0 10px rgba(125,244,255,0.5); }}
.lx-bar.lime > span {{ background:{LIME}; box-shadow:0 0 8px rgba(195,244,0,0.5); }}
.lx-bar.error > span {{ background:{ERROR}; box-shadow:none; }}
.lx-bar.muted > span {{ background:{OUTLINE}; box-shadow:none; }}

/* ---- Panels (section titles above native widgets) -------------------- */
.lx-panel-head {{ display:flex; justify-content:space-between; align-items:center;
    margin:18px 0 10px; }}
.lx-panel-title {{ font-family:'JetBrains Mono'; font-size:12px; color:{ON_SURFACE_VARIANT};
    letter-spacing:.16em; text-transform:uppercase; display:flex; align-items:center; gap:8px; }}
.lx-panel-title .material-symbols-outlined {{ font-size:17px; color:{CYAN}; }}
.lx-badge {{ font-family:'JetBrains Mono'; font-size:10px; padding:3px 9px; border-radius:999px;
    background:rgba(0,240,255,0.10); color:{CYAN}; border:1px solid rgba(125,244,255,0.30);
    letter-spacing:.08em; }}

/* Bordered containers act as glass cards */
[data-testid="stVerticalBlockBorderWrapper"] {{
    background: rgba(32,31,32,0.34); backdrop-filter: blur(14px);
    border:1px solid rgba(132,148,149,0.18) !important; border-radius:16px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
}}

/* ---- Decision bias / key-value rows ---------------------------------- */
.lx-kv {{ display:flex; justify-content:space-between; font-family:'JetBrains Mono';
    font-size:12px; margin-bottom:5px; }}
.lx-kv .k {{ color:{OUTLINE}; }}
.lx-kv .v {{ color:{ON_SURFACE}; }}
.lx-riskgrid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px 22px; }}
.lx-risk .rk {{ font-family:'JetBrains Mono'; font-size:10px; color:{OUTLINE};
    letter-spacing:.05em; text-transform:uppercase; }}
.lx-risk .rv {{ font-family:'JetBrains Mono'; font-size:15px; color:{ON_SURFACE}; margin-top:3px; }}
.lx-risk .rv.lime {{ color:{LIME}; }}
.lx-risk .rv.cyan {{ color:{CYAN}; }}

/* ---- HTML tables (opportunity queue / attempt log) ------------------- */
.lx-table-wrap {{ overflow-x:auto; border-radius:14px; border:1px solid rgba(132,148,149,0.18);
    background: rgba(14,14,15,0.45); }}
table.lx-table {{ width:100%; border-collapse:collapse; }}
table.lx-table th {{ font-family:'JetBrains Mono'; font-size:10px; color:{OUTLINE};
    letter-spacing:.08em; text-transform:uppercase; text-align:left; font-weight:500;
    padding:12px 16px; background:rgba(19,19,20,0.6); border-bottom:1px solid rgba(132,148,149,0.14); }}
table.lx-table td {{ font-family:'JetBrains Mono'; font-size:12px; color:{ON_SURFACE};
    padding:11px 16px; border-bottom:1px solid rgba(132,148,149,0.07); white-space:nowrap; }}
table.lx-table tr:last-child td {{ border-bottom:none; }}
table.lx-table tbody tr {{ transition: background .15s ease; }}
table.lx-table tbody tr:hover {{ background: rgba(53,52,54,0.30); }}
.lx-pill {{ font-family:'JetBrains Mono'; font-size:10px; padding:3px 9px; border-radius:6px;
    border:1px solid; letter-spacing:.03em; }}
.lx-pill.buy {{ background:rgba(195,244,0,0.10); color:{LIME}; border-color:rgba(195,244,0,0.25); }}
.lx-pill.sell {{ background:rgba(255,180,171,0.10); color:{ERROR}; border-color:rgba(255,180,171,0.25); }}
.lx-pill.none {{ background:{SURFACE_VARIANT}; color:{OUTLINE}; border-color:rgba(132,148,149,0.25); }}
.lx-pill.ready {{ background:rgba(0,240,255,0.10); color:{CYAN}; border-color:rgba(125,244,255,0.28); }}
.lx-pill.blocked {{ background:rgba(255,180,171,0.10); color:{ERROR}; border-color:rgba(255,180,171,0.25); }}
.lx-dot {{ display:inline-block; width:7px; height:7px; border-radius:999px; margin-right:7px; }}
.lx-minibar {{ display:inline-block; width:64px; height:5px; border-radius:999px;
    background:{SURFACE}; overflow:hidden; vertical-align:middle; }}
.lx-minibar > span {{ display:block; height:100%; }}

/* ---- Native widget theming ------------------------------------------- */
[data-testid="stMetric"] {{
    background: rgba(32,31,32,0.40); backdrop-filter: blur(14px);
    border:1px solid rgba(132,148,149,0.18); border-radius:14px; padding:14px 16px;
}}
[data-testid="stMetricLabel"] p {{ font-family:'JetBrains Mono'; font-size:11px !important;
    color:{ON_SURFACE_VARIANT}; letter-spacing:.05em; text-transform:uppercase; }}
[data-testid="stMetricValue"] {{ font-family:'Hanken Grotesk'; color:{ON_SURFACE}; }}

div[data-baseweb="tab-list"] {{ gap:6px; border-bottom:1px solid rgba(132,148,149,0.14); }}
button[data-baseweb="tab"] {{ background:transparent; border-radius:8px 8px 0 0;
    padding:8px 16px; color:{OUTLINE}; font-family:'JetBrains Mono'; font-size:12px;
    letter-spacing:.04em; }}
button[data-baseweb="tab"]:hover {{ color:{ON_SURFACE}; background:rgba(53,52,54,0.30); }}
button[data-baseweb="tab"][aria-selected="true"] {{ color:{CYAN};
    border-bottom:2px solid {CYAN}; }}

.stButton > button {{
    background:{SURFACE}; color:{CYAN}; border:1px solid rgba(125,244,255,0.40);
    border-radius:9px; font-family:'JetBrains Mono'; font-size:12px; letter-spacing:.04em;
    transition: all .2s ease;
}}
.stButton > button:hover {{ background:rgba(0,240,255,0.10); border-color:{CYAN};
    box-shadow:0 0 14px rgba(125,244,255,0.30); color:{CYAN}; }}
.stDownloadButton > button {{ background:{WHITE}; color:#00242a; border:none;
    border-radius:9px; font-family:'JetBrains Mono'; font-weight:500; }}
.stDownloadButton > button:hover {{ box-shadow:0 0 16px rgba(125,244,255,0.40); color:#00242a; }}

[data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
[data-baseweb="select"] > div, [data-baseweb="input"], [data-baseweb="base-input"] {{
    background: rgba(0,0,0,0.40) !important; border-color: rgba(132,148,149,0.25) !important;
    color:{ON_SURFACE} !important; font-family:'JetBrains Mono'; border-radius:9px;
}}
[data-testid="stTextInput"] input::placeholder {{ color:{OUTLINE} !important; }}
[data-testid="stWidgetLabel"] p {{ font-family:'JetBrains Mono'; font-size:11px;
    color:{OUTLINE}; letter-spacing:.04em; text-transform:uppercase; }}

/* Dropdown / select popovers render in a body-level portal — theme explicitly */
[data-baseweb="popover"] [role="listbox"], [data-baseweb="menu"], ul[role="listbox"] {{
    background: {SURFACE_LOW} !important;
    border: 1px solid rgba(132,148,149,0.25) !important;
    border-radius: 10px !important;
    box-shadow: 0 12px 30px rgba(0,0,0,0.55) !important;
}}
li[role="option"], [data-baseweb="menu"] li {{
    background: transparent !important; color: {ON_SURFACE} !important;
    font-family: 'JetBrains Mono' !important; font-size: 12px !important;
}}
li[role="option"]:hover, [data-baseweb="menu"] li:hover,
li[role="option"][aria-selected="true"], li[aria-selected="true"] {{
    background: rgba(0,240,255,0.12) !important; color: {CYAN} !important;
}}
/* multiselect chips + dropdown carets */
[data-baseweb="tag"] {{ background: rgba(0,240,255,0.12) !important; color: {CYAN} !important;
    border: 1px solid rgba(125,244,255,0.30) !important; font-family:'JetBrains Mono'; }}
[data-baseweb="select"] svg, [data-baseweb="input"] svg {{ fill: {OUTLINE} !important; }}
/* calendar / generic popover surface */
[data-baseweb="popover"] > div {{ background: {SURFACE_LOW} !important; }}

div[data-testid="stDataFrame"] {{ border:1px solid rgba(132,148,149,0.18); border-radius:12px; }}
[data-testid="stExpander"] {{ border:1px solid rgba(132,148,149,0.16); border-radius:12px;
    background:rgba(14,14,15,0.4); }}
[data-testid="stExpander"] summary {{ font-family:'JetBrains Mono'; font-size:12px; color:{ON_SURFACE_VARIANT}; }}
hr {{ border-color: rgba(132,148,149,0.14); }}

/* scrollbar */
::-webkit-scrollbar {{ width:7px; height:7px; }}
::-webkit-scrollbar-track {{ background:transparent; }}
::-webkit-scrollbar-thumb {{ background:{OUTLINE_VARIANT}; border-radius:4px; }}
::-webkit-scrollbar-thumb:hover {{ background:{CYAN}; }}
</style>
"""


def apply_theme() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def fmt(value: Any, suffix: str = "", decimals: int = 2) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:,.{decimals}f}{suffix}"
    return f"{value}{suffix}"


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _clean(markup: str) -> str:
    """Flatten HTML to a single line.

    Streamlit renders markdown, where lines indented by 4+ spaces become code
    blocks — that would dump our raw HTML on screen. Collapsing the markup to a
    single line (tags are only ever separated by whitespace across lines) keeps
    ``unsafe_allow_html`` rendering it as real HTML.
    """
    return "".join(line.strip() for line in markup.splitlines())


def human_age(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    return f"{seconds / 3600:.1f}h ago"


def market_session(now: datetime) -> tuple[str, str]:
    if now.weekday() >= 5:
        return "CLOSED", "Weekend"
    minutes = now.hour * 60 + now.minute
    if minutes < 4 * 60:
        return "CLOSED", "Overnight"
    if minutes < 9 * 60 + 30:
        return "PRE-MARKET", "Before 09:30"
    if minutes < 16 * 60:
        return "OPEN", "Regular session"
    if minutes < 20 * 60:
        return "AFTER-HOURS", "After 16:00"
    return "CLOSED", "Overnight"


# --------------------------------------------------------------------------- #
# Structural building blocks (raw HTML)
# --------------------------------------------------------------------------- #
def topbar(brand_sub: str, chips: Sequence[tuple[str, str, str]]) -> None:
    """Render the command bar: brand on the left, status chips on the right.

    ``chips`` items are ``(icon, text, variant)`` where variant is one of
    ``""``, ``ok``, ``warn``, ``cyan``.
    """
    chip_html = "".join(
        f'<span class="lx-chip {variant}">'
        f'<span class="material-symbols-outlined">{_esc(icon)}</span>{_esc(text)}</span>'
        for icon, text, variant in chips
    )
    st.markdown(
        _clean(
            f"""
            <div class="lx-topbar">
              <div class="lx-brand">
                <div class="dot"><span class="material-symbols-outlined">monitoring</span></div>
                <div>
                  <div class="name">Gerchik&nbsp;Bot&nbsp;v2</div>
                  <div class="sub">{_esc(brand_sub)}</div>
                </div>
              </div>
              <div class="right">{chip_html}</div>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )


def hero(title: str, online: bool, status_text: str) -> None:
    pulse = "lx-pulse" if online else "lx-pulse red"
    st.markdown(
        _clean(
            f"""
            <div class="lx-kicker">Trading operations</div>
            <div class="lx-h1">{_esc(title)}</div>
            <div class="lx-status"><span class="{pulse}"></span>{_esc(status_text)}</div>
            """
        ),
        unsafe_allow_html=True,
    )


def metric_grid(cards: Sequence[dict[str, Any]], columns: int = 4) -> None:
    """Render glass metric cards.

    Each card dict supports: ``label``, ``value``, ``icon``, ``accent``
    (cyan/lime/magenta/error or ""), ``sub`` text, ``sub_dir`` (up/down/""),
    and optional ``progress`` (0..1) with ``progress_label``.
    """
    blocks = []
    for card in cards:
        accent = card.get("accent", "")
        icon = card.get("icon")
        icon_html = (
            f'<span class="material-symbols-outlined icon">{_esc(icon)}</span>' if icon else ""
        )
        sub_html = ""
        if card.get("progress") is not None:
            pct = max(0.0, min(1.0, float(card["progress"]))) * 100
            label = card.get("progress_label", f"{pct:.0f}%")
            sub_html = (
                f'<div class="lx-bar"><span style="width:{pct:.0f}%"></span></div>'
                f'<div class="sub" style="text-align:right">{_esc(label)}</div>'
            )
        elif card.get("sub"):
            arrow = ""
            direction = card.get("sub_dir", "")
            if direction == "up":
                arrow = '<span class="material-symbols-outlined" style="font-size:13px">arrow_upward</span>'
            elif direction == "down":
                arrow = '<span class="material-symbols-outlined" style="font-size:13px">arrow_downward</span>'
            sub_html = f'<div class="sub {direction}">{arrow}{_esc(card["sub"])}</div>'
        blocks.append(
            _clean(
                f"""
                <div class="lx-card">
                  <div class="head"><span class="label">{_esc(card.get("label",""))}</span>{icon_html}</div>
                  <div class="value {accent}">{_esc(card.get("value",""))}</div>
                  {sub_html}
                </div>
                """
            )
        )
    st.markdown(
        f'<div class="lx-grid c{columns}">{"".join(blocks)}</div>',
        unsafe_allow_html=True,
    )


def panel_header(title: str, icon: str | None = None, badge: str | None = None) -> None:
    icon_html = f'<span class="material-symbols-outlined">{_esc(icon)}</span>' if icon else ""
    badge_html = f'<span class="lx-badge">{_esc(badge)}</span>' if badge else ""
    st.markdown(
        f'<div class="lx-panel-head"><div class="lx-panel-title">{icon_html}{_esc(title)}</div>'
        f"{badge_html}</div>",
        unsafe_allow_html=True,
    )


def bias_card(title: str, rows: Sequence[tuple[str, float, str]]) -> str:
    """Return HTML for a decision-bias card: rows of (label, pct 0..100, color)."""
    body = []
    for label, pct, color in rows:
        pct = max(0.0, min(100.0, float(pct)))
        body.append(
            f'<div class="lx-kv"><span class="k" style="color:{ACCENTS.get(color, OUTLINE)}">{_esc(label)}</span>'
            f'<span class="v">{pct:.0f}%</span></div>'
            f'<div class="lx-bar {color}" style="margin-bottom:12px"><span style="width:{pct:.0f}%"></span></div>'
        )
    return (
        f'<div class="lx-card" style="height:100%">'
        f'<div class="label" style="border-bottom:1px solid rgba(132,148,149,0.18);'
        f'padding-bottom:10px;margin-bottom:14px">{_esc(title)}</div>{"".join(body)}</div>'
    )


def stat_grid_card(title: str, items: Sequence[tuple[str, str, str]]) -> str:
    """Return HTML for a 2-col stat card: items of (label, value, accent)."""
    cells = "".join(
        f'<div class="lx-risk"><div class="rk">{_esc(label)}</div>'
        f'<div class="rv {accent}">{_esc(value)}</div></div>'
        for label, value, accent in items
    )
    return (
        f'<div class="lx-card" style="height:100%">'
        f'<div class="label" style="border-bottom:1px solid rgba(132,148,149,0.18);'
        f'padding-bottom:10px;margin-bottom:14px">{_esc(title)}</div>'
        f'<div class="lx-riskgrid">{cells}</div></div>'
    )


def feature_card(title: str, big: str, big_accent: str, caption: str, footer: tuple[str, str, str] | None = None) -> str:
    """Return HTML for a hero metric card (big number + caption + optional footer pill)."""
    footer_html = ""
    if footer:
        f_label, f_value, f_accent = footer
        footer_html = (
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'margin-top:16px;padding:9px 12px;border-radius:9px;background:{SURFACE_LOW};'
            f'border:1px solid rgba(132,148,149,0.20)">'
            f'<span class="rk">{_esc(f_label)}</span>'
            f'<span class="rv {f_accent}" style="font-size:12px">{_esc(f_value)}</span></div>'
        )
    return (
        f'<div class="lx-card" style="height:100%;display:flex;flex-direction:column;justify-content:space-between">'
        f'<div><div class="label" style="border-bottom:1px solid rgba(132,148,149,0.18);'
        f'padding-bottom:10px;margin-bottom:14px">{_esc(title)}</div>'
        f'<div style="display:flex;align-items:flex-end;gap:8px">'
        f'<span class="value {big_accent}" style="font-size:44px;margin:0">{_esc(big)}</span>'
        f'<span class="sub" style="margin:0 0 6px">{_esc(caption)}</span></div></div>'
        f"{footer_html}</div>"
    )


def signal_pill(signal: Any) -> str:
    text = str(signal or "NONE").upper()
    cls = "buy" if text == "BUY" else "sell" if text == "SELL" else "none"
    return f'<span class="lx-pill {cls}">{_esc(text)}</span>'


def status_pill(status: str) -> str:
    cls = {"READY": "ready", "BLOCKED": "blocked"}.get(status.upper(), "none")
    return f'<span class="lx-pill {cls}">{_esc(status)}</span>'


def minibar(pct: float, color: str = "cyan") -> str:
    pct = max(0.0, min(100.0, float(pct)))
    return (
        f'<span class="lx-minibar"><span style="width:{pct:.0f}%;'
        f'background:{ACCENTS.get(color, CYAN)}"></span></span>'
    )


def html_table(columns: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    """Render a styled HTML table. Cell values are inserted as raw HTML, so
    callers must pre-escape any untrusted text (use the pill/minibar helpers)."""
    head = "".join(f"<th>{_esc(col)}</th>" for col in columns)
    if rows:
        body = "".join(
            "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
        )
    else:
        body = (
            f'<tr><td colspan="{len(columns)}" style="text-align:center;color:{OUTLINE};'
            f'padding:24px">No data available.</td></tr>'
        )
    st.markdown(
        f'<div class="lx-table-wrap"><table class="lx-table"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Dataframes + diagnostics
# --------------------------------------------------------------------------- #
def show_df(
    frame: pd.DataFrame,
    *,
    empty: str = "No rows match the current filters.",
    height: int | None = None,
    column_config: dict[str, Any] | None = None,
) -> None:
    if frame is None or frame.empty:
        st.caption(empty)
        return
    safe = frame.copy()
    for column in safe.columns:
        if safe[column].apply(lambda value: isinstance(value, (list, dict, tuple))).any():
            safe[column] = safe[column].apply(_stringify)
        elif pd.api.types.is_object_dtype(safe[column]):
            inferred = pd.api.types.infer_dtype(safe[column].dropna(), skipna=True)
            if inferred.startswith("mixed"):
                safe[column] = safe[column].apply(lambda value: "" if value is None else str(value))
    options: dict[str, Any] = {
        "hide_index": True,
        "width": "stretch",
        "column_config": column_config,
    }
    if height is not None:
        options["height"] = height
    st.dataframe(safe, **options)


def _stringify(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return ", ".join(map(str, value))
    if isinstance(value, dict):
        return ", ".join(f"{key}: {item}" for key, item in value.items())
    return value


def source_health_bar(items: Iterable["SourceHealth"]) -> None:
    chips = []
    icon_for = {"fresh": "check_circle", "available": "history",
                "stale": "warning", "missing": "error"}
    variant_for = {"fresh": "ok", "available": "", "stale": "warn", "missing": "warn"}
    for item in items:
        if item.updated_at:
            label = f"{item.name} {item.updated_at:%H:%M:%S} · {human_age(item.age_seconds)}"
        else:
            label = f"{item.name} missing"
        chips.append(
            f'<span class="lx-chip {variant_for.get(item.status, "")}">'
            f'<span class="material-symbols-outlined">{icon_for.get(item.status, "circle")}</span>'
            f"{_esc(label)}</span>"
        )
    st.markdown(
        f'<div style="display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 4px">{"".join(chips)}</div>',
        unsafe_allow_html=True,
    )
