"""Interactive Brokers connectivity built on top of ib_insync."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.config import LOGGER, SETTINGS, fx_pair_components


def _ensure_event_loop() -> None:
    """ib_insync/eventkit expects a current event loop on newer Python versions."""
    try:
        asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


_ensure_event_loop()

try:
    from ib_insync import (
        IB,
        Forex,
        LimitOrder,
        MarketOrder,
        Stock,
        StopLimitOrder,
        StopOrder,
        Ticker,
        Trade,
        util,
    )
except ImportError:  # pragma: no cover - exercised only when dependency is missing.
    IB = None  # type: ignore[assignment]
    Forex = LimitOrder = MarketOrder = StopLimitOrder = StopOrder = Stock = Ticker = Trade = None  # type: ignore[assignment]
    util = None  # type: ignore[assignment]


class IBKRDependencyError(RuntimeError):
    """Raised when ib_insync is unavailable."""


@dataclass
class OrderResult:
    order_id: int
    symbol: str
    action: str
    quantity: int
    order_type: str
    status: str
    detail: str = ""
    filled: float = 0.0
    remaining: float = 0.0
    avg_fill_price: float = 0.0


def _safe_market_price(value: object) -> float:
    """Normalize broker quote/order prices so IBKR unset values become zeros."""
    try:
        numeric = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(numeric):
        return 0.0
    # ib_insync/IBKR uses very large sentinel values for unset fields such as
    # lmtPrice/auxPrice. Treat those as missing instead of rendering absurd
    # stop/target prices in the journal.
    if abs(numeric) > 1e20:
        return 0.0
    return numeric


class IBKRClient:
    """Thin broker adapter responsible for connectivity and core order actions."""

    def __init__(self) -> None:
        if IB is None:
            raise IBKRDependencyError(
                "ib_insync is not installed. Install requirements.txt before using the broker adapter."
            )
        self.ib = IB()
        self.config = SETTINGS.broker
        self._ib_error_handler_registered = False
        self._recent_api_errors: List[Dict[str, object]] = []

    @property
    def is_connected(self) -> bool:
        return self.ib.isConnected()

    def _attach_error_handler(self) -> None:
        if self._ib_error_handler_registered:
            return
        error_event = getattr(self.ib, "errorEvent", None)
        if error_event is None:
            return
        error_event += self._on_ib_error
        self._ib_error_handler_registered = True

    def _detach_error_handler(self) -> None:
        if not self._ib_error_handler_registered:
            return
        error_event = getattr(self.ib, "errorEvent", None)
        if error_event is None:
            self._ib_error_handler_registered = False
            return
        error_event -= self._on_ib_error
        self._ib_error_handler_registered = False

    @staticmethod
    def _contract_summary(contract: Any | None) -> str:
        if contract is None:
            return ""
        symbol = str(getattr(contract, "symbol", "") or "").strip()
        exchange = str(getattr(contract, "exchange", "") or "").strip()
        primary_exchange = str(getattr(contract, "primaryExchange", "") or "").strip()
        currency = str(getattr(contract, "currency", "") or "").strip()
        parts = [item for item in [symbol, exchange or primary_exchange, currency] if item]
        return " ".join(parts)

    def _on_ib_error(
        self,
        req_id: int,
        error_code: int,
        error_string: str,
        contract: Any | None = None,
        *args: object,
    ) -> None:
        del args
        contract_text = self._contract_summary(contract)
        symbol = str(getattr(contract, "symbol", "") or "").strip().upper()
        self._recent_api_errors.append(
            {
                "timestamp": time.time(),
                "req_id": int(req_id),
                "error_code": int(error_code),
                "message": str(error_string),
                "symbol": symbol,
            }
        )
        self._recent_api_errors = self._recent_api_errors[-50:]
        message = f"IBKR API error code={error_code} reqId={req_id} message={error_string}"
        if contract_text:
            message = f"{message} contract={contract_text}"

        informational_codes = {2104, 2106, 2107, 2108, 2158}
        if error_code in informational_codes:
            LOGGER.info(message)
        elif error_code > 0:
            LOGGER.warning(message)
        else:
            LOGGER.error(message)

    def has_recent_market_data_subscription_error(self, symbol: str, *, within_seconds: float = 10.0) -> bool:
        normalized_symbol = symbol.strip().upper()
        threshold = time.time() - within_seconds
        for item in reversed(self._recent_api_errors):
            if float(item.get("timestamp", 0.0) or 0.0) < threshold:
                break
            if int(item.get("error_code", 0) or 0) != 10089:
                continue
            error_symbol = str(item.get("symbol", "") or "").strip().upper()
            if not error_symbol or error_symbol == normalized_symbol:
                return True
        return False

    def connect(self) -> None:
        """Connect to IBKR TWS or IB Gateway with retry logic."""
        last_error: Optional[Exception] = None
        for attempt in range(1, self.config.reconnect_retries + 1):
            try:
                if self.ib.isConnected():
                    return
                LOGGER.info("Connecting to IBKR host=%s port=%s client_id=%s", self.config.host, self.config.port, self.config.client_id)
                self.ib.connect(
                    host=self.config.host,
                    port=self.config.port,
                    clientId=self.config.client_id,
                    timeout=self.config.market_data_timeout_seconds,
                )
                self._attach_error_handler()
                LOGGER.info("Connected to IBKR.")
                return
            except Exception as exc:  # pragma: no cover - needs live broker/network.
                last_error = exc
                LOGGER.exception("IBKR connection attempt %s failed.", attempt)
                time.sleep(self.config.reconnect_delay_seconds)
        raise ConnectionError(f"Unable to connect to IBKR after retries: {last_error}")

    def disconnect(self) -> None:
        self._detach_error_handler()
        if self.ib.isConnected():
            self.ib.disconnect()
            LOGGER.info("Disconnected from IBKR.")

    def request_market_data_type(self, market_data_type: int) -> None:
        """Select live/frozen/delayed market data for this IBKR connection."""
        self.ensure_connection()
        self.ib.reqMarketDataType(int(market_data_type))
        LOGGER.info("Requested IBKR market data type=%s", market_data_type)

    def ensure_connection(self) -> None:
        """Reconnect if the live connection is not healthy."""
        if not self.ib.isConnected():
            LOGGER.warning("IBKR connection lost; reconnecting.")
            self.connect()

    def create_stock_contract(self, symbol: str, exchange: str = "SMART", currency: str = "USD") -> Any:
        contract = Stock(symbol=symbol, exchange=exchange, currency=currency)
        self.ib.qualifyContracts(contract)
        return contract

    def create_forex_contract(self, symbol: str, exchange: str = "IDEALPRO") -> Any:
        pair = fx_pair_components(symbol)
        if pair is None:
            raise ValueError(f"Unsupported forex symbol format: {symbol}")
        base, quote = pair
        contract = Forex(pair=f"{base}{quote}", exchange=exchange)
        self.ib.qualifyContracts(contract)
        return contract

    def create_contract(self, symbol: str) -> Any:
        if SETTINGS.symbol_security_type(symbol) == "CASH":
            return self.create_forex_contract(symbol)
        return self.create_stock_contract(symbol)

    def get_account_summary(self) -> List[Dict[str, Any]]:
        self.ensure_connection()
        summary: List[Dict[str, Any]] = []
        for item in self.ib.accountSummary():
            summary.append(
                {
                    "account": getattr(item, "account", ""),
                    "tag": getattr(item, "tag", ""),
                    "value": getattr(item, "value", ""),
                    "currency": getattr(item, "currency", ""),
                }
            )
        return summary

    def get_positions(self) -> List[Dict[str, Any]]:
        self.ensure_connection()
        positions = []
        for position in self.ib.positions():
            positions.append(
                {
                    "symbol": position.contract.symbol,
                    "currency": getattr(position.contract, "currency", ""),
                    "local_symbol": getattr(position.contract, "localSymbol", ""),
                    "position": position.position,
                    "avg_cost": position.avgCost,
                    "sec_type": position.contract.secType,
                }
            )
        return positions

    def get_open_orders(self) -> List[Dict[str, Any]]:
        self.ensure_connection()
        orders = []
        for trade in self.ib.openTrades():
            order = trade.order
            status = trade.orderStatus
            orders.append(
                {
                    "symbol": trade.contract.symbol,
                    "order_id": order.orderId,
                    "perm_id": getattr(order, "permId", 0),
                    "parent_id": getattr(order, "parentId", 0),
                    "action": order.action,
                    "quantity": order.totalQuantity,
                    "type": order.orderType,
                    "status": status.status,
                    "aux_price": _safe_market_price(getattr(order, "auxPrice", 0.0)),
                    "stop_price": _safe_market_price(getattr(order, "auxPrice", 0.0)),
                    "limit_price": _safe_market_price(getattr(order, "lmtPrice", 0.0)),
                    "filled": _safe_market_price(getattr(status, "filled", 0.0)),
                    "remaining": _safe_market_price(getattr(status, "remaining", 0.0)),
                    "avg_fill_price": _safe_market_price(getattr(status, "avgFillPrice", 0.0)),
                    "order_ref": getattr(order, "orderRef", ""),
                    "tif": getattr(order, "tif", ""),
                    "outside_rth": bool(getattr(order, "outsideRth", False)),
                }
            )
        return orders

    def cancel_order(self, order_id: int) -> bool:
        self.ensure_connection()
        for trade in self.ib.openTrades():
            if trade.order.orderId == order_id:
                self.ib.cancelOrder(trade.order)
                self.ib.sleep(1)
                LOGGER.info("Cancelled order_id=%s", order_id)
                return True
        LOGGER.warning("Order id %s was not found among open trades.", order_id)
        return False

    def get_market_price(self, symbol: str) -> Dict[str, float]:
        """Request a snapshot and return bid/ask/last/close safely."""
        self.ensure_connection()
        contract = self.create_contract(symbol)
        ticker: Ticker = self.ib.reqMktData(contract, "", snapshot=True, regulatorySnapshot=False)
        self.ib.sleep(2)
        bid = _safe_market_price(ticker.bid)
        ask = _safe_market_price(ticker.ask)
        last = _safe_market_price(ticker.last)
        close_price = _safe_market_price(ticker.close)
        subscription_blocked = self.has_recent_market_data_subscription_error(symbol)
        if subscription_blocked:
            quote_status = "subscription_blocked"
        elif bid > 0 and ask > 0:
            quote_status = "ok"
        elif last > 0 or close_price > 0:
            quote_status = "partial"
        else:
            quote_status = "missing"
        return {
            "bid": bid,
            "ask": ask,
            "last": last or close_price,
            "close": close_price,
            "quote_status": quote_status,
        }

    def get_historical_bars(
        self,
        symbol: str,
        duration: str = "5 D",
        bar_size: str = "5 mins",
        what_to_show: str = "TRADES",
        use_rth: bool = True,
    ) -> Any:
        """Fetch historical bars and return a DataFrame."""
        self.ensure_connection()
        contract = self.create_contract(symbol)
        is_fx = SETTINGS.symbol_security_type(symbol) == "CASH"
        resolved_what_to_show = "MIDPOINT" if is_fx else what_to_show
        resolved_use_rth = False if is_fx else use_rth
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=resolved_what_to_show,
            useRTH=resolved_use_rth,
            formatDate=1,
        )
        return util.df(bars)

    def get_news_providers(self) -> List[Dict[str, str]]:
        self.ensure_connection()
        providers = self.ib.reqNewsProviders()
        return [
            {
                "code": getattr(provider, "code", ""),
                "name": getattr(provider, "name", ""),
            }
            for provider in providers
        ]

    def get_historical_news(
        self,
        symbol: str,
        provider_codes: List[str],
        total_results: int = 10,
        lookback_hours: int = 24,
    ) -> List[Dict[str, str]]:
        self.ensure_connection()
        if not provider_codes or SETTINGS.symbol_security_type(symbol) != "STK":
            return []

        try:
            contract = self.create_stock_contract(symbol)
        except Exception:
            LOGGER.exception("Unable to qualify stock contract for IBKR news symbol=%s", symbol)
            return []

        con_id = int(getattr(contract, "conId", 0) or 0)
        if con_id <= 0:
            LOGGER.warning("Missing conId for IBKR news symbol=%s", symbol)
            return []

        provider_string = "+".join(provider_codes)
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=lookback_hours)
        try:
            headlines = self.ib.reqHistoricalNews(
                con_id,
                provider_string,
                start_time.strftime("%Y-%m-%d %H:%M:%S"),
                end_time.strftime("%Y-%m-%d %H:%M:%S"),
                total_results,
            )
        except Exception:
            LOGGER.exception(
                "IBKR historical news request failed for symbol=%s providers=%s",
                symbol,
                provider_string,
            )
            return []

        normalized: List[Dict[str, str]] = []
        for item in headlines or []:
            normalized.append(
                {
                    "headline": str(getattr(item, "headline", "")),
                    "published_at": str(getattr(item, "time", "")),
                    "source": str(getattr(item, "providerCode", "")),
                    "provider_code": str(getattr(item, "providerCode", "")),
                    "article_id": str(getattr(item, "articleId", "")),
                    "url": "",
                }
            )
        return normalized


    def place_market_order(self, symbol: str, action: str, quantity: int, tif: str | None = None) -> OrderResult:
        self.ensure_connection()
        contract = self.create_contract(symbol)
        order = MarketOrder(action=action.upper(), totalQuantity=quantity)
        if tif:
            order.tif = tif
        trade: Trade = self.ib.placeOrder(contract, order)
        status = self._await_order_settled(trade, timeout=12.0) or trade.orderStatus.status or "Submitted"
        # IBKR/TWS can briefly surface preset/cancel messages even when a market
        # order ultimately fills. Give the trade object a moment to receive fills
        # and let actual execution data override noisy status text.
        self.ib.sleep(1.5)
        try:
            filled = float(getattr(trade.orderStatus, "filled", 0) or 0)
        except (TypeError, ValueError):
            filled = 0.0
        try:
            remaining = float(getattr(trade.orderStatus, "remaining", 0) or 0)
        except (TypeError, ValueError):
            remaining = 0.0
        try:
            avg_fill_price = float(getattr(trade.orderStatus, "avgFillPrice", 0) or 0)
        except (TypeError, ValueError):
            avg_fill_price = 0.0
        if filled > 0 and remaining <= 0:
            status = "Filled"
        elif filled > 0:
            status = "PartiallyFilled"
        else:
            status = trade.orderStatus.status or status or "Submitted"
        detail = self._order_reject_reason(trade)
        LOGGER.info(
            "Market order placed for %s %s x%s status=%s filled=%s remaining=%s%s",
            action,
            symbol,
            quantity,
            status,
            filled,
            remaining,
            f" reason={detail}" if detail else "",
        )
        return OrderResult(
            order_id=trade.order.orderId,
            symbol=symbol,
            action=action.upper(),
            quantity=quantity,
            order_type="MKT",
            status=status,
            detail=detail,
            filled=filled,
            remaining=remaining,
            avg_fill_price=avg_fill_price,
        )

    @staticmethod
    def _round_to_tick(price: float | None) -> float | None:
        """Round a price to a valid US-equity tick (1c at/above $1, else 0.0001).

        TWS rejects orders whose price does not conform to the contract's minimum
        price variation (error 110), so all order prices must be snapped first.
        """
        if price is None:
            return None
        value = float(price)
        return round(value, 2) if abs(value) >= 1.0 else round(value, 4)

    @staticmethod
    def _order_reject_reason(trade: Any) -> str:
        """Return the broker's reject/cancel message from a trade's log, if any."""
        try:
            for entry in reversed(list(getattr(trade, "log", []) or [])):
                message = str(getattr(entry, "message", "") or "").strip()
                if message:
                    return message
        except Exception:  # pragma: no cover - defensive
            return ""
        return ""

    def _await_order_settled(self, trade: Any, timeout: float = 12.0) -> str:
        """Wait until an order leaves the transient PendingSubmit state.

        Some broker rejections (precautionary cancels, size/price limits) arrive
        several seconds after the order is sent, so reading the status immediately
        would falsely report success. Accepted orders settle to
        Submitted/PreSubmitted/Filled within a fraction of a second, so this only
        blocks while an order is genuinely stuck pending.
        """
        pending = {"", "pendingsubmit", "apipending"}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = str(getattr(trade.orderStatus, "status", "") or "").strip().lower()
            if status and status not in pending:
                break
            self.ib.sleep(0.5)
        return trade.orderStatus.status or "Submitted"

    def place_market_bracket_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        stop_price: float,
        limit_price: float | None = None,
        *,
        outside_rth: bool = False,
        tif: str | None = None,
    ) -> tuple[OrderResult, OrderResult, OrderResult | None]:
        self.ensure_connection()
        contract = self.create_stock_contract(symbol)
        stop_price = self._round_to_tick(stop_price)
        limit_price = self._round_to_tick(limit_price)
        parent_action = action.upper()
        exit_action = "SELL" if parent_action == "BUY" else "BUY"

        parent_order = MarketOrder(action=parent_action, totalQuantity=quantity)
        parent_order.orderId = self.ib.client.getReqId()
        parent_order.transmit = False
        if tif:
            parent_order.tif = tif
        if outside_rth:
            parent_order.outsideRth = True

        stop_order = StopOrder(action=exit_action, totalQuantity=quantity, stopPrice=stop_price)
        stop_order.orderId = self.ib.client.getReqId()
        stop_order.parentId = parent_order.orderId
        stop_order.transmit = limit_price is None
        if tif:
            stop_order.tif = tif
        if outside_rth:
            stop_order.outsideRth = True

        limit_order: LimitOrder | None = None
        if limit_price is not None:
            limit_order = LimitOrder(action=exit_action, totalQuantity=quantity, lmtPrice=limit_price)
            limit_order.orderId = self.ib.client.getReqId()
            limit_order.parentId = parent_order.orderId
            limit_order.transmit = True
            if tif:
                limit_order.tif = tif
            if outside_rth:
                limit_order.outsideRth = True

        parent_trade: Trade = self.ib.placeOrder(contract, parent_order)
        stop_trade: Trade = self.ib.placeOrder(contract, stop_order)
        limit_trade: Trade | None = None
        if limit_order is not None:
            limit_trade = self.ib.placeOrder(contract, limit_order)
        # Wait for the parent to leave the transient PendingSubmit state so a fast
        # broker rejection (e.g. a precautionary cancel) is reported accurately
        # instead of a premature "submitted".
        self._await_order_settled(parent_trade)

        parent_status = parent_trade.orderStatus.status or "Submitted"
        stop_status = stop_trade.orderStatus.status or "Submitted"
        limit_status = (limit_trade.orderStatus.status if limit_trade is not None else "") or "Submitted"
        parent_detail = self._order_reject_reason(parent_trade)

        LOGGER.info(
            "Bracket order placed for %s %s x%s parent=%s stop=%s limit=%s%s",
            parent_action,
            symbol,
            quantity,
            parent_status,
            stop_status,
            limit_status if limit_order is not None else "n/a",
            f" reason={parent_detail}" if parent_detail else "",
        )

        parent_result = OrderResult(
            order_id=parent_order.orderId,
            symbol=symbol,
            action=parent_action,
            quantity=quantity,
            order_type="MKT",
            status=parent_status,
            detail=parent_detail,
        )
        stop_result = OrderResult(
            order_id=stop_order.orderId,
            symbol=symbol,
            action=exit_action,
            quantity=quantity,
            order_type="STP",
            status=stop_status,
        )
        limit_result = (
            OrderResult(
                order_id=limit_order.orderId,
                symbol=symbol,
                action=exit_action,
                quantity=quantity,
                order_type="LMT",
                status=limit_status,
            )
            if limit_order is not None
            else None
        )
        return parent_result, stop_result, limit_result

    def place_limit_bracket_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        entry_price: float,
        stop_price: float,
        limit_price: float | None = None,
        *,
        outside_rth: bool = False,
        tif: str | None = None,
    ) -> tuple[OrderResult, OrderResult, OrderResult | None]:
        self.ensure_connection()
        contract = self.create_stock_contract(symbol)
        entry_price = self._round_to_tick(entry_price)
        stop_price = self._round_to_tick(stop_price)
        limit_price = self._round_to_tick(limit_price)
        parent_action = action.upper()
        exit_action = "SELL" if parent_action == "BUY" else "BUY"

        parent_order = LimitOrder(action=parent_action, totalQuantity=quantity, lmtPrice=entry_price)
        parent_order.orderId = self.ib.client.getReqId()
        parent_order.transmit = False
        if tif:
            parent_order.tif = tif
        if outside_rth:
            parent_order.outsideRth = True

        stop_order = StopOrder(action=exit_action, totalQuantity=quantity, stopPrice=stop_price)
        stop_order.orderId = self.ib.client.getReqId()
        stop_order.parentId = parent_order.orderId
        stop_order.transmit = limit_price is None
        if tif:
            stop_order.tif = tif
        if outside_rth:
            stop_order.outsideRth = True

        take_profit_order: LimitOrder | None = None
        if limit_price is not None:
            take_profit_order = LimitOrder(action=exit_action, totalQuantity=quantity, lmtPrice=limit_price)
            take_profit_order.orderId = self.ib.client.getReqId()
            take_profit_order.parentId = parent_order.orderId
            take_profit_order.transmit = True
            if tif:
                take_profit_order.tif = tif
            if outside_rth:
                take_profit_order.outsideRth = True

        parent_trade: Trade = self.ib.placeOrder(contract, parent_order)
        stop_trade: Trade = self.ib.placeOrder(contract, stop_order)
        limit_trade: Trade | None = None
        if take_profit_order is not None:
            limit_trade = self.ib.placeOrder(contract, take_profit_order)
        self._await_order_settled(parent_trade)

        parent_status = parent_trade.orderStatus.status or "Submitted"
        stop_status = stop_trade.orderStatus.status or "Submitted"
        limit_status = (limit_trade.orderStatus.status if limit_trade is not None else "") or "Submitted"
        parent_detail = self._order_reject_reason(parent_trade)

        LOGGER.info(
            "Limit bracket order placed for %s %s x%s entry=%s parent=%s stop=%s limit=%s%s",
            parent_action,
            symbol,
            quantity,
            entry_price,
            parent_status,
            stop_status,
            limit_status if take_profit_order is not None else "n/a",
            f" reason={parent_detail}" if parent_detail else "",
        )

        parent_result = OrderResult(
            order_id=parent_order.orderId,
            symbol=symbol,
            action=parent_action,
            quantity=quantity,
            order_type="LMT",
            status=parent_status,
            detail=parent_detail,
        )
        stop_result = OrderResult(
            order_id=stop_order.orderId,
            symbol=symbol,
            action=exit_action,
            quantity=quantity,
            order_type="STP",
            status=stop_status,
        )
        limit_result = (
            OrderResult(
                order_id=take_profit_order.orderId,
                symbol=symbol,
                action=exit_action,
                quantity=quantity,
                order_type="LMT",
                status=limit_status,
            )
            if take_profit_order is not None
            else None
        )
        return parent_result, stop_result, limit_result

    def place_stop_limit_bracket_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        entry_stop_price: float,
        entry_limit_price: float,
        stop_price: float,
        target_price: float,
        *,
        order_ref: str,
        tif: str = "DAY",
        outside_rth: bool = False,
    ) -> tuple[OrderResult, OrderResult, OrderResult]:
        """Place an idempotency-tagged stop-limit parent with OCA protection."""
        self.ensure_connection()
        if not order_ref:
            raise ValueError("IRS stop-limit bracket requires order_ref.")
        contract = self.create_stock_contract(symbol)
        entry_stop_price = self._round_to_tick(entry_stop_price)
        entry_limit_price = self._round_to_tick(entry_limit_price)
        stop_price = self._round_to_tick(stop_price)
        target_price = self._round_to_tick(target_price)
        parent_action = action.upper()
        exit_action = "SELL" if parent_action == "BUY" else "BUY"
        oca_group = f"{order_ref}-OCA"

        parent = StopLimitOrder(
            action=parent_action,
            totalQuantity=quantity,
            stopPrice=entry_stop_price,
            lmtPrice=entry_limit_price,
        )
        parent.orderId = self.ib.client.getReqId()
        parent.orderRef = order_ref
        parent.tif = tif
        parent.outsideRth = outside_rth
        parent.transmit = False

        protective_stop = StopOrder(
            action=exit_action,
            totalQuantity=quantity,
            stopPrice=stop_price,
        )
        protective_stop.orderId = self.ib.client.getReqId()
        protective_stop.parentId = parent.orderId
        protective_stop.orderRef = f"{order_ref}-SL"
        protective_stop.ocaGroup = oca_group
        protective_stop.ocaType = 1
        protective_stop.tif = "GTC"
        protective_stop.outsideRth = outside_rth
        protective_stop.transmit = False

        target = LimitOrder(
            action=exit_action,
            totalQuantity=quantity,
            lmtPrice=target_price,
        )
        target.orderId = self.ib.client.getReqId()
        target.parentId = parent.orderId
        target.orderRef = f"{order_ref}-TP"
        target.ocaGroup = oca_group
        target.ocaType = 1
        target.tif = "GTC"
        target.outsideRth = outside_rth
        target.transmit = True

        parent_trade: Trade = self.ib.placeOrder(contract, parent)
        stop_trade: Trade = self.ib.placeOrder(contract, protective_stop)
        target_trade: Trade = self.ib.placeOrder(contract, target)
        self._await_order_settled(parent_trade)
        statuses = [
            parent_trade.orderStatus.status or "Submitted",
            stop_trade.orderStatus.status or "Submitted",
            target_trade.orderStatus.status or "Submitted",
        ]
        detail = self._order_reject_reason(parent_trade)
        LOGGER.info(
            "Stop-limit bracket placed ref=%s %s %s x%s parent=%s stop=%s target=%s%s",
            order_ref,
            parent_action,
            symbol,
            quantity,
            statuses[0],
            statuses[1],
            statuses[2],
            f" reason={detail}" if detail else "",
        )
        return (
            OrderResult(
                parent.orderId,
                symbol,
                parent_action,
                quantity,
                "STP LMT",
                statuses[0],
                detail=detail,
            ),
            OrderResult(
                protective_stop.orderId,
                symbol,
                exit_action,
                quantity,
                "STP",
                statuses[1],
            ),
            OrderResult(
                target.orderId,
                symbol,
                exit_action,
                quantity,
                "LMT",
                statuses[2],
            ),
        )

    def resize_open_order(self, order_id: int, quantity: int) -> bool:
        """Resize an existing open child order, used for partial-fill protection."""
        self.ensure_connection()
        if quantity <= 0:
            return False
        for trade in self.ib.openTrades():
            if int(getattr(trade.order, "orderId", 0) or 0) != int(order_id):
                continue
            trade.order.totalQuantity = int(quantity)
            self.ib.placeOrder(trade.contract, trade.order)
            self.ib.sleep(0.5)
            status = str(getattr(trade.orderStatus, "status", "") or "").lower()
            return status not in {"cancelled", "inactive", "apicancelled"}
        return False

    def place_stop_order(self, symbol: str, action: str, quantity: int, stop_price: float) -> OrderResult:
        self.ensure_connection()
        contract = self.create_stock_contract(symbol)
        stop_price = self._round_to_tick(stop_price)
        order = StopOrder(action=action.upper(), totalQuantity=quantity, stopPrice=stop_price)
        trade: Trade = self.ib.placeOrder(contract, order)
        self.ib.sleep(1)
        status = trade.orderStatus.status or "Submitted"
        LOGGER.info("Stop order placed for %s %s x%s stop=%s status=%s", action, symbol, quantity, stop_price, status)
        return OrderResult(
            order_id=trade.order.orderId,
            symbol=symbol,
            action=action.upper(),
            quantity=quantity,
            order_type="STP",
            status=status,
        )

    def place_limit_order(self, symbol: str, action: str, quantity: int, limit_price: float) -> OrderResult:
        self.ensure_connection()
        contract = self.create_stock_contract(symbol)
        limit_price = self._round_to_tick(limit_price)
        order = LimitOrder(action=action.upper(), totalQuantity=quantity, lmtPrice=limit_price)
        trade: Trade = self.ib.placeOrder(contract, order)
        self.ib.sleep(1)
        status = trade.orderStatus.status or "Submitted"
        LOGGER.info("Limit order placed for %s %s x%s limit=%s status=%s", action, symbol, quantity, limit_price, status)
        return OrderResult(
            order_id=trade.order.orderId,
            symbol=symbol,
            action=action.upper(),
            quantity=quantity,
            order_type="LMT",
            status=status,
        )

    def replace_stop_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        stop_price: float,
        existing_order_id: int | None = None,
    ) -> OrderResult:
        if existing_order_id:
            self.cancel_order(existing_order_id)
        return self.place_stop_order(symbol=symbol, action=action, quantity=quantity, stop_price=stop_price)
