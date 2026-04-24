"""Interactive Brokers connectivity built on top of ib_insync."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.config import LOGGER, SETTINGS


def _ensure_event_loop() -> None:
    """ib_insync/eventkit expects a current event loop on newer Python versions."""
    try:
        asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


_ensure_event_loop()

try:
    from ib_insync import IB, MarketOrder, Stock, StopOrder, Ticker, Trade, util
except ImportError:  # pragma: no cover - exercised only when dependency is missing.
    IB = None  # type: ignore[assignment]
    MarketOrder = StopOrder = Stock = Ticker = Trade = None  # type: ignore[assignment]
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


class IBKRClient:
    """Thin broker adapter responsible for connectivity and core order actions."""

    def __init__(self) -> None:
        if IB is None:
            raise IBKRDependencyError(
                "ib_insync is not installed. Install requirements.txt before using the broker adapter."
            )
        self.ib = IB()
        self.config = SETTINGS.broker

    @property
    def is_connected(self) -> bool:
        return self.ib.isConnected()

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
                LOGGER.info("Connected to IBKR.")
                return
            except Exception as exc:  # pragma: no cover - needs live broker/network.
                last_error = exc
                LOGGER.exception("IBKR connection attempt %s failed.", attempt)
                time.sleep(self.config.reconnect_delay_seconds)
        raise ConnectionError(f"Unable to connect to IBKR after retries: {last_error}")

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()
            LOGGER.info("Disconnected from IBKR.")

    def ensure_connection(self) -> None:
        """Reconnect if the live connection is not healthy."""
        if not self.ib.isConnected():
            LOGGER.warning("IBKR connection lost; reconnecting.")
            self.connect()

    def create_stock_contract(self, symbol: str, exchange: str = "SMART", currency: str = "USD") -> Any:
        contract = Stock(symbol=symbol, exchange=exchange, currency=currency)
        self.ib.qualifyContracts(contract)
        return contract

    def get_account_summary(self) -> List[Dict[str, Any]]:
        self.ensure_connection()
        return [item.dict() for item in self.ib.accountSummary()]

    def get_positions(self) -> List[Dict[str, Any]]:
        self.ensure_connection()
        positions = []
        for position in self.ib.positions():
            positions.append(
                {
                    "symbol": position.contract.symbol,
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
            orders.append(
                {
                    "symbol": trade.contract.symbol,
                    "order_id": trade.order.orderId,
                    "action": trade.order.action,
                    "quantity": trade.order.totalQuantity,
                    "type": trade.order.orderType,
                    "status": trade.orderStatus.status,
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
        contract = self.create_stock_contract(symbol)
        ticker: Ticker = self.ib.reqMktData(contract, "", snapshot=True, regulatorySnapshot=False)
        self.ib.sleep(2)
        return {
            "bid": float(ticker.bid or 0.0),
            "ask": float(ticker.ask or 0.0),
            "last": float(ticker.last or ticker.close or 0.0),
            "close": float(ticker.close or 0.0),
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
        contract = self.create_stock_contract(symbol)
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=1,
        )
        return util.df(bars)

    def place_market_order(self, symbol: str, action: str, quantity: int) -> OrderResult:
        self.ensure_connection()
        contract = self.create_stock_contract(symbol)
        order = MarketOrder(action=action.upper(), totalQuantity=quantity)
        trade: Trade = self.ib.placeOrder(contract, order)
        self.ib.sleep(1)
        status = trade.orderStatus.status or "Submitted"
        LOGGER.info("Market order placed for %s %s x%s status=%s", action, symbol, quantity, status)
        return OrderResult(
            order_id=trade.order.orderId,
            symbol=symbol,
            action=action.upper(),
            quantity=quantity,
            order_type="MKT",
            status=status,
        )

    def place_stop_order(self, symbol: str, action: str, quantity: int, stop_price: float) -> OrderResult:
        self.ensure_connection()
        contract = self.create_stock_contract(symbol)
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
