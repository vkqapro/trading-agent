"""Universal trade validation rules."""

from __future__ import annotations

from typing import Dict, List, Tuple

from src.config import SETTINGS
from src.risk.position_size import position_value_ok
from src.risk.take_profit import reward_risk_ratio


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
    entry = float(signal.get("entry", 0.0))
    stop_price = float(signal.get("stop", signal.get("stop_loss", 0.0)))
    target = float(signal.get("target", 0.0))
    risk_amount = float(signal.get("risk_amount", abs(entry - stop_price) * float(signal.get("quantity", 0.0))))
    quantity = int(signal.get("quantity", 0))
    next_level = signal.get("next_major_level")

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
    if reward_risk_ratio(entry, stop_price, target) < SETTINGS.risk.min_reward_risk_ratio:
        reasons.append("reward_risk_too_low")
    if next_level is not None:
        if signal.get("direction") == "long" and target > float(next_level):
            reasons.append("target_beyond_next_major_level")
        if signal.get("direction") == "short" and target < float(next_level):
            reasons.append("target_beyond_next_major_level")
    if spread_pct > SETTINGS.risk.max_spread_pct:
        reasons.append("spread_too_wide")
    if not atr_has_room:
        reasons.append("atr_room_too_small")
    if has_symbol_news_risk:
        reasons.append("high_risk_news")
    if has_macro_risk:
        reasons.append("macro_risk")
    if not order_size_valid:
        reasons.append("order_size_invalid")
    if not position_value_ok(quantity, entry, SETTINGS.risk.max_position_value, cash_available):
        reasons.append("position_value_invalid")
    return len(reasons) == 0, reasons
