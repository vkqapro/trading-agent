"""Universal trade validation rules."""

from __future__ import annotations

from typing import Dict, List, Tuple

from src.config import SETTINGS
from src.risk.position_size import position_value_ok
from src.risk.take_profit import reward_risk_ratio


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_trade(
    signal: Dict[str, object],
    account_equity: float,
    current_positions: List[Dict[str, object]],
    open_risk_amount: float,
    spread_pct: float,
    *,
    paper_trading: bool,
    tws_connected: bool,
    account_synced: bool,
    market_open: bool,
    first_unstable_minutes: bool,
    allow_first_unstable_minutes: bool,
    has_symbol_news_risk: bool = False,
    has_macro_risk: bool = False,
    atr_has_room: bool = True,
    order_size_valid: bool = True,
    cash_available: float | None = None,
) -> Tuple[bool, List[str]]:
    """Validate a trade candidate against hard trading rules."""
    reasons: List[str] = []
    symbol = str(signal.get("symbol", ""))
    side = str(signal.get("signal", "")).upper()
    entry = _optional_float(signal.get("entry")) or 0.0
    stop_price = _optional_float(signal.get("stop", signal.get("stop_loss"))) or 0.0
    target = _optional_float(signal.get("target")) or 0.0
    quantity = int(signal.get("quantity", 0) or 0)
    risk_amount = _optional_float(signal.get("risk_amount"))
    if risk_amount is None:
        risk_amount = abs(entry - stop_price) * quantity
    risk_per_share = _optional_float(signal.get("risk_per_share"))
    if risk_per_share is None:
        risk_per_share = abs(entry - stop_price)
    next_level = signal.get("next_major_level")
    next_level_price = _optional_float(next_level)
    level_strength = _optional_float(signal.get("level_strength"))
    atr_used = _optional_float(signal.get("atr_used"))
    is_new_extreme = bool(signal.get("is_new_extreme", False))

    if side not in {"BUY", "SELL"}:
        reasons.append("missing_signal")
    if entry <= 0:
        reasons.append("missing_entry")
    if not paper_trading:
        reasons.append("paper_trading_disabled")
    if not tws_connected:
        reasons.append("tws_disconnected")
    if not account_synced:
        reasons.append("account_not_synced")
    if not market_open:
        reasons.append("market_closed")
    if first_unstable_minutes and not allow_first_unstable_minutes:
        reasons.append("unstable_open_window")
    if any(position.get("symbol") == symbol for position in current_positions):
        reasons.append("duplicate_position")
    if len(current_positions) >= SETTINGS.risk.max_positions:
        reasons.append("max_positions_reached")
    if risk_amount > account_equity * SETTINGS.risk.risk_per_trade:
        reasons.append("risk_per_trade_exceeded")
    if open_risk_amount + risk_amount > account_equity * SETTINGS.risk.max_open_risk_pct:
        reasons.append("open_risk_limit_exceeded")
    if not stop_price:
        reasons.append("missing_stop_loss")
    if not target:
        reasons.append("missing_target")
    if risk_per_share <= 0:
        reasons.append("invalid_risk_per_share")
    if reward_risk_ratio(entry, stop_price, target) < SETTINGS.risk.min_reward_risk_ratio:
        reasons.append("reward_risk_too_low")
    if next_level_price is not None:
        if signal.get("direction") == "long" and target > next_level_price:
            reasons.append("target_beyond_next_major_level")
        if signal.get("direction") == "short" and target < next_level_price:
            reasons.append("target_beyond_next_major_level")
    if spread_pct > SETTINGS.risk.max_spread_pct:
        reasons.append("spread_too_wide")
    if not atr_has_room:
        reasons.append("atr_room_too_small")
    if atr_used is not None and atr_used > SETTINGS.strategy.atr_travel_limit_pct and not is_new_extreme:
        reasons.append("atr_travel_exhausted")
    if level_strength is not None and level_strength < SETTINGS.strategy.level_strength_threshold:
        reasons.append("level_strength_too_low")
    if has_symbol_news_risk:
        reasons.append("high_risk_news")
    if has_macro_risk:
        reasons.append("macro_risk")
    if not order_size_valid:
        reasons.append("order_size_invalid")
    if not position_value_ok(quantity, entry, SETTINGS.risk.max_position_value, cash_available):
        reasons.append("position_value_invalid")
    return len(reasons) == 0, reasons


def validate_trade_result(*args: object, **kwargs: object) -> Dict[str, object]:
    """Explainable validator wrapper that preserves deterministic reason tracking."""
    valid, reasons = validate_trade(*args, **kwargs)
    return {"valid": valid, "reasons": reasons}
