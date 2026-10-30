"""Durable, isolated paper portfolio for ``paper_autonomous`` mode."""

from __future__ import annotations

import json
import math
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .models import DecisionCandidate


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


class PaperPortfolio:
    """A crash-safe ledger that never calls a broker or mutates bot state."""

    def __init__(self, path: str | Path, *, starting_equity: float = 100_000.0) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load(starting_equity)
        self._mark_to_market()

    @staticmethod
    def _default(starting_equity: float) -> dict[str, object]:
        equity = max(0.0, float(starting_equity))
        return {
            "version": 1,
            "owner": "LLM_AGENT",
            "starting_equity": equity,
            "cash": equity,
            "equity": equity,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "daily_R": 0.0,
            "total_R": 0.0,
            "risk_in_use": 0.0,
            "trades_today": 0,
            "positions": [],
            "closed_positions": [],
            "updated_at": _now(),
        }

    def _load(self, starting_equity: float) -> dict[str, object]:
        if not self.path.exists():
            data = self._default(starting_equity)
            self._save(data)
            return data
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"paper portfolio state is unreadable: {self.path}") from exc
        if not isinstance(data, dict) or data.get("owner") != "LLM_AGENT":
            raise RuntimeError("paper portfolio ownership marker is invalid")
        return data

    def _save(self, data: dict[str, object] | None = None) -> None:
        payload = data or self._data
        payload["updated_at"] = _now()
        temporary = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary_name = temporary.name
        try:
            with temporary:
                json.dump(payload, temporary, indent=2, sort_keys=True)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self.path)
        finally:
            Path(temporary_name).unlink(missing_ok=True)

    def state(self) -> dict[str, object]:
        return json.loads(json.dumps(self._data))

    def positions(self) -> list[dict[str, object]]:
        """Return a detached snapshot for the deterministic safety loop."""
        return [dict(item) for item in self._data.setdefault("positions", [])]

    def _mark_to_market(self, quotes: Mapping[str, Mapping[str, object]] | None = None) -> None:
        quotes = quotes or {}
        unrealized = 0.0
        position_value = 0.0
        risk_in_use = 0.0
        for position in self._data.get("positions", []):
            symbol = str(position.get("symbol", "")).upper()
            quote = quotes.get(symbol, {})
            current = _number(quote.get("last"), _number(position.get("current_price"), _number(position.get("entry"))))
            position["current_price"] = current
            quantity = _number(position.get("quantity"))
            entry = _number(position.get("entry"))
            direction = str(position.get("direction", "long")).lower()
            pnl = (current - entry) * quantity if direction == "long" else (entry - current) * quantity
            position["unrealized_pnl"] = pnl
            unrealized += pnl
            position_value += abs(current * quantity)
            risk_in_use += _number(position.get("risk_amount"))
        self._data["unrealized_pnl"] = unrealized
        self._data["risk_in_use"] = risk_in_use
        self._data["equity"] = _number(self._data.get("cash")) + position_value + unrealized

    def mark_to_market(self, quotes: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
        self._mark_to_market(quotes)
        self._save()
        return self.state()

    def enter(
        self,
        candidate: DecisionCandidate,
        *,
        quantity: int,
        quote: Mapping[str, object],
        slippage_pct: float = 0.0,
        commission_per_share: float = 0.0,
    ) -> dict[str, object]:
        quantity = int(quantity)
        if quantity <= 0:
            raise ValueError("paper quantity must be positive")
        positions = self._data.setdefault("positions", [])
        if any(str(item.get("symbol", "")).upper() == candidate.symbol.upper() for item in positions):
            raise ValueError(f"symbol already owned by LLM_AGENT: {candidate.symbol.upper()}")
        if slippage_pct < 0 or commission_per_share < 0:
            raise ValueError("paper execution costs cannot be negative")
        direction = candidate.direction.lower()
        raw_price = _number(quote.get("ask" if direction == "long" else "bid"), candidate.entry)
        if raw_price <= 0:
            raise ValueError("paper fill quote is invalid")
        fill_price = raw_price * (1.0 + slippage_pct if direction == "long" else 1.0 - slippage_pct)
        fees = quantity * commission_per_share
        notional = fill_price * quantity
        if _number(self._data.get("cash")) < notional + fees:
            raise ValueError("paper portfolio has insufficient cash")
        position_id = f"llm-paper-{uuid.uuid4().hex}"
        position = {
            "position_id": position_id,
            "owner": "LLM_AGENT",
            "candidate_id": candidate.candidate_id,
            "symbol": candidate.symbol.upper(),
            "strategy": candidate.strategy,
            "direction": direction,
            "quantity": quantity,
            "entry": fill_price,
            "stop_loss": candidate.stop,
            "target": candidate.target,
            "risk_per_share": candidate.risk_per_share or abs(candidate.entry - candidate.stop),
            "risk_amount": abs(fill_price - candidate.stop) * quantity,
            "opened_at": _now(),
            "current_price": fill_price,
            "fees": fees,
            "protection_policy": "deterministic_paper_stop_target",
        }
        self._data["cash"] = _number(self._data.get("cash")) - notional - fees
        positions.append(position)
        self._data["trades_today"] = int(self._data.get("trades_today", 0)) + 1
        self._mark_to_market()
        self._save()
        return dict(position)

    def close(
        self,
        position_id: str,
        *,
        price: float,
        reason: str,
        quantity: int | None = None,
        commission_per_share: float = 0.0,
    ) -> dict[str, object]:
        positions = self._data.setdefault("positions", [])
        position = next((item for item in positions if item.get("position_id") == position_id), None)
        if position is None:
            raise ValueError(f"paper position not found: {position_id}")
        exit_price = _number(price)
        if exit_price <= 0:
            raise ValueError("paper close price is invalid")
        open_quantity = int(_number(position.get("quantity")))
        close_quantity = open_quantity if quantity is None else int(quantity)
        if close_quantity <= 0 or close_quantity > open_quantity:
            raise ValueError("paper close quantity is invalid")
        entry = _number(position.get("entry"))
        direction = str(position.get("direction", "long")).lower()
        pnl = ((exit_price - entry) if direction == "long" else (entry - exit_price)) * close_quantity
        fees = close_quantity * commission_per_share
        pnl -= fees
        risk = _number(position.get("risk_per_share")) * close_quantity
        final_r = pnl / risk if risk > 0 else 0.0
        self._data["cash"] = _number(self._data.get("cash")) + (entry * close_quantity) + pnl
        closed = {
            **position,
            "quantity": close_quantity,
            "exit_price": exit_price,
            "pnl": pnl,
            "final_r": final_r,
            "final_status": "CLOSED" if close_quantity == open_quantity else "PARTIAL_CLOSED",
            "exit_reason": reason,
            "closed_at": _now(),
        }
        self._data.setdefault("closed_positions", []).append(closed)
        if close_quantity == open_quantity:
            positions.remove(position)
        else:
            position["quantity"] = open_quantity - close_quantity
            position["risk_amount"] = _number(position.get("risk_per_share")) * (open_quantity - close_quantity)
        self._data["realized_pnl"] = _number(self._data.get("realized_pnl")) + pnl
        self._data["total_R"] = _number(self._data.get("total_R")) + final_r
        self._data["daily_R"] = _number(self._data.get("daily_R")) + final_r
        self._mark_to_market()
        self._save()
        return closed

    def move_stop_to_breakeven(self, position_id: str) -> dict[str, object]:
        """Tighten protection only; this never loosens or removes a stop."""
        position = next(
            (item for item in self._data.setdefault("positions", []) if item.get("position_id") == position_id),
            None,
        )
        if position is None:
            raise ValueError(f"paper position not found: {position_id}")
        entry = _number(position.get("entry"))
        current_stop = _number(position.get("stop_loss"))
        direction = str(position.get("direction", "long")).lower()
        if (direction == "long" and entry < current_stop) or (direction == "short" and entry > current_stop):
            raise ValueError("breakeven would loosen the protective stop")
        position["stop_loss"] = entry
        position["protection_policy"] = "deterministic_paper_stop_target_breakeven"
        self._save()
        return dict(position)

    def enforce_protection(self, quotes: Mapping[str, Mapping[str, object]]) -> list[dict[str, object]]:
        """Apply deterministic stop/target exits without consulting an LLM."""
        closed: list[dict[str, object]] = []
        for position in list(self._data.setdefault("positions", [])):
            quote = quotes.get(str(position.get("symbol", "")).upper(), {})
            price = _number(quote.get("last"), _number(position.get("current_price")))
            if price <= 0:
                continue
            direction = str(position.get("direction", "long")).lower()
            stop = _number(position.get("stop_loss"))
            target = _number(position.get("target"))
            stop_hit = price <= stop if direction == "long" else price >= stop
            target_hit = target > 0 and (price >= target if direction == "long" else price <= target)
            if stop_hit or target_hit:
                closed.append(self.close(
                    str(position["position_id"]),
                    price=price,
                    reason="deterministic_stop" if stop_hit else "deterministic_target",
                ))
        return closed
