"""Position sizing logic."""

from __future__ import annotations

import math


def calculate_position_size(account_equity: float, risk_pct: float, entry_price: float, stop_loss: float) -> int:
    """Size a position so the loss to stop equals the allowed risk budget."""
    risk_per_share = abs(entry_price - stop_loss)
    if account_equity <= 0 or risk_pct <= 0 or risk_per_share <= 0:
        return 0
    total_risk_budget = account_equity * risk_pct
    shares = math.floor(total_risk_budget / risk_per_share)
    return max(shares, 0)
