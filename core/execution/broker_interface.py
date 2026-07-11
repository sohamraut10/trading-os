"""
Execution Engine — Broker Interface & Smart Order Router
Translates TradeSignal + RiskCheckResult into actual market orders.
Implements smart order routing: limit orders first, market orders as fallback.
"""
import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.agents.base_agent import Signal
from core.agents.meta_agent import TradeSignal
from core.risk.risk_engine import RiskCheckResult

try:
    import alpaca_trade_api as _alpaca_trade_api
    _ALPACA_AVAILABLE = True
except ImportError:
    _ALPACA_AVAILABLE = False


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Order:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    asset: str = ""
    side: str = ""                  # "buy" | "sell"
    quantity: float = 0.0
    order_type: OrderType = OrderType.LIMIT
    limit_price: float | None = None
    stop_price: float | None = None
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    submitted_at: float = field(default_factory=time.time)
    filled_at: float | None = None
    broker_order_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def slippage_pct(self) -> float:
        if not self.limit_price or not self.avg_fill_price:
            return 0.0
        return abs(self.avg_fill_price - self.limit_price) / self.limit_price * 100


@dataclass
class BracketOrder:
    """Entry + stop loss + take profit as one atomic unit."""
    entry: Order
    stop_loss: Order
    take_profit: Order
    signal_id: str = ""


class BrokerAdapter(ABC):
    """All brokers implement this contract."""

    @abstractmethod
    async def submit_order(self, order: Order) -> Order:
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> Order:
        ...

    @abstractmethod
    async def get_positions(self) -> dict[str, Any]:
        ...

    @abstractmethod
    async def get_account(self) -> dict[str, Any]:
        ...


class AlpacaBroker(BrokerAdapter):
    """Alpaca paper/live trading adapter."""

    def __init__(self, api_key: str, secret_key: str, base_url: str):
        if not _ALPACA_AVAILABLE:
            raise RuntimeError(
                "alpaca-trade-api is not installed — add it to requirements.txt "
                "or unset ALPACA_API_KEY to use PaperBroker instead."
            )
        self._api = _alpaca_trade_api.REST(api_key, secret_key, base_url)

    async def submit_order(self, order: Order) -> Order:
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self._api.submit_order(
                    symbol=order.asset,
                    qty=order.quantity,
                    side=order.side,
                    type=order.order_type.value,
                    time_in_force="gtc",
                    limit_price=order.limit_price,
                    stop_price=order.stop_price,
                )
            )
            order.broker_order_id = result.id
            order.status = OrderStatus.SUBMITTED
        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.metadata["error"] = str(e)
        return order

    async def cancel_order(self, order_id: str) -> bool:
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, lambda: self._api.cancel_order(order_id))
            return True
        except Exception:
            return False

    async def get_order_status(self, order_id: str) -> Order:
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, lambda: self._api.get_order(order_id))
        order = Order(broker_order_id=raw.id, asset=raw.symbol)
        order.status = OrderStatus(raw.status)
        order.filled_qty = float(raw.filled_qty or 0)
        order.avg_fill_price = float(raw.filled_avg_price or 0)
        return order

    async def get_positions(self) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        positions = await loop.run_in_executor(None, self._api.list_positions)
        return {p.symbol: {"qty": float(p.qty), "value": float(p.market_value)} for p in positions}

    async def get_account(self) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        acc = await loop.run_in_executor(None, self._api.get_account)
        return {"equity": float(acc.equity), "cash": float(acc.cash), "buying_power": float(acc.buying_power)}


