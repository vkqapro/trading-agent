"""Gerchik-style one-bar false breakout detection helpers with explainability."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Literal, Optional, Sequence

import pandas as pd

from src.config import LOGGER
from src.risk.take_profit import reward_risk_ratio
from src.strategy.candles import (
    body_size,
    close_location,
    full_range,
    is_bearish,
    is_bullish,
    lower_wick,
    upper_wick,
)
from src.strategy.levels import Level
from src.strategy.signal_models import TradeSignal
from src.strategy.trend import detect_trend


SignalSide = Literal["BUY", "SELL", "NONE"]
Direction = Literal["long", "short"]
PatternName = Literal["ONE_BAR", "TWO_BAR", "COMPLEX", "NONE"]

DEFAULT_CONFIG: Dict[str, float] = {
    "zone_buffer_pct": 0.0015,
    "minimum_target_atr": 1.0,
    "exhausted_move_atr_pct": 0.7,
    "score_threshold": 60.0,
    "confirmation_close_location": 0.55,
    "volatility_spike_atr_multiplier": 1.8,
}

DAILY_DECISIONS_PATH = Path(__file__).resolve().parents[2] / "memory" / "daily_decisions.json"
DAILY_DECISIONS_LOCK_PATH = DAILY_DECISIONS_PATH.with_suffix(".json.lock")
MAX_STORED_ATTEMPTS = 100
DAILY_DECISIONS_WRITE_ATTEMPTS = 8
DAILY_DECISIONS_WRITE_DELAY_SECONDS = 0.1
DAILY_DECISIONS_LOCK_STALE_SECONDS = 30.0


@dataclass(frozen=True)
class ZoneContext:
    level_price: float
    zone_low: float
    zone_high: float
    center: float


def _merged_config(config: Optional[Dict[str, float]]) -> Dict[str, float]:
    merged = dict(DEFAULT_CONFIG)
    if config:
        merged.update(config)
    return merged


def _normalize_candles(candles: pd.DataFrame) -> pd.DataFrame:
    normalized = candles.copy()
    if "datetime" in normalized.columns and not normalized["datetime"].isna().all():
        normalized = normalized.sort_values("datetime").reset_index(drop=True)
    return normalized.reset_index(drop=True)


def _zone_from_level(level: Level, config: Dict[str, float]) -> ZoneContext:
    if level.zone_low is not None and level.zone_high is not None:
        zone_low = float(level.zone_low)
        zone_high = float(level.zone_high)
    else:
        buffer = max(abs(level.price) * config["zone_buffer_pct"], 0.01)
        zone_low = float(level.price) - buffer
        zone_high = float(level.price) + buffer
    center = float(level.center) if level.center is not None else (zone_low + zone_high) / 2.0
    return ZoneContext(level_price=float(level.price), zone_low=zone_low, zone_high=zone_high, center=center)


def _calculate_atr_from_intraday(candles: pd.DataFrame, period: int = 6) -> float:
    if candles.empty:
        return 0.0
    sample = candles.tail(max(period, 3)).copy()
    sample["range"] = sample["high"].astype(float) - sample["low"].astype(float)
    average_range = float(sample["range"].mean())
    if average_range <= 0:
        return 0.0
    filtered = sample[(sample["range"] < average_range * 2.0) & (sample["range"] > average_range / 3.0)]
    if filtered.empty:
        filtered = sample
    return round(float(filtered["range"].mean()), 4)


def _resolve_atr(candles: pd.DataFrame, level: Level, atr: Optional[float]) -> float:
    if atr is not None and atr > 0:
        return float(atr)
    if level.atr_value > 0:
        return float(level.atr_value)
    return _calculate_atr_from_intraday(candles)


def _determine_news_risk(news_context: Optional[Dict[str, object]]) -> str:
    if not news_context:
        return "LOW"
    risk_level = str(news_context.get("risk_level", "")).upper()
    if risk_level in {"LOW", "MEDIUM", "HIGH"}:
        return risk_level
    if bool(news_context.get("blocked")):
        return "HIGH"
    if news_context.get("matched_headlines"):
        return "MEDIUM"
    return "LOW"


def _confirmation_count(news_risk: str) -> int:
    return 2 if news_risk == "MEDIUM" else 1


def _position_modifier(news_risk: str) -> float:
    return 0.5 if news_risk == "MEDIUM" else 1.0


def _trend_label(candles: pd.DataFrame) -> str:
    trend = detect_trend(candles, lookback=min(len(candles), 6)) if len(candles) >= 4 else "range"
    if trend == "uptrend":
        return "UP"
    if trend == "downtrend":
        return "DOWN"
    return "RANGE"


def _trend_context_ok(candles: pd.DataFrame, direction: Direction, atr_value: float) -> bool:
    if len(candles) < 4:
        return False
    trend = detect_trend(candles, lookback=min(len(candles), 6))
    closes = candles["close"].astype(float).tolist()
    move = closes[-1] - closes[0]
    minimum_directional_move = atr_value * 0.2 if atr_value > 0 else 0.2
    if abs(move) < minimum_directional_move:
        LOGGER.info("Rejected false breakout: flat/noisy market move=%.4f atr=%.4f", move, atr_value)
        return False
    if direction == "long":
        return trend in {"downtrend", "trend_break"} or move < 0
    return trend in {"uptrend", "trend_break"} or move > 0


def _confirmation_is_strong(candle: pd.Series, direction: Direction, minimum_close_location: float) -> bool:
    if full_range(candle) <= 0:
        return False
    if direction == "long":
        return is_bullish(candle) and close_location(candle) >= minimum_close_location
    return is_bearish(candle) and close_location(candle) <= (1.0 - minimum_close_location)


def _confirmations_ok(confirmations: Sequence[pd.Series], direction: Direction, minimum_close_location: float) -> bool:
    return all(_confirmation_is_strong(candle, direction, minimum_close_location) for candle in confirmations)


def _target_for_direction(level: Level, direction: Direction, entry: float, stop: float) -> Optional[float]:
    next_level = level.nearest_upper_level if direction == "long" else level.nearest_lower_level
    if isinstance(next_level, float) and reward_risk_ratio(entry, stop, next_level) >= 2.0:
        return round(next_level, 2)
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    fallback_target = entry + (3 * risk if direction == "long" else -3 * risk)
    return round(fallback_target, 2)


def _evaluate_atr_status(entry: float, target: float, zone: ZoneContext, atr_value: float, config: Dict[str, float]) -> str:
    if atr_value <= 0:
        return "LOW"
    target_distance = abs(target - entry)
    if target_distance < atr_value * config["minimum_target_atr"]:
        return "LOW"
    if abs(entry - zone.center) > atr_value * config["exhausted_move_atr_pct"]:
        return "OVEREXTENDED"
    return "OK"


def _atr_filters_ok(entry: float, target: float, zone: ZoneContext, atr_value: float, config: Dict[str, float]) -> bool:
    return _evaluate_atr_status(entry, target, zone, atr_value, config) == "OK"


def _is_volatility_spike(candles: Iterable[pd.Series], atr_value: float, config: Dict[str, float]) -> bool:
    if atr_value <= 0:
        return False
    return any(full_range(candle) > atr_value * config["volatility_spike_atr_multiplier"] for candle in candles)


def _build_context(
    *,
    trend: str,
    atr_status: str,
    news_risk: str,
    zone: ZoneContext,
    pattern: PatternName,
) -> Dict[str, object]:
    return {
        "trend": trend,
        "atr_status": atr_status,
        "news_risk": news_risk,
        "zone": [round(zone.zone_low, 2), round(zone.zone_high, 2)],
        "pattern": pattern,
    }


def _dedupe_reasons(reasons: Sequence[str]) -> List[str]:
    deduped: List[str] = []
    for reason in reasons:
        normalized = reason.strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _build_signal_dict(
    *,
    signal: SignalSide,
    entry: Optional[float],
    stop: Optional[float],
    target: Optional[float],
    news_risk: str,
    score: float,
    reasons: Sequence[str],
    context: Dict[str, object],
) -> Dict[str, object]:
    return {
        "signal": signal,
        "entry": round(entry, 2) if entry is not None else None,
        "stop": round(stop, 2) if stop is not None else None,
        "target": round(target, 2) if target is not None else None,
        "position_modifier": _position_modifier(news_risk),
        "confidence": round(min(score, 100.0) / 100.0, 2),
        "reason": _dedupe_reasons(reasons),
        "context": context,
    }


def _empty_result(reasons: Sequence[str] | str, context: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    reason_list = [reasons] if isinstance(reasons, str) else list(reasons)
    return {
        "signal": "NONE",
        "entry": None,
        "stop": None,
        "target": None,
        "position_modifier": 1.0,
        "confidence": 0.0,
        "reason": _dedupe_reasons(reason_list),
        "context": context or {
            "trend": "RANGE",
            "atr_status": "LOW",
            "news_risk": "LOW",
            "zone": [0.0, 0.0],
            "pattern": "NONE",
        },
    }


def _long_score(false_candle: pd.Series, confirmations: Sequence[pd.Series], zone: ZoneContext, atr_value: float) -> float:
    wick_ratio = lower_wick(false_candle) / max(body_size(false_candle), 0.01)
    score = 35.0
    score += min(20.0, wick_ratio * 8.0)
    score += 10.0 if float(false_candle["close"]) > zone.zone_low else 0.0
    score += 10.0 if all(float(candle["close"]) >= zone.zone_low for candle in confirmations) else 0.0
    score += 10.0 if float(confirmations[-1]["close"]) > float(confirmations[0]["close"]) else 0.0
    if atr_value > 0 and abs(float(confirmations[-1]["close"]) - zone.center) <= atr_value * 0.5:
        score += 10.0
    return score


def _short_score(false_candle: pd.Series, confirmations: Sequence[pd.Series], zone: ZoneContext, atr_value: float) -> float:
    wick_ratio = upper_wick(false_candle) / max(body_size(false_candle), 0.01)
    score = 35.0
    score += min(20.0, wick_ratio * 8.0)
    score += 10.0 if float(false_candle["close"]) < zone.zone_high else 0.0
    score += 10.0 if all(float(candle["close"]) <= zone.zone_high for candle in confirmations) else 0.0
    score += 10.0 if float(confirmations[-1]["close"]) < float(confirmations[0]["close"]) else 0.0
    if atr_value > 0 and abs(float(confirmations[-1]["close"]) - zone.center) <= atr_value * 0.5:
        score += 10.0
    return score


def _decision_rank(result: Dict[str, object]) -> tuple[int, float]:
    router_status = str(result.get("router_status", "") or "").strip().lower()
    if router_status == "accepted" and result.get("signal") in {"BUY", "SELL"}:
        signal_rank = 3
    elif router_status == "rejected":
        signal_rank = 2
    else:
        signal_rank = 1 if result.get("signal") in {"BUY", "SELL"} else 0
    return signal_rank, float(result.get("confidence", 0.0))


def _load_daily_decisions() -> Dict[str, object]:
    today = date.today().isoformat()
    default_payload: Dict[str, object] = {"date": today, "decisions": {}}
    if not DAILY_DECISIONS_PATH.exists():
        return default_payload
    try:
        with DAILY_DECISIONS_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError):
        LOGGER.warning("Daily decisions file is invalid: %s", DAILY_DECISIONS_PATH)
        return default_payload
    if str(payload.get("date")) != today:
        return default_payload
    return payload if isinstance(payload, dict) else default_payload


def _lock_is_stale(lock_path: Path) -> bool:
    try:
        modified_at = lock_path.stat().st_mtime
    except OSError:
        return False
    return (time.time() - modified_at) > DAILY_DECISIONS_LOCK_STALE_SECONDS


def _acquire_daily_decisions_lock() -> int:
    DAILY_DECISIONS_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    last_error: OSError | None = None
    for attempt in range(DAILY_DECISIONS_WRITE_ATTEMPTS):
        try:
            handle = os.open(str(DAILY_DECISIONS_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(handle, f"{os.getpid()}|{datetime.now().isoformat()}".encode("utf-8"))
            return handle
        except FileExistsError as exc:
            last_error = exc
            if _lock_is_stale(DAILY_DECISIONS_LOCK_PATH):
                try:
                    DAILY_DECISIONS_LOCK_PATH.unlink()
                    LOGGER.warning("Recovered stale daily decisions lock: %s", DAILY_DECISIONS_LOCK_PATH.name)
                    continue
                except OSError:
                    pass
            time.sleep(DAILY_DECISIONS_WRITE_DELAY_SECONDS * (attempt + 1))
    raise TimeoutError(f"Could not acquire daily decisions lock: {last_error}")


def _release_daily_decisions_lock(handle: int) -> None:
    os.close(handle)
    try:
        DAILY_DECISIONS_LOCK_PATH.unlink()
    except OSError:
        LOGGER.warning("Failed to remove daily decisions lock: %s", DAILY_DECISIONS_LOCK_PATH)


def _save_daily_decisions(payload: Dict[str, object]) -> bool:
    DAILY_DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = DAILY_DECISIONS_PATH.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    for attempt in range(DAILY_DECISIONS_WRITE_ATTEMPTS):
        try:
            os.replace(temp_path, DAILY_DECISIONS_PATH)
            return True
        except PermissionError:
            if attempt == DAILY_DECISIONS_WRITE_ATTEMPTS - 1:
                break
            time.sleep(DAILY_DECISIONS_WRITE_DELAY_SECONDS * (attempt + 1))
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {5, 32} or attempt == DAILY_DECISIONS_WRITE_ATTEMPTS - 1:
                break
            time.sleep(DAILY_DECISIONS_WRITE_DELAY_SECONDS * (attempt + 1))
    LOGGER.warning("Failed to replace daily decisions file after retries: %s", DAILY_DECISIONS_PATH)
    try:
        temp_path.unlink()
    except OSError:
        pass
    return False


def _persist_daily_decision(symbol: str, level: Level, strategy_name: str, result: Dict[str, object]) -> None:
    lock_handle: int | None = None
    try:
        lock_handle = _acquire_daily_decisions_lock()
        payload = _load_daily_decisions()
        decisions = payload.setdefault("decisions", {})
        symbol_payload = decisions.setdefault(symbol, {"symbol": symbol, "attempts": [], "best_decision": None})
        attempt = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "strategy": strategy_name,
            "level": round(level.price, 2),
            "level_type": level.type,
            "signal": result.get("signal"),
            "entry": result.get("entry"),
            "stop": result.get("stop"),
            "target": result.get("target"),
            "position_modifier": result.get("position_modifier"),
            "confidence": result.get("confidence"),
            "reason": list(result.get("reason", [])),
            "context": dict(result.get("context", {})),
        }
        for optional_key in ("router_status", "raw_signal"):
            if result.get(optional_key) is not None:
                attempt[optional_key] = result.get(optional_key)
        attempts = symbol_payload.setdefault("attempts", [])
        if isinstance(attempts, list):
            attempts.append(attempt)
            if len(attempts) > MAX_STORED_ATTEMPTS:
                del attempts[:-MAX_STORED_ATTEMPTS]
        best_decision = symbol_payload.get("best_decision")
        if not isinstance(best_decision, dict) or _decision_rank(result) >= _decision_rank(best_decision):
            symbol_payload["best_decision"] = attempt
        if _save_daily_decisions(payload):
            LOGGER.info(
                "Stored daily decision symbol=%s strategy=%s signal=%s reasons=%s context=%s",
                symbol,
                strategy_name,
                result.get("signal"),
                result.get("reason"),
                result.get("context"),
            )
        else:
            LOGGER.warning(
                "Skipped persisting daily decision after write failure symbol=%s strategy=%s signal=%s",
                symbol,
                strategy_name,
                result.get("signal"),
            )
    except TimeoutError:
        LOGGER.warning("Skipped persisting daily decision due to lock timeout symbol=%s strategy=%s", symbol, strategy_name)
    finally:
        if lock_handle is not None:
            _release_daily_decisions_lock(lock_handle)


def _dict_to_trade_signal(symbol: str, level: Level, strategy_name: str, result: Dict[str, object]) -> Optional[TradeSignal]:
    if result.get("signal") not in {"BUY", "SELL"}:
        return None
    if result.get("entry") is None or result.get("stop") is None or result.get("target") is None:
        return None
    direction = "long" if result["signal"] == "BUY" else "short"
    context = result.get("context", {})
    reasons = ", ".join(str(reason) for reason in result.get("reason", []))
    notes = [
        f"reasons={reasons}" if reasons else "reasons=none",
        f"confidence={result.get('confidence', 0.0)}",
        f"position_modifier={result.get('position_modifier', 1.0)}",
        (
            f"context trend={context.get('trend')} atr={context.get('atr_status')} "
            f"news={context.get('news_risk')} pattern={context.get('pattern')}"
        ),
    ]
    return TradeSignal(
        symbol=symbol,
        strategy=strategy_name,
        signal=str(result["signal"]),
        direction=direction,
        entry=float(result["entry"]),
        stop=float(result["stop"]),
        target=float(result["target"]),
        level_price=level.price,
        level_type=level.type,
        nearest_upper_level=level.nearest_upper_level,
        nearest_lower_level=level.nearest_lower_level,
        notes=notes,
    )


def detect_false_breakout(
    candles: pd.DataFrame,
    level: Level,
    atr: Optional[float] = None,
    news_context: Optional[Dict[str, object]] = None,
    config: Optional[Dict[str, float]] = None,
) -> Dict[str, object]:
    """Detect a one-bar false breakout using a zone-aware, ATR-filtered workflow."""
    merged_config = _merged_config(config)
    normalized = _normalize_candles(candles)
    news_risk = _determine_news_risk(news_context)
    zone = _zone_from_level(level, merged_config)

    if len(normalized) < 3:
        context = _build_context(trend="RANGE", atr_status="LOW", news_risk=news_risk, zone=zone, pattern="ONE_BAR")
        return _empty_result(["insufficient candles"], context)

    confirmation_count = _confirmation_count(news_risk)
    if len(normalized) < confirmation_count + 2:
        context = _build_context(trend="RANGE", atr_status="LOW", news_risk=news_risk, zone=zone, pattern="ONE_BAR")
        return _empty_result(["insufficient candles"], context)

    false_candle = normalized.iloc[-(confirmation_count + 1)]
    confirmations = [normalized.iloc[-idx] for idx in range(confirmation_count, 0, -1)]
    prior_context = normalized.iloc[: -(confirmation_count + 1)]
    if len(prior_context) < 4:
        prior_context = normalized.iloc[: -(confirmation_count)]

    atr_value = _resolve_atr(normalized, level, atr)
    trend_label = _trend_label(prior_context if len(prior_context) >= 4 else normalized.head(len(normalized) - confirmation_count))
    context = _build_context(
        trend=trend_label,
        atr_status="OK" if atr_value > 0 else "LOW",
        news_risk=news_risk,
        zone=zone,
        pattern="ONE_BAR",
    )
    LOGGER.info(
        "One-bar false breakout level=%.2f zone=(%.2f, %.2f) atr=%.4f news_risk=%s",
        zone.level_price,
        zone.zone_low,
        zone.zone_high,
        atr_value,
        news_risk,
    )

    reasons: List[str] = []
    if news_risk == "HIGH":
        reasons.append("news risk high")
        return _empty_result(reasons, context)

    if _is_volatility_spike([false_candle, *confirmations], atr_value, merged_config):
        reasons.append("abnormal volatility spike")
        return _empty_result(reasons, context)

    min_close_location = merged_config["confirmation_close_location"]

    broke_below = float(false_candle["low"]) < zone.zone_low
    returned_above = float(false_candle["close"]) > zone.zone_low
    strong_lower_rejection = lower_wick(false_candle) > body_size(false_candle)
    if broke_below and returned_above and strong_lower_rejection:
        if not _confirmations_ok(confirmations, "long", min_close_location):
            reasons.append("no confirmation candle")
            return _empty_result(reasons, context)
        if not _trend_context_ok(prior_context if len(prior_context) >= 4 else normalized.head(len(normalized) - confirmation_count), "long", atr_value):
            reasons.append("trend mismatch")
            return _empty_result(reasons, context)
        entry = float(confirmations[-1]["close"])
        stop = min(float(false_candle["low"]), *(float(candle["low"]) for candle in confirmations))
        target = _target_for_direction(level, "long", entry, stop)
        if target is None:
            reasons.append("ATR too small")
            context["atr_status"] = "LOW"
            return _empty_result(reasons, context)
        context["atr_status"] = _evaluate_atr_status(entry, target, zone, atr_value, merged_config)
        if context["atr_status"] == "LOW":
            reasons.append("ATR too small")
            return _empty_result(reasons, context)
        if context["atr_status"] == "OVEREXTENDED":
            reasons.append("move already extended")
            return _empty_result(reasons, context)
        score = _long_score(false_candle, confirmations, zone, atr_value)
        if score < merged_config["score_threshold"]:
            reasons.append("weak setup score")
            return _empty_result(reasons, context)
        reasons.append("clean false breakout return")
        LOGGER.info("Detected one-bar long false breakout at %.2f score=%.2f", entry, score)
        return _build_signal_dict(
            signal="BUY",
            entry=entry,
            stop=stop,
            target=target,
            news_risk=news_risk,
            score=score,
            reasons=reasons,
            context=context,
        )

    broke_above = float(false_candle["high"]) > zone.zone_high
    returned_below = float(false_candle["close"]) < zone.zone_high
    strong_upper_rejection = upper_wick(false_candle) > body_size(false_candle)
    if broke_above and returned_below and strong_upper_rejection:
        if not _confirmations_ok(confirmations, "short", min_close_location):
            reasons.append("no confirmation candle")
            return _empty_result(reasons, context)
        if not _trend_context_ok(prior_context if len(prior_context) >= 4 else normalized.head(len(normalized) - confirmation_count), "short", atr_value):
            reasons.append("trend mismatch")
            return _empty_result(reasons, context)
        entry = float(confirmations[-1]["close"])
        stop = max(float(false_candle["high"]), *(float(candle["high"]) for candle in confirmations))
        target = _target_for_direction(level, "short", entry, stop)
        if target is None:
            reasons.append("ATR too small")
            context["atr_status"] = "LOW"
            return _empty_result(reasons, context)
        context["atr_status"] = _evaluate_atr_status(entry, target, zone, atr_value, merged_config)
        if context["atr_status"] == "LOW":
            reasons.append("ATR too small")
            return _empty_result(reasons, context)
        if context["atr_status"] == "OVEREXTENDED":
            reasons.append("move already extended")
            return _empty_result(reasons, context)
        score = _short_score(false_candle, confirmations, zone, atr_value)
        if score < merged_config["score_threshold"]:
            reasons.append("weak setup score")
            return _empty_result(reasons, context)
        reasons.append("clean false breakout return")
        LOGGER.info("Detected one-bar short false breakout at %.2f score=%.2f", entry, score)
        return _build_signal_dict(
            signal="SELL",
            entry=entry,
            stop=stop,
            target=target,
            news_risk=news_risk,
            score=score,
            reasons=reasons,
            context=context,
        )

    if not broke_below and not broke_above:
        reasons.append("no breakout")
    elif broke_below and not returned_above:
        reasons.append("no return inside zone")
    elif broke_above and not returned_below:
        reasons.append("no return inside zone")
    else:
        reasons.append("weak rejection wick")
    return _empty_result(reasons, context)


def detect_false_breakout_one_bar(
    symbol: str,
    bars: pd.DataFrame,
    level: Level,
    atr: Optional[float] = None,
    news_context: Optional[Dict[str, object]] = None,
    config: Optional[Dict[str, float]] = None,
) -> Optional[TradeSignal]:
    """Router-compatible wrapper returning a TradeSignal for one-bar false breakouts."""
    result = detect_false_breakout(bars, level, atr=atr, news_context=news_context, config=config)
    _persist_daily_decision(symbol, level, "false_breakout_one_bar", result)
    return _dict_to_trade_signal(symbol, level, "false_breakout_one_bar", result)
