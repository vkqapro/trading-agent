"""Saved-bar scanner for the Inefficiency Reclaim Strategy."""

from __future__ import annotations

import math
import os
import time as clock
import uuid
from collections import Counter
from dataclasses import asdict, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from src.config import LOGGER, SETTINGS
from src.data.bar_store import load_bars
from src.storage.inefficiency_reclaim_store import InefficiencyReclaimStore
from src.strategy.inefficiency_reclaim import (
    AccountState,
    Bar,
    IRSConfig,
    MarketRegime,
    Quote,
    RejectionReason,
    RiskStatus,
    SetupState,
    StrategyCandidate,
    StrategyContext,
    StructuralLevel,
    calculate_robust_atr,
    completed_bars,
    detect_displacement,
    evaluate_strategy,
    grade_for_score,
)


ET = ZoneInfo("America/New_York")
BarLoader = Callable[[str, str], pd.DataFrame]
QuoteLoader = Callable[[str], Mapping[str, Any]]
TIMEFRAME_DELTAS = {
    "1 day": timedelta(days=1),
    "1 hour": timedelta(hours=1),
    "15 mins": timedelta(minutes=15),
    "5 mins": timedelta(minutes=5),
    "1 min": timedelta(minutes=1),
}


def _git_commit() -> str | None:
    configured = str(os.getenv("GIT_COMMIT") or "").strip()
    if configured:
        return configured
    for parent in Path(__file__).resolve().parents:
        git_dir = parent / ".git"
        try:
            head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
            if head.startswith("ref: "):
                return (git_dir / head[5:]).read_text(encoding="utf-8").strip() or None
            return head or None
        except OSError:
            continue
    return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["date", "open", "high", "low", "close", "volume"]
    if frame is None or frame.empty or "date" not in frame.columns:
        return pd.DataFrame(columns=columns)
    data = frame.copy()
    raw_dates = data["date"]
    parsed_samples: list[pd.Timestamp] = []
    for value in raw_dates:
        try:
            parsed_samples.append(pd.Timestamp(value))
        except (TypeError, ValueError):
            continue
    if any(item.tzinfo is not None for item in parsed_samples):
        data["date"] = pd.to_datetime(raw_dates, errors="coerce", utc=True).dt.tz_convert(ET)
    else:
        data["date"] = pd.to_datetime(raw_dates, errors="coerce").dt.tz_localize(
            ET, ambiguous="NaT", nonexistent="shift_forward"
        )
    for column in ("open", "high", "low", "close", "volume"):
        data[column] = pd.to_numeric(data.get(column), errors="coerce")
    data["volume"] = data["volume"].fillna(0)
    data = data.dropna(subset=["date", "open", "high", "low", "close"])
    valid = (
        (data["high"] >= data[["open", "close", "low"]].max(axis=1))
        & (data["low"] <= data[["open", "close", "high"]].min(axis=1))
        & (data["volume"] >= 0)
    )
    return (
        data.loc[valid, columns]
        .sort_values("date")
        .drop_duplicates(subset=["date"], keep="last")
        .reset_index(drop=True)
    )


