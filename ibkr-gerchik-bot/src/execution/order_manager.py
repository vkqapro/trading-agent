"""Order execution orchestration."""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

from src.alerts.slack import SlackAlerter
from src.brokers.ibkr import IBKRClient, OrderResult
from src.config import LOGGER, SETTINGS, append_markdown_log
from src.data.market_data import MarketDataService
from src.data.news_filter import NewsRiskFilter
from src.risk.position_size import calculate_position_size, position_value_ok
from src.strategy.signal_models import TradeSignal
from src.strategy.validator import validate_trade


class OrderManager:
    """Execute validated trades and record the resulting actions."""

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
    ) -> Tuple[bool, Dict[str, object]]:
        quote = self.market_data.get_quote(signal.symbol)
        spread_pct = self._spread_pct(quote)
        quantity = calculate_position_size(account_equity, SETTINGS.risk.risk_per_trade, signal.entry, signal.stop)
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
            account_synced=True,
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
        failure_statuses = {"cancelled", "inactive", "apicancelled"}
        if any(
            isinstance(status, str) and status.strip().lower() in failure_statuses
            for status in broker_statuses.values()
            if status is not None
        ):
            payload = {
                "status": "broker_rejected",
                "reasons": ["broker_cancelled_order"],
                "signal": enriched_signal,
                "broker_statuses": broker_statuses,
                "market_order_id": entry_order.order_id,
                "stop_order_id": stop_order.order_id,
                "limit_order_id": 0 if limit_order is None else limit_order.order_id,
            }
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

        if self.dry_run:
            payload = self._build_payload(signal, quantity, 0, 0, status="simulated", dry_run=True)
            payload["manual_override"] = True
            payload["entry_order_type"] = entry_order_type
            append_markdown_log(SETTINGS.paths.trade_log, f"Simulated Manual Trade {signal.symbol}", payload)
            return True, payload

        limit_price = float(signal.target) if signal.target else None
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
        failure_statuses = {"cancelled", "inactive", "apicancelled"}
        if any(
            isinstance(status, str) and status.strip().lower() in failure_statuses
            for status in broker_statuses.values()
            if status is not None
        ):
            reason = getattr(entry_order, "detail", "") or "broker_cancelled_order"
            payload = {
                "status": "broker_rejected",
                "reasons": [reason],
                "signal": enriched,
                "entry_order_type": entry_order_type,
                "broker_statuses": broker_statuses,
                "market_order_id": entry_order.order_id,
                "stop_order_id": stop_order.order_id,
                "limit_order_id": 0 if limit_order is None else limit_order.order_id,
            }
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
        return {
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
        return {
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