class PaperBroker(BrokerAdapter):
    """
    In-process paper trading broker — no network calls.
    Simulates fills with realistic slippage model.
    """

    def __init__(self, initial_equity: float = 100_000.0, slippage_bps: float = 5.0):
        self._equity = initial_equity
        self._cash = initial_equity
        self._positions: dict[str, dict] = {}
        self._orders: dict[str, Order] = {}
        self._slippage_bps = slippage_bps / 10_000

    async def submit_order(self, order: Order) -> Order:
        await asyncio.sleep(0.01)  # simulate network latency

        # Simulate fill: limit → fill at limit price + slippage
        fill_price = order.limit_price or 0.0
        if order.order_type == OrderType.MARKET:
            fill_price = fill_price * (1 + (self._slippage_bps if order.side == "buy" else -self._slippage_bps))

        order.avg_fill_price = round(fill_price, 6)
        order.filled_qty = order.quantity
        order.status = OrderStatus.FILLED
        order.filled_at = time.time()
        order.broker_order_id = f"paper_{order.id[:8]}"

        # Update internal state
        notional = fill_price * order.quantity
        if order.side == "buy":
            self._cash -= notional
            if order.asset in self._positions:
                self._positions[order.asset]["qty"] += order.quantity
                self._positions[order.asset]["value"] += notional
            else:
                self._positions[order.asset] = {"qty": order.quantity, "value": notional, "avg_price": fill_price}
        else:
            self._cash += notional
            if order.asset in self._positions:
                pos = self._positions[order.asset]
                pos["qty"] -= order.quantity
                if pos["qty"] <= 0:
                    del self._positions[order.asset]

        self._orders[order.id] = order
        return order

    async def cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders:
            self._orders[order_id].status = OrderStatus.CANCELLED
            return True
        return False

    async def get_order_status(self, order_id: str) -> Order:
        return self._orders.get(order_id, Order(id=order_id, status=OrderStatus.REJECTED))

    async def get_positions(self) -> dict[str, Any]:
        return self._positions.copy()

    async def get_account(self) -> dict[str, Any]:
        positions_value = sum(p["value"] for p in self._positions.values())
        return {
            "equity": self._cash + positions_value,
            "cash": self._cash,
            "buying_power": self._cash,
        }


class SmartOrderRouter:
    """
    Routes orders optimally:
    1. Try limit order at mid + small edge
    2. If not filled in timeout → convert to market
    3. Monitor and log slippage
    """

    def __init__(self, broker: BrokerAdapter, limit_timeout_sec: float = 30.0, slippage_tolerance_bps: float = 5.0):
        self._broker = broker
        self._timeout = limit_timeout_sec
        self._slippage_bps = slippage_tolerance_bps / 10_000

    async def execute_bracket(
        self, signal: TradeSignal, risk: RiskCheckResult, current_price: float
    ) -> BracketOrder:
        side = "buy" if signal.action == Signal.BUY else "sell"

        # Entry order — limit at current price with small edge
        edge = 0.0005 if side == "buy" else -0.0005
        entry_limit = current_price * (1 + edge)
        qty = risk.approved_position_size_usd / current_price

        entry = Order(
            asset=signal.asset,
            side=side,
            quantity=round(qty, 6),
            order_type=OrderType.LIMIT,
            limit_price=round(entry_limit, 6),
            metadata={"signal_id": signal.request_id, "strategy": "consensus"},
        )

        # Submit entry
        entry = await self._broker.submit_order(entry)

        # Wait for fill or timeout → fallback to market
        if entry.status == OrderStatus.SUBMITTED:
            deadline = time.time() + self._timeout
            while time.time() < deadline:
                await asyncio.sleep(2)
                entry = await self._broker.get_order_status(entry.broker_order_id)
                if entry.status == OrderStatus.FILLED:
                    break
            else:
                # Cancel and resubmit as market
                await self._broker.cancel_order(entry.broker_order_id)
                entry.order_type = OrderType.MARKET
                entry = await self._broker.submit_order(entry)

        fill_price = entry.avg_fill_price or current_price

        # Stop loss order
        sl_side = "sell" if side == "buy" else "buy"
        sl = Order(
            asset=signal.asset,
            side=sl_side,
            quantity=entry.filled_qty,
            order_type=OrderType.STOP,
            stop_price=risk.stop_loss_price,
            metadata={"type": "stop_loss", "parent": entry.id},
        )
        sl = await self._broker.submit_order(sl)

        # Take profit order
        tp = Order(
            asset=signal.asset,
            side=sl_side,
            quantity=entry.filled_qty,
            order_type=OrderType.LIMIT,
            limit_price=risk.take_profit_price,
            metadata={"type": "take_profit", "parent": entry.id},
        )
        tp = await self._broker.submit_order(tp)

        return BracketOrder(
            entry=entry, stop_loss=sl, take_profit=tp, signal_id=signal.request_id
        )
