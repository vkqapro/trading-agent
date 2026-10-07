"""Order execution orchestration."""

from __future__ import annotations

import math
import uuid
from typing import Dict, List, Mapping, Tuple

from src.alerts.slack import SlackAlerter
from src.brokers.ibkr import IBKRClient, OrderResult
from src.config import LOGGER, SETTINGS, append_markdown_log
from src.data.market_data import MarketDataService
from src.data.news_filter import NewsRiskFilter
from src.risk.position_size import calculate_position_size, position_value_ok
from src.strategy.signal_models import TradeSignal
from src.strategy.validator import validate_trade
from src.decision.identity import broker_order_ref
from src.decision.market import quote_age_seconds, quote_last, quote_spread_pct


class OrderManager:
    """Execute validated trades and record the resulting actions."""

    _BROKER_FAILURE_STATUSES = {"cancelled", "inactive", "apicancelled", "rejected"}
    _BROKER_ACTIVE_STATUSES = {
        "", "submitted", "presubmitted", "pendingsubmit", "apipending",
        "accepted", "pendingcancel", "pendingreplace", "partiallyfilled",
        "partially filled", "filled",
    }

    @classmethod
    def _classify_bracket_snapshot(
        cls,
        entry_order: OrderResult,
        stop_order: OrderResult,
        limit_order: OrderResult | None,
    ) -> str:
        """Classify a bracket without treating a transient snapshot as truth."""
        orders = [entry_order, stop_order] + ([limit_order] if limit_order is not None else [])
        if any(float(getattr(order, "filled", 0) or 0) > 0 for order in orders):
            return "executed"
        statuses = {
            str(getattr(order, "status", "") or "").strip().lower()
            for order in orders
        }
        failures = statuses & cls._BROKER_FAILURE_STATUSES
        active = statuses & cls._BROKER_ACTIVE_STATUSES
        if failures and active:
            return "unknown_requires_reconciliation"
        if failures:
            return "broker_rejected"
        return "executed"

    @classmethod
    def _bracket_failure_payload(
        cls,
        signal: TradeSignal,
        enriched_signal: Dict[str, object],
        entry_order: OrderResult,
        stop_order: OrderResult,
        limit_order: OrderResult | None,
        *,
        entry_order_type: str | None = None,
    ) -> Dict[str, object]:
        outcome = cls._classify_bracket_snapshot(entry_order, stop_order, limit_order)
        if outcome == "executed":
            raise ValueError("_bracket_failure_payload called for a non-failure snapshot")
        statuses = {
            "market_order": entry_order.status,
            "stop_order": stop_order.status,
            "limit_order": None if limit_order is None else limit_order.status,
        }
        return {
            "status": outcome,
            "reasons": [
                "broker_status_conflict" if outcome == "unknown_requires_reconciliation" else "broker_cancelled_order"
            ],
            "signal": enriched_signal,
            "entry_order_type": entry_order_type,
            "broker_statuses": statuses,
            "market_order_id": entry_order.order_id,
            "broker_perm_id": getattr(entry_order, "perm_id", 0) or 0,
            "stop_order_id": stop_order.order_id,
            "limit_order_id": 0 if limit_order is None else limit_order.order_id,
            "filled": getattr(entry_order, "filled", 0),
            "remaining": getattr(entry_order, "remaining", 0),
            "reconciliation_required": outcome == "unknown_requires_reconciliation",
        }

    def __init__(
        self,
        broker: IBKRClient,
        market_data: MarketDataService,
        alerter: SlackAlerter,
        news_filter: NewsRiskFilter,
        dry_run: bool = False,
        alert_on_manual_candidates: bool = True,
    ) -> None:
        self.broker = broker
        self.market_data = market_data
        self.alerter = alerter
        self.news_filter = news_filter
        self.dry_run = dry_run
        self.alert_on_manual_candidates = alert_on_manual_candidates
        self._manual_candidate_alert_keys: set[tuple[str, float, float, float, str]] = set()

    def execute_trade(
        self,
        signal: TradeSignal,
        account_equity: float,
        cash_available: float,
        current_positions: List[Dict[str, object]],
        open_risk_amount: float,
        *,
        allow_after_hours: bool = False,
        allow_extended_hours_order: bool = False,
        time_in_force: str | None = None,
        autonomous_guard: Mapping[str, object] | None = None,
    ) -> Tuple[bool, Dict[str, object]]:
        quote = self.market_data.get_quote(signal.symbol)
        spread_pct = self._spread_pct(quote)
        if autonomous_guard is not None:
            guard_reasons: list[str] = []
            age = quote_age_seconds(quote)
            if age is None:
                guard_reasons.append("quote_age_unknown")
            elif age > float(autonomous_guard.get("max_data_age_seconds", 0.0) or 0.0):
                guard_reasons.append("quote_stale")
            current_price = quote_last(quote)
            entry = float(autonomous_guard.get("entry", signal.entry) or signal.entry)
            max_chase = float(autonomous_guard.get("max_entry_chase_pct", 0.0) or 0.0)
            if current_price <= 0:
                guard_reasons.append("quote_invalid")
            elif entry > 0 and abs(current_price - entry) / entry > max_chase:
                guard_reasons.append("price_chase_too_far")
            if quote_spread_pct(quote) > float(autonomous_guard.get("max_spread_pct", SETTINGS.risk.max_spread_pct)):
                guard_reasons.append("spread_too_wide")
            quote_status = str(quote.get("quote_status", "") or "").lower()
            if "quote_status" in quote and quote_status in {"missing", "unknown", "unavailable"}:
                guard_reasons.append("quote_status_unknown")
            if guard_reasons:
                return False, {"status": "rejected", "reasons": tuple(dict.fromkeys(guard_reasons)), "signal": signal.to_dict()}
        quantity = calculate_position_size(account_equity, SETTINGS.risk.risk_per_trade, signal.entry, signal.stop)
        if autonomous_guard is not None:
            guarded_quantity = int(autonomous_guard.get("quantity", 0) or 0)
            if guarded_quantity > 0:
                quantity = guarded_quantity
        order_size_valid = position_value_ok(quantity, signal.entry, SETTINGS.risk.max_position_value, cash_available)
        quote_status = str(quote.get("quote_status", "unknown") or "unknown")
        enriched_signal = signal.to_dict()
        enriched_signal["quantity"] = quantity
        enriched_signal["risk_amount"] = abs(signal.entry - signal.stop) * quantity
        enriched_signal["next_major_level"] = signal.nearest_upper_level if signal.direction == "long" else signal.nearest_lower_level

        is_valid, reasons = validate_trade(
            enriched_signal,
            account_equity=account_equity,
            current_positions=current_positions,
            open_risk_amount=open_risk_amount,
            spread_pct=spread_pct,
            paper_trading=SETTINGS.paper_trading,
            tws_connected=self.broker.is_connected,
            account_synced=bool(autonomous_guard.get("account_synced", False)) if autonomous_guard is not None else True,
            market_open=self.market_data.market_is_open() or allow_after_hours,
            first_unstable_minutes=self.market_data.unstable_open_window(),
            allow_first_unstable_minutes=False,
            has_symbol_news_risk=self.news_filter.has_high_risk_news(signal.symbol),
            has_macro_risk=self.news_filter.is_macro_risk(),
            atr_has_room=True,
            order_size_valid=order_size_valid,
            cash_available=cash_available,
        )
        if not is_valid or quantity <= 0:
            manual_candidate = self._build_manual_candidate_payload(
                enriched_signal,
                reasons,
                quote_status=quote_status,
                quantity=quantity,
            )
            if manual_candidate is not None:
                if self.alert_on_manual_candidates:
                    self._notify_manual_candidate(manual_candidate)
                LOGGER.warning("Trade requires manual placement due to quote subscription: %s", manual_candidate)
                return False, manual_candidate
            payload = {"status": "rejected", "reasons": reasons or ["position_size_zero"], "signal": enriched_signal}
            LOGGER.warning("Trade rejected: %s", payload)
            return False, payload

        if self.dry_run:
            trade_payload = self._build_payload(signal, quantity, 0, 0, status="simulated", dry_run=True)
            append_markdown_log(SETTINGS.paths.trade_log, f"Simulated Trade {signal.symbol}", trade_payload)
            return True, trade_payload

        limit_price = float(signal.target) if signal.target else None
        broker_kwargs = {
            "limit_price": limit_price,
            "outside_rth": allow_extended_hours_order,
            "tif": time_in_force,
        }
        if autonomous_guard is not None:
            broker_kwargs["order_ref"] = str(
                autonomous_guard.get("order_ref")
                or broker_order_ref(
                    agent_id=str(autonomous_guard.get("agent_id", "GERCHIK_LLM_01")),
                    candidate_id=str(autonomous_guard.get("candidate_id", "")),
                    decision_id=str(autonomous_guard.get("decision_id", "")),
                )
            )
        try:
            entry_order, stop_order, limit_order = self.broker.place_market_bracket_order(
                signal.symbol,
                signal.signal,
                quantity,
                signal.stop,
                **broker_kwargs,
            )
        except TypeError:
            if autonomous_guard is not None:
                return False, {
                    "status": "rejected",
                    "reasons": ("broker_order_reference_unsupported",),
                    "signal": signal.to_dict(),
                }
            raise
        broker_statuses = {
            "market_order": entry_order.status,
            "stop_order": stop_order.status,
            "limit_order": None if limit_order is None else limit_order.status,
        }
        outcome = self._classify_bracket_snapshot(entry_order, stop_order, limit_order)
        if outcome != "executed":
            payload = self._bracket_failure_payload(
                signal, enriched_signal, entry_order, stop_order, limit_order,
            )
            LOGGER.warning("Broker cancelled bracket order: %s", payload)
            return False, payload
        trade_payload = self._build_payload(
            signal,
            quantity,
            entry_order.order_id,
            stop_order.order_id,
            limit_order_id=0 if limit_order is None else limit_order.order_id,
            status="executed",
        )
        trade_payload["broker_statuses"] = broker_statuses
        trade_payload["broker_perm_id"] = getattr(entry_order, "perm_id", 0) or 0
        append_markdown_log(SETTINGS.paths.trade_log, f"Trade {signal.symbol}", trade_payload)
        self.alerter.send_trade_executed(trade_payload)
        return True, trade_payload

    def execute_manual_order(
        self,
        signal: TradeSignal,
        *,
        account_equity: float,
        cash_available: float,
        quantity: int | None = None,
        allow_extended_hours_order: bool = True,
        time_in_force: str | None = "GTC",
        entry_order_type: str = "MARKET",
    ) -> Tuple[bool, Dict[str, object]]:
        """Place a human-initiated (dashboard) order.

        This bypasses the *autonomous* signal-quality gates that protect the
        unattended scanner — spread, news, ATR room, reward:risk, and market-hours
        checks — because the order is an explicit, confirmed human decision.

        When ``quantity`` is supplied (the size the user reviewed in the forecast
        ledger) it is used directly; otherwise it is risk-sized from the live
        config. Either way the size is capped to remain affordable and within the
        configured maximum position value, and paper-only / DRY_RUN are enforced
        by the caller.
        """
        entry = float(signal.entry or 0.0)
        stop = float(signal.stop or 0.0)
        if signal.signal not in {"BUY", "SELL"} or entry <= 0 or stop <= 0:
            return False, {"status": "rejected", "reasons": ["invalid_setup"], "signal": signal.to_dict()}

        if quantity is not None and int(quantity) > 0:
            base_quantity = int(quantity)
        else:
            base_quantity = calculate_position_size(account_equity, SETTINGS.risk.risk_per_trade, entry, stop)
        caps = [base_quantity, int(SETTINGS.risk.max_position_value // entry)]
        if cash_available > 0:
            caps.append(int(cash_available // entry))
        quantity = max(0, min(caps))
        if quantity <= 0:
            return False, {
                "status": "rejected",
                "reasons": ["insufficient_cash_or_position_value"],
                "signal": signal.to_dict(),
            }

        enriched = signal.to_dict()
        enriched["quantity"] = quantity
        enriched["risk_amount"] = abs(entry - stop) * quantity
        entry_order_type = str(entry_order_type or "MARKET").strip().upper()

        if entry_order_type not in {"LIMIT", "MARKET", "STOP_LIMIT"}:
            return False, {
                "status": "rejected",
                "reasons": ["unsupported_entry_order_type"],
                "entry_order_type": entry_order_type,
                "signal": enriched,
            }

        target = float(signal.target or 0.0)
        if target <= 0:
            return False, {
                "status": "rejected",
                "reasons": ["manual_setup_requires_target"],
                "entry_order_type": entry_order_type,
                "signal": enriched,
            }
        valid_protection = (
            (signal.signal == "BUY" and stop < entry < target)
            or (signal.signal == "SELL" and stop > entry > target)
        )
        if not valid_protection:
            return False, {
                "status": "rejected",
                "reasons": ["manual_setup_invalid_stop_target"],
                "entry_order_type": entry_order_type,
                "signal": enriched,
            }

        if self.dry_run:
            payload = self._build_payload(signal, quantity, 0, 0, status="simulated", dry_run=True)
            payload["manual_override"] = True
            payload["entry_order_type"] = entry_order_type
            append_markdown_log(SETTINGS.paths.trade_log, f"Simulated Manual Trade {signal.symbol}", payload)
            return True, payload

        limit_price = target
        if entry_order_type == "LIMIT":
            entry_order, stop_order, limit_order = self.broker.place_limit_bracket_order(
                signal.symbol,
                signal.signal,
                quantity,
                signal.entry,
                signal.stop,
                limit_price=limit_price,
                outside_rth=allow_extended_hours_order,
                tif=time_in_force,
            )
        elif entry_order_type == "STOP_LIMIT":
            entry_order, stop_order, limit_order = self.broker.place_stop_limit_bracket_order(
                signal.symbol,
                signal.signal,
                quantity,
                entry_stop_price=signal.entry,
                entry_limit_price=signal.entry,
                stop_price=signal.stop,
                target_price=target,
                order_ref=f"manual-{signal.symbol}-{uuid.uuid4().hex[:12]}",
                tif=time_in_force or "GTC",
                outside_rth=allow_extended_hours_order,
            )
        else:
            entry_order, stop_order, limit_order = self.broker.place_market_bracket_order(
                signal.symbol,
                signal.signal,
                quantity,
                signal.stop,
                limit_price=limit_price,
                outside_rth=allow_extended_hours_order,
                tif=time_in_force,
            )
        broker_statuses = {
            "market_order": entry_order.status,
            "stop_order": stop_order.status,
            "limit_order": None if limit_order is None else limit_order.status,
        }
        outcome = self._classify_bracket_snapshot(entry_order, stop_order, limit_order)
        if outcome != "executed":
            payload = self._bracket_failure_payload(
                signal, enriched, entry_order, stop_order, limit_order,
                entry_order_type=entry_order_type,
            )
            if getattr(entry_order, "detail", ""):
                payload["reasons"] = [entry_order.detail]
            LOGGER.warning("Broker cancelled manual bracket order: %s", payload)
            return False, payload

        payload = self._build_payload(
            signal,
            quantity,
            entry_order.order_id,
            stop_order.order_id,
            limit_order_id=0 if limit_order is None else limit_order.order_id,
            status="executed",
        )
        payload["manual_override"] = True
        payload["entry_order_type"] = entry_order_type
        payload["broker_statuses"] = broker_statuses
        append_markdown_log(SETTINGS.paths.trade_log, f"Manual Trade {signal.symbol}", payload)
        self.alerter.send_trade_executed(payload)
        return True, payload

    @staticmethod
    def _spread_pct(quote: Dict[str, float]) -> float:
        bid = OrderManager._finite_or_zero(quote.get("bid", 0.0))
        ask = OrderManager._finite_or_zero(quote.get("ask", 0.0))
        last = OrderManager._finite_or_zero(quote.get("last", 0.0))
        if bid <= 0 or ask <= 0:
            return 1.0
        mid = ((bid + ask) / 2.0) or last
        if mid <= 0:
            return 1.0
        return abs(ask - bid) / mid

    @staticmethod
    def _finite_or_zero(value: object) -> float:
        try:
            numeric = float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return numeric if math.isfinite(numeric) else 0.0

    def _build_manual_candidate_payload(
        self,
        enriched_signal: Dict[str, object],
        reasons: List[str],
        *,
        quote_status: str,
        quantity: int,
    ) -> Dict[str, object] | None:
        if quantity <= 0:
            return None
        if quote_status != "subscription_blocked":
            return None
        non_quote_reasons = [reason for reason in reasons if reason != "spread_too_wide"]
        if non_quote_reasons:
            return None
        payload = {
            "status": "manual_candidate",
            "reasons": ["quote_subscription_required"],
            "signal": enriched_signal,
            "symbol": enriched_signal.get("symbol"),
            "strategy": enriched_signal.get("strategy"),
            "direction": enriched_signal.get("direction"),
            "signal_side": enriched_signal.get("signal"),
            "entry": enriched_signal.get("entry"),
            "stop_loss": enriched_signal.get("stop"),
            "target": enriched_signal.get("target"),
            "reward_risk": enriched_signal.get("reward_risk"),
            "quantity": quantity,
            "quote_status": quote_status,
        }
        return payload

    def _notify_manual_candidate(self, payload: Dict[str, object]) -> None:
        key = (
            str(payload.get("symbol", "") or ""),
            float(payload.get("entry", 0.0) or 0.0),
            float(payload.get("stop_loss", 0.0) or 0.0),
            float(payload.get("target", 0.0) or 0.0),
            str(payload.get("strategy", "") or ""),
        )
        if key in self._manual_candidate_alert_keys:
            return
        self._manual_candidate_alert_keys.add(key)
        self.alerter.send_manual_candidate(payload)

    @staticmethod
    def _build_payload(
        signal: TradeSignal,
        quantity: int,
        market_order_id: int,
        stop_order_id: int,
        *,
        limit_order_id: int = 0,
        status: str,
        dry_run: bool = False,
    ) -> Dict[str, object]:
        payload = {
            "status": status,
            "dry_run": dry_run,
            "symbol": signal.symbol,
            "strategy": signal.strategy,
            "level_type": signal.level_type,
            "direction": signal.direction,
            "signal": signal.signal,
            "entry": signal.entry,
            "stop_loss": signal.stop,
            "target": signal.target,
            "reward_risk": signal.reward_risk,
            "partial_targets": signal.partial_targets,
            "quantity": quantity,
            "market_order_id": market_order_id,
            "stop_order_id": stop_order_id,
            "limit_order_id": limit_order_id,
        }
        for key in ("owner", "agent_id", "decision_id", "candidate_id", "order_ref"):
            if key in signal.metadata:
                payload[key] = signal.metadata[key]
        return payload