def resample_rth(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    data = normalize_frame(frame)
    if data.empty:
        return data
    local = data[
        (data["date"].dt.time >= time(9, 30))
        & (data["date"].dt.time < time(16, 0))
    ].copy()
    if local.empty:
        return local
    return (
        local.set_index("date")
        .resample(rule, origin="start_day", offset="30min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )


def frame_to_bars(
    symbol: str,
    timeframe: str,
    frame: pd.DataFrame,
    *,
    as_of: datetime,
) -> tuple[Bar, ...]:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware.")
    data = normalize_frame(frame)
    duration = TIMEFRAME_DELTAS[timeframe]
    bars: list[Bar] = []
    for row in data.to_dict(orient="records"):
        timestamp = pd.Timestamp(row["date"]).to_pydatetime()
        source_timestamp = timestamp
        if timeframe == "1 day":
            session_close = datetime.combine(timestamp.date(), time(16, 0), tzinfo=ET)
            complete = session_close <= as_of.astimezone(ET)
            timestamp = session_close
        else:
            complete = timestamp + duration <= as_of
            timestamp = timestamp + duration
        regular = (
            timeframe == "1 day"
            or time(9, 30) <= source_timestamp.astimezone(ET).time() < time(16, 0)
        )
        try:
            bars.append(
                Bar(
                    symbol=symbol.upper(),
                    timeframe=timeframe,
                    timestamp=timestamp,
                    open=Decimal(str(row["open"])),
                    high=Decimal(str(row["high"])),
                    low=Decimal(str(row["low"])),
                    close=Decimal(str(row["close"])),
                    volume=max(0, int(float(row.get("volume") or 0))),
                    is_complete=complete,
                    is_regular_session=regular,
                    adjustment_status="adjusted",
                    source="bar_store",
                )
            )
        except (ValueError, TypeError):
            continue
    return tuple(bars)


def data_quality_diagnostics(
    bars: Sequence[Bar],
    timeframe: str,
) -> dict[str, Any]:
    complete = [bar for bar in bars if bar.is_complete]
    duplicate_count = len(complete) - len({bar.timestamp for bar in complete})
    missing_intervals: list[str] = []
    expected = TIMEFRAME_DELTAS[timeframe]
    if timeframe != "1 day":
        for previous, current in zip(complete, complete[1:]):
            if previous.timestamp.date() != current.timestamp.date():
                continue
            gap = current.timestamp - previous.timestamp
            if gap > expected * 1.5:
                missing_intervals.append(
                    f"{previous.timestamp.isoformat()}->{current.timestamp.isoformat()}"
                )
    return {
        "rows": len(bars),
        "completed_rows": len(complete),
        "incomplete_rows_ignored": len(bars) - len(complete),
        "duplicate_count": duplicate_count,
        "missing_intervals": missing_intervals[:20],
        "complete": bool(complete) and duplicate_count == 0 and not missing_intervals,
    }


def _status(value: Any, *, explicit_clear: bool = False) -> RiskStatus:
    normalized = str(value or "").strip().upper()
    if normalized in {"CLEAR", "OK", "PASS", "SAFE", "FALSE"}:
        return RiskStatus.CLEAR
    if normalized in {"BLOCKED", "RISK", "FAIL", "TRUE", "HALTED"}:
        return RiskStatus.BLOCKED
    return RiskStatus.CLEAR if explicit_clear else RiskStatus.UNKNOWN


def _plan_risk_statuses(plan: Mapping[str, Any]) -> tuple[RiskStatus, RiskStatus, RiskStatus, RiskStatus]:
    news = _status(plan.get("news_status"))
    if news is RiskStatus.UNKNOWN and plan.get("news_blocked") is True:
        news = RiskStatus.BLOCKED
    earnings = _status(plan.get("earnings_status"))
    event = plan.get("earnings_event")
    if earnings is RiskStatus.UNKNOWN and isinstance(event, Mapping) and event:
        earnings = RiskStatus.BLOCKED
    corporate = _status(
        plan.get("corporate_action_status"),
        explicit_clear=plan.get("corporate_action_checked") is True,
    )
    halt = _status(plan.get("halt_status"), explicit_clear=plan.get("halt_checked") is True)
    return news, earnings, corporate, halt


def _levels(plan: Mapping[str, Any]) -> tuple[StructuralLevel, ...]:
    raw = plan.get("levels") or plan.get("raw_levels") or []
    levels: list[StructuralLevel] = []
    for index, item in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(item, Mapping):
            continue
        price = _number(item.get("price"))
        if price is None or price <= 0:
            continue
        levels.append(
            StructuralLevel(
                price=Decimal(str(price)),
                kind=str(item.get("kind") or item.get("level_type") or "level"),
                level_id=str(item.get("level_id") or f"saved-level-{index}"),
                strength=Decimal(str(_number(item.get("strength_score") or item.get("strength")) or 0)),
            )
        )
    return tuple(levels)


def classify_market_regime(daily: Sequence[Bar]) -> MarketRegime:
    closes = [float(bar.close) for bar in daily if bar.is_complete]
    if len(closes) < 50:
        return MarketRegime.UNKNOWN
    series = pd.Series(closes)
    ema20 = series.ewm(span=20, adjust=False).mean()
    ema50 = series.ewm(span=50, adjust=False).mean()
    slope = float(ema20.iloc[-1] - ema20.iloc[-6])
    returns = series.pct_change().dropna()
    recent_vol = float(returns.tail(20).std() or 0)
    rolling_volatility = returns.rolling(20).std().dropna().tail(100)
    historical_vol = float(rolling_volatility.median() or 0)
    trending = (
        (series.iloc[-1] > ema20.iloc[-1] >= ema50.iloc[-1] and slope > 0)
        or (series.iloc[-1] < ema20.iloc[-1] <= ema50.iloc[-1] and slope < 0)
    )
    high_vol = recent_vol > max(historical_vol * 1.5, 0.02)
    if trending:
        return MarketRegime.TREND_HIGH_VOL if high_vol else MarketRegime.TREND_LOW_VOL
    return MarketRegime.RANGE_HIGH_VOL if high_vol else MarketRegime.RANGE_LOW_VOL


def classify_market_trend_direction(daily: Sequence[Bar]) -> str | None:
    closes = [float(bar.close) for bar in daily if bar.is_complete]
    if len(closes) < 50:
        return None
    series = pd.Series(closes)
    ema20 = series.ewm(span=20, adjust=False).mean()
    ema50 = series.ewm(span=50, adjust=False).mean()
    slope = float(ema20.iloc[-1] - ema20.iloc[-6])
    if series.iloc[-1] > ema20.iloc[-1] >= ema50.iloc[-1] and slope > 0:
        return "LONG"
    if series.iloc[-1] < ema20.iloc[-1] <= ema50.iloc[-1] and slope < 0:
        return "SHORT"
    return None


def _account_state(account: Mapping[str, Any] | None) -> AccountState | None:
    if not account:
        return None
    return AccountState(
        equity=Decimal(str(_number(account.get("equity")) or 0)),
        buying_power=Decimal(str(_number(account.get("buying_power")) or 0)),
        open_positions=int(account.get("open_positions") or 0),
        trades_today=int(account.get("trades_today") or 0),
        daily_loss=Decimal(str(_number(account.get("daily_loss")) or 0)),
        existing_symbols=tuple(str(item).upper() for item in account.get("existing_symbols", [])),
        pending_symbols=tuple(str(item).upper() for item in account.get("pending_symbols", [])),
        protective_stop_available=bool(account.get("protective_stop_available", False)),
        broker_connected=bool(account.get("broker_connected", False)),
        short_available=bool(account.get("short_available", False)),
    )


def _quote(value: Mapping[str, Any] | None, as_of: datetime) -> Quote | None:
    if not value:
        return None
    bid, ask, last = (_number(value.get(key)) for key in ("bid", "ask", "last"))
    if not bid or not ask:
        return None
    raw_time = value.get("timestamp")
    try:
        timestamp = pd.Timestamp(raw_time).to_pydatetime() if raw_time else as_of
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=ET)
    except (TypeError, ValueError):
        return None
    return Quote(
        Decimal(str(bid)),
        Decimal(str(ask)),
        Decimal(str(last or (bid + ask) / 2)),
        timestamp,
        str(value.get("market_data_type") or "live"),
    )


def _liquidity_rejections(daily: Sequence[Bar], config: IRSConfig) -> tuple[str, ...]:
    complete = [bar for bar in daily if bar.is_complete]
    if not complete:
        return (RejectionReason.INSUFFICIENT_DAILY_HISTORY.value,)
    latest = complete[-1]
    reasons: list[str] = []
    if not config.min_price <= latest.close <= config.max_price:
        reasons.append(RejectionReason.PRICE_OUT_OF_RANGE.value)
    recent = complete[-20:]
    average_volume = sum(bar.volume for bar in recent) / max(len(recent), 1)
    average_dollar_volume = (
        sum(Decimal(bar.volume) * bar.close for bar in recent) / Decimal(max(len(recent), 1))
    )
    if average_volume < config.min_average_daily_volume:
        reasons.append(RejectionReason.LIQUIDITY_TOO_LOW.value)
    if average_dollar_volume < config.min_average_daily_dollar_volume:
        reasons.append(RejectionReason.LIQUIDITY_TOO_LOW.value)
    return tuple(dict.fromkeys(reasons))


def _with_scanner_rejections(
    candidate: StrategyCandidate,
    reasons: Sequence[str],
) -> StrategyCandidate:
    merged = tuple(dict.fromkeys((*candidate.hard_rejections, *reasons)))
    if not merged:
        return candidate
    state = candidate.state
    if any(
        item
        in {
            RejectionReason.INSUFFICIENT_DAILY_HISTORY.value,
            RejectionReason.INSUFFICIENT_HOURLY_HISTORY.value,
            RejectionReason.INSUFFICIENT_15M_HISTORY.value,
            RejectionReason.INVALID_BAR_DATA.value,
        }
        for item in merged
    ):
        state = SetupState.REJECTED_BY_DATA
    elif RejectionReason.LIQUIDITY_TOO_LOW.value in merged:
        state = SetupState.REJECTED_BY_LIQUIDITY
    return replace(candidate, hard_rejections=merged, state=state)


def candidate_row(candidate: StrategyCandidate) -> dict[str, Any]:
    payload = candidate.to_dict()
    zone = payload["zone"]
    plan = payload.get("order_plan") or {}
    confirmation = payload.get("confirmation") or {}
    diagnostics = payload.get("diagnostics") or {}
    retrace = payload.get("retrace_metrics") or {}
    displacement = payload.get("displacement_metrics") or {}
    def chart_bar_start(value: Any, minutes: int) -> str | None:
        if not value:
            return None
        try:
            return (pd.Timestamp(value) - pd.Timedelta(minutes=minutes)).isoformat()
        except (TypeError, ValueError):
            return None

    displacement_chart_time = chart_bar_start(zone.get("displacement_bar_time"), 60)
    retrace_chart_time = chart_bar_start(retrace.get("first_touch_time"), 15)
    confirmation_chart_time = chart_bar_start(confirmation.get("timestamp"), 15)
    return {
        "ticker": candidate.symbol,
        "direction": candidate.direction.value,
        "side": "LONG" if candidate.direction.value.endswith("LONG") else "SHORT",
        "strategy": candidate.strategy,
        "strategy_profile": candidate.profile.value,
        "setup_state": candidate.state.value,
        "status": candidate.state.value,
        "score": float(candidate.score),
        "grade": grade_for_score(candidate.score),
        "daily_regime": diagnostics.get("market_regime"),
        "sector_regime": diagnostics.get("sector_regime"),
        "relative_strength_20d": diagnostics.get("relative_strength_20d"),
        "zone_id": zone["zone_id"],
        "zone_type": zone["zone_type"],
        "zone_low": zone["zone_low"],
        "zone_high": zone["zone_high"],
        "zone_mid": zone["zone_mid"],
        "zone_width_atr": zone["zone_width_atr"],
        "displacement_time": zone["displacement_bar_time"],
        "displacement_atr_multiple": displacement.get("atr_multiple"),
        "displacement_body_ratio": displacement.get("body_ratio"),
        "relative_volume": displacement.get("relative_volume"),
        "retrace_depth": retrace.get("depth"),
        "confirmation_type": confirmation.get("confirmation_type"),
        "confirmation_time": confirmation.get("timestamp"),
        "planned_entry": plan.get("entry_stop"),
        "entry_limit": plan.get("entry_limit"),
        "planned_stop": plan.get("stop"),
        "planned_target": plan.get("target"),
        "risk_per_share": plan.get("risk_per_share"),
        "reward_per_share": plan.get("reward_per_share"),
        "structural_R": plan.get("structural_r"),
        "estimated_slippage": plan.get("estimated_costs"),
        "spread_percent": diagnostics.get("spread_percent"),
        "risk_cash": plan.get("risk_cash"),
        "position_size": plan.get("quantity"),
        "required_buying_power": plan.get("required_buying_power"),
        "earnings_status": diagnostics.get("earnings_status"),
        "news_status": diagnostics.get("news_status"),
        "quote_age": diagnostics.get("quote_age_seconds"),
        "quote_data_type": diagnostics.get("quote_data_type"),
        "expires_at": payload.get("expires_at"),
        "primary_reason": candidate.hard_rejections[0] if candidate.hard_rejections else "QUALIFIED",
        "rejection_reasons": list(candidate.hard_rejections),
        "soft_warnings": list(candidate.soft_warnings),
        "paper_live_mode": "PAPER",
        "strategy_version": candidate.strategy_version,
        "signal_id": candidate.signal_id,
        "score_breakdown": payload["score_breakdown"],
        "reasoning": candidate.explanation,
        "chart": {
            "timeframe": "intraday_15m",
            "displacement_bar_time": displacement_chart_time,
            "retrace_bar_time": retrace_chart_time,
            "confirmation_bar_time": confirmation_chart_time,
            "highlight_bars": [
                value
                for value in (
                    displacement_chart_time,
                    retrace_chart_time,
                    confirmation_chart_time,
                )
                if value
            ],
            "zone": {
                "p1": zone["zone_low"],
                "p2": zone["zone_high"],
                "label": zone["zone_type"],
            },
            "lines": [
                {"p": plan.get("entry_stop"), "label": "ENTRY", "color": "#7df4ff"},
                {"p": plan.get("stop"), "label": "STOP", "color": "#ff315f"},
                {"p": plan.get("target"), "label": "TARGET", "color": "#d7ff00"},
            ]
            if plan
            else [],
        },
        "diagnostics": diagnostics,
    }


def _diagnose_no_candidate(
    hourly: Sequence[Bar],
    levels: Sequence[StructuralLevel],
    config: IRSConfig,
) -> list[str]:
    complete_hourly = completed_bars(hourly)
    if len(complete_hourly) < max(120, config.robust_atr_min_values + 2):
        return [RejectionReason.INSUFFICIENT_HOURLY_HISTORY.value]
    reasons: Counter[str] = Counter()
    start = max(config.robust_atr_min_values + 1, len(complete_hourly) - 8)
    for index in range(start, len(complete_hourly)):
        atr = calculate_robust_atr(complete_hourly, config, end_index=index)
        if atr is None:
            continue
        result = detect_displacement(complete_hourly, index, atr, levels, config)
        reasons.update(result.failed_conditions)
    return [item for item, _count in reasons.most_common(4)] or [
        RejectionReason.NO_MEANINGFUL_STRUCTURE.value
    ]


def run_inefficiency_reclaim_screener(
    *,
    symbols: Sequence[str],
    bars_loader: BarLoader = load_bars,
    watchlist: Mapping[str, Any] | None = None,
    as_of: datetime | None = None,
    anchor_date: str | None = None,
    min_daily_history_rows: int | None = None,
    config: IRSConfig | None = None,
    store: InefficiencyReclaimStore | None = None,
    quotes: Mapping[str, Mapping[str, Any]] | None = None,
    quote_loader: QuoteLoader | None = None,
    account: Mapping[str, Any] | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    scan_started = clock.perf_counter()
    resolved_config = config or SETTINGS.inefficiency_reclaim.strategy_config()
    resolved_min_daily_rows = (
        SETTINGS.inefficiency_reclaim.min_daily_history_rows
        if min_daily_history_rows is None
        else int(min_daily_history_rows)
    )
    if resolved_min_daily_rows < 20:
        raise ValueError("IRS minimum daily history must be at least 20 rows.")
    now = as_of or datetime.now(ET)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    if anchor_date:
        anchor = date.fromisoformat(anchor_date)
        now = datetime.combine(anchor, time(16, 0), tzinfo=ET)
    run_id = uuid.uuid4().hex
    resolved_store = store
    if persist and resolved_store is None:
        resolved_store = InefficiencyReclaimStore(SETTINGS.inefficiency_reclaim.database_path)
    if resolved_store:
        resolved_store.record_scanner_run(
            run_id=run_id,
            mode="HOURLY_SETUP_SCAN",
            started_at=now,
            strategy_version=resolved_config.version,
            config_snapshot={
                **asdict(resolved_config),
                "min_daily_history_rows": resolved_min_daily_rows,
            },
            git_commit=_git_commit(),
            status="RUNNING",
        )

    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    data_readiness: dict[str, Any] = {}
    data_fetch_duration = 0.0
    candidate_count = 0
    retrace_count = 0
    confirmation_count = 0
    quote_requests = 0
    quotes_available = 0
    delayed_quotes = 0
    zone_ids: set[str] = set()
    newly_armed: list[dict[str, Any]] = []
    watchlist = watchlist or {}
    quotes = quotes or {}

    for raw_symbol in symbols:
        fetch_started = clock.perf_counter()
        symbol = str(raw_symbol).strip().upper()
        plan = watchlist.get(symbol, {})
        plan = plan if isinstance(plan, Mapping) else {}
        daily_frame = bars_loader(symbol, "daily")
        intraday_5m = bars_loader(symbol, "intraday_5m")
        hourly_frame = bars_loader(symbol, "intraday_1h")
        if hourly_frame.empty:
            hourly_frame = resample_rth(intraday_5m, "60min")
        bars_15m_frame = bars_loader(symbol, "intraday_15m")
        if bars_15m_frame.empty:
            bars_15m_frame = resample_rth(intraday_5m, "15min")
        daily = frame_to_bars(symbol, "1 day", daily_frame, as_of=now)
        hourly = frame_to_bars(symbol, "1 hour", hourly_frame, as_of=now)
        bars_15m = frame_to_bars(symbol, "15 mins", bars_15m_frame, as_of=now)
        bars_5m = frame_to_bars(symbol, "5 mins", intraday_5m, as_of=now)
        bars_1m = frame_to_bars(
            symbol,
            "1 min",
            bars_loader(symbol, "intraday_1m"),
            as_of=now,
        )
        data_fetch_duration += clock.perf_counter() - fetch_started
        readiness = {
            "daily": data_quality_diagnostics(daily, "1 day"),
            "hourly": data_quality_diagnostics(hourly, "1 hour"),
            "15m": data_quality_diagnostics(bars_15m, "15 mins"),
            "5m": data_quality_diagnostics(bars_5m, "5 mins"),
            "1m": data_quality_diagnostics(bars_1m, "1 min"),
        }
        readiness["paper_ready"] = (
            readiness["daily"]["completed_rows"] >= resolved_min_daily_rows
            and readiness["hourly"]["completed_rows"] >= 120
            and readiness["15m"]["completed_rows"] >= 200
            and readiness["daily"]["complete"]
            and readiness["hourly"]["complete"]
            and readiness["15m"]["complete"]
        )
        data_readiness[symbol] = readiness
        levels = _levels(plan)
        news, earnings, corporate, halt = _plan_risk_statuses(plan)
        raw_sector_regime = str(plan.get("sector_regime") or "").strip().upper()
        try:
            sector_regime = MarketRegime(raw_sector_regime)
        except ValueError:
            sector_regime = MarketRegime.UNKNOWN
        raw_sector_direction = str(plan.get("sector_trend_direction") or "").strip().upper()
        sector_direction = raw_sector_direction if raw_sector_direction in {"LONG", "SHORT"} else None
        session_entry_allowed = (
            anchor_date is None
            and now.astimezone(ET).weekday() < 5
            and time(9, 30) <= now.astimezone(ET).time() < time(15, 45)
        )
        raw_quote = quotes.get(symbol)
        context = StrategyContext(
            symbol=symbol,
            as_of=now,
            daily_bars=daily,
            hourly_bars=hourly,
            bars_15m=bars_15m,
            bars_5m=bars_5m or None,
            bars_1m=bars_1m or None,
            quote=_quote(raw_quote, now),
            levels=levels,
            market_regime=classify_market_regime(daily),
            market_trend_direction=classify_market_trend_direction(daily),
            sector_regime=sector_regime,
            sector_trend_direction=sector_direction,
            relative_strength_20d=(
                Decimal(str(plan["relative_strength_20d"]))
                if _number(plan.get("relative_strength_20d")) is not None
                else None
            ),
            news_status=news,
            earnings_status=earnings,
            corporate_action_status=corporate,
            halt_status=halt,
            account_state=_account_state(account),
            session_entry_allowed=session_entry_allowed,
        )
        candidates = evaluate_strategy(context, resolved_config)
        if (
            candidates
            and context.quote is None
            and quote_loader is not None
            and anchor_date is None
        ):
            quote_requests += 1
            try:
                fetched_quote = dict(quote_loader(symbol) or {})
                fetched_quote.setdefault("timestamp", now.isoformat())
                fetched = _quote(fetched_quote, now)
            except Exception as exc:
                LOGGER.warning("IRS quote fetch failed %s: %s", symbol, exc)
                fetched = None
            if fetched is not None:
                quotes_available += 1
                delayed_quotes += int(fetched.data_type != "live")
                context = replace(context, quote=fetched)
                candidates = evaluate_strategy(context, resolved_config)
        scanner_reasons = list(_liquidity_rejections(daily, resolved_config))
        if len([bar for bar in daily if bar.is_complete]) < resolved_min_daily_rows:
            scanner_reasons.append(RejectionReason.INSUFFICIENT_DAILY_HISTORY.value)
        if len([bar for bar in hourly if bar.is_complete]) < 120:
            scanner_reasons.append(RejectionReason.INSUFFICIENT_HOURLY_HISTORY.value)
        if len([bar for bar in bars_15m if bar.is_complete]) < 200:
            scanner_reasons.append(RejectionReason.INSUFFICIENT_15M_HISTORY.value)
        if readiness["hourly"]["missing_intervals"] or readiness["15m"]["missing_intervals"]:
            scanner_reasons.append(RejectionReason.INVALID_BAR_DATA.value)

        if not candidates:
            reasons = tuple(dict.fromkeys((*scanner_reasons, *_diagnose_no_candidate(hourly, levels, resolved_config))))
            reason_counts.update(reasons)
            rejected.append(
                {
                    "ticker": symbol,
                    "primary_reason": reasons[0],
                    "rejection_reasons": list(reasons),
                    "data_readiness": readiness,
                }
            )
            continue
        for candidate in candidates:
            enriched = _with_scanner_rejections(candidate, scanner_reasons)
            enriched = replace(
                enriched,
                diagnostics={
                    **enriched.diagnostics,
                    "config_snapshot": {
                        **asdict(resolved_config),
                        "min_daily_history_rows": resolved_min_daily_rows,
                    },
                },
            )
            row = candidate_row(enriched)
            candidate_count += 1
            zone_ids.add(enriched.zone.zone_id)
            if enriched.retrace_metrics.get("first_touch_time"):
                retrace_count += 1
            if enriched.confirmation is not None:
                confirmation_count += 1
            is_new_signal = True
            if resolved_store:
                is_new_signal = resolved_store.upsert_candidate(enriched, run_id=run_id)
                resolved_store.record_risk_snapshot(
                    signal_id=enriched.signal_id,
                    captured_at=now,
                    payload={
                        "account": account or {},
                        "quote": quotes.get(symbol) or {},
                        "market_regime": context.market_regime.value,
                        "market_trend_direction": context.market_trend_direction,
                        "sector_regime": context.sector_regime.value if context.sector_regime else None,
                        "sector_trend_direction": context.sector_trend_direction,
                        "session_entry_allowed": context.session_entry_allowed,
                        "hard_rejections": enriched.hard_rejections,
                    },
                )
                resolved_store.record_news_check(
                    signal_id=enriched.signal_id,
                    symbol=symbol,
                    checked_at=now,
                    news_status=context.news_status.value,
                    earnings_status=context.earnings_status.value,
                    payload={
                        "corporate_action_status": context.corporate_action_status.value,
                        "halt_status": context.halt_status.value,
                        "source": "watchlist_context",
                    },
                )
            if is_new_signal and enriched.state is SetupState.ENTRY_ARMED:
                newly_armed.append(row)
            if enriched.score >= resolved_config.minimum_display_score:
                rows.append(row)
            if enriched.hard_rejections:
                reason_counts.update(enriched.hard_rejections)
                rejected.append(row)

    rows.sort(key=lambda item: (-float(item["score"]), item["ticker"]))
    scan_duration = clock.perf_counter() - scan_started
    observability = {
        "irs_scanner_runs_total": 1,
        "irs_symbols_scanned_total": len(symbols),
        "irs_displacements_total": candidate_count,
        "irs_zones_created_total": len(zone_ids),
        "irs_retraces_total": retrace_count,
        "irs_confirmations_total": confirmation_count,
        "irs_candidates_total": candidate_count,
        "irs_quote_requests_total": quote_requests,
        "irs_quotes_available_total": quotes_available,
        "irs_delayed_quotes_total": delayed_quotes,
        "irs_rejections_total": sum(reason_counts.values()),
        "irs_rejections_by_reason": dict(reason_counts.most_common()),
        "irs_data_stale_total": int(
            sum(
                1
                for readiness in data_readiness.values()
                if readiness["hourly"]["missing_intervals"]
                or readiness["15m"]["missing_intervals"]
            )
        ),
        "irs_news_fail_closed_total": reason_counts[
            RejectionReason.NEWS_STATUS_UNAVAILABLE.value
        ],
        "irs_scan_duration_seconds": round(scan_duration, 6),
        "irs_data_fetch_duration_seconds": round(data_fetch_duration, 6),
    }
    result = {
        "run_id": run_id,
        "scan_timestamp": now.isoformat(),
        "anchor_date": anchor_date,
        "strategy": "INEFFICIENCY_RECLAIM",
        "strategy_version": resolved_config.version,
        "min_daily_history_rows": resolved_min_daily_rows,
        "strategy_enabled": SETTINGS.inefficiency_reclaim.enabled,
        "paper_live_mode": "PAPER",
        "analysis_only": True,
        "universe_size": len(symbols),
        "signals_found": len(rows),
        "signals": rows,
        "rejected": rejected,
        "reason_counts": dict(reason_counts.most_common()),
        "newly_armed": newly_armed,
        "observability": observability,
        "data_readiness": data_readiness,
        "active_setups": resolved_store.active_setups() if resolved_store else [],
        "execution_block": (
            "INEFFICIENCY_RECLAIM_ENABLED=false"
            if not SETTINGS.inefficiency_reclaim.enabled
            else "Paper execution requires an ENTRY_ARMED candidate and all fresh risk gates."
        ),
    }
    if resolved_store:
        resolved_store.finish_scanner_run(
            run_id,
            finished_at=now,
            status="COMPLETED",
            symbols_scanned=len(symbols),
            candidates=len(rows),
            rejections=len(rejected),
            diagnostics={
                "reason_counts": result["reason_counts"],
                "observability": observability,
            },
        )
    LOGGER.info(
        "IRS scan run=%s symbols=%s display=%s rejected=%s enabled=%s",
        run_id,
        len(symbols),
        len(rows),
        len(rejected),
        SETTINGS.inefficiency_reclaim.enabled,
    )
    return result
