"""
Execution Engine — Broker Interface & Smart Order Router
Translates TradeSignal + RiskCheckResult into actual market orders.
Implements smart order routing: limit orders first, market orders as fallback.
"""
import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)

from core.agents.base_agent import Signal
from core.agents.meta_agent import TradeSignal
from core.risk.risk_engine import RiskCheckResult

try:
    import alpaca_trade_api as _alpaca_trade_api
    _ALPACA_AVAILABLE = True
except ImportError:
    _ALPACA_AVAILABLE = False

try:
    import dhanhq as _dhanhq
    _DHANHQ_AVAILABLE = True
except ImportError:
    _DHANHQ_AVAILABLE = False

from core.data.instruments import scrip_master


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
        # Alpaca rejects market orders that include limit_price/stop_price
        limit_price = order.limit_price if order.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) else None
        stop_price = order.stop_price if order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) else None
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self._api.submit_order(
                    symbol=order.asset,
                    qty=order.quantity,
                    side=order.side,
                    type=order.order_type.value,
                    time_in_force="gtc",
                    limit_price=limit_price,
                    stop_price=stop_price,
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

    async def cancel_orders_for_symbol(self, symbol: str) -> int:
        """Cancel all open orders for a symbol before submitting a close."""
        loop = asyncio.get_event_loop()
        try:
            orders = await loop.run_in_executor(None, lambda: self._api.list_orders(status="open"))
            sym_orders = [o for o in orders if o.symbol == symbol]
            for o in sym_orders:
                await loop.run_in_executor(None, lambda oid=o.id: self._api.cancel_order(oid))
            return len(sym_orders)
        except Exception:
            return 0

    async def close_position_native(self, symbol: str) -> Order:
        """Cancel all open orders for the symbol, then close the position via Alpaca's native endpoint."""
        loop = asyncio.get_event_loop()
        order = Order(asset=symbol, side="sell", order_type=OrderType.MARKET)
        # Normalize: Alpaca may store as "BTC/USD" while we use "BTCUSD"
        normalized = symbol.replace("/", "").replace("-", "")
        try:
            # Cancel all open orders for this symbol to free locked inventory
            open_orders = await loop.run_in_executor(None, lambda: self._api.list_orders(status="open"))
            for o in open_orders:
                if o.symbol.replace("/", "").replace("-", "") == normalized:
                    try:
                        await loop.run_in_executor(None, lambda oid=o.id: self._api.cancel_order(oid))
                    except Exception:
                        pass
            # Small delay for cancellations to propagate
            await asyncio.sleep(1.0)
            result = await loop.run_in_executor(None, lambda: self._api.close_position(symbol))
            order.broker_order_id = result.id
            order.status = OrderStatus.SUBMITTED
            order.filled_qty = float(result.filled_qty or 0)
            order.avg_fill_price = float(result.filled_avg_price or 0)
        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.metadata["error"] = str(e)
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


class DhanBroker(BrokerAdapter):
    """
    Dhan broker adapter for Indian markets (NSE/BSE equities, F&O, currency, commodity).

    Symbol resolution: Dhan identifies instruments by a numeric security_id.
    Pass the security_id directly (e.g. "500325" for RELIANCE NSE) or a
    ticker string (e.g. "RELIANCE") — ticker strings trigger a search-API
    lookup on first use and the result is cached for the session.

    Set order.metadata["exchange"] to override the default exchange segment
    (e.g. "BSE_EQ", "NSE_FNO"). Default comes from the constructor arg.
    Set order.metadata["security_id"] to skip symbol resolution entirely.
    """

    # Dhan order-type mapping
    _ORDER_TYPE_MAP = {
        OrderType.MARKET:    "MARKET",
        OrderType.LIMIT:     "LIMIT",
        OrderType.STOP:      "STOP_LOSS_MARKET",
        OrderType.STOP_LIMIT: "STOP_LOSS",
        OrderType.TRAILING_STOP: "MARKET",  # not natively supported — falls back
    }

    # Dhan status → our OrderStatus
    _STATUS_MAP = {
        "PENDING":    OrderStatus.PENDING,
        "TRANSIT":    OrderStatus.SUBMITTED,
        "TRADED":     OrderStatus.FILLED,
        "PART_TRADED": OrderStatus.PARTIAL,
        "CANCELLED":  OrderStatus.CANCELLED,
        "REJECTED":   OrderStatus.REJECTED,
        "EXPIRED":    OrderStatus.CANCELLED,
    }

    def __init__(
        self,
        client_id: str,
        access_token: str,
        default_exchange: str = "NSE_EQ",
        product_type: str = "CNC",
    ):
        if not _DHANHQ_AVAILABLE:
            raise RuntimeError(
                "dhanhq is not installed — run `pip install dhanhq` or "
                "unset DHAN_CLIENT_ID to use PaperBroker instead."
            )
        ctx = _dhanhq.DhanContext(client_id, access_token)
        self._dhan = _dhanhq.dhanhq(ctx)
        self._default_exchange = default_exchange
        self._product_type = product_type
        self._symbol_cache: dict[str, str] = {}  # ticker → security_id

    def _resolve_instrument(self, symbol: str) -> tuple[str, str, str]:
        """
        Return (security_id, exchange, instrument_type) for a symbol.
        Uses the live scrip master; falls back to passing the symbol as-is.
        Also handles full trading symbols like "CRUDEOIL-20Jul2026-FUT" by
        stripping the futures suffix and resolving the base symbol.
        """
        if symbol.lstrip("-").isdigit():
            return symbol, self._default_exchange, "EQUITY"
        inst = scrip_master.resolve(symbol)
        if inst:
            return inst.security_id, inst.exchange, inst.instrument_type
        # Strip futures/options suffix (e.g. "CRUDEOIL-20Jul2026-FUT" → "CRUDEOIL")
        base = symbol.split("-")[0]
        if base != symbol:
            inst = scrip_master.resolve(base)
            if inst:
                log.debug("Resolved %s via base symbol %s", symbol, base)
                return inst.security_id, inst.exchange, inst.instrument_type
        log.warning("Unknown symbol %s — passing as-is to Dhan", symbol)
        return symbol, self._default_exchange, "EQUITY"

    def _product_type_for(self, instrument_type: str) -> str:
        """MCX futures and options use INTRADAY; equities use configured default."""
        if instrument_type in ("FUTCOM", "OPTIDX", "OPTSTK", "FUTIDX"):
            return "INTRADAY"
        return self._product_type

    async def submit_order(self, order: Order) -> Order:
        loop = asyncio.get_event_loop()
        if order.metadata.get("security_id") and order.metadata.get("exchange"):
            security_id = order.metadata["security_id"]
            exchange = order.metadata["exchange"]
            itype = order.metadata.get("instrument_type", "EQUITY")
        else:
            security_id, exchange, itype = self._resolve_instrument(order.asset)

        product_type = self._product_type_for(itype)
        dhan_order_type = self._ORDER_TYPE_MAP.get(order.order_type, "MARKET")
        price = order.limit_price or 0
        trigger_price = order.stop_price or 0
        transaction_type = "BUY" if order.side.lower() == "buy" else "SELL"

        # MCX: quantity must be in lots; quantity from risk engine is in ₹ worth ÷ price
        # Ensure at least 1 lot
        inst = scrip_master.resolve(order.asset)
        lot_size = inst.lot_size if inst else 1
        qty = max(1, round(int(order.quantity) / lot_size)) * lot_size if lot_size > 1 else max(1, int(order.quantity))

        log.info("DHAN ORDER — %s %s %s qty=%d price=%.2f trigger=%.2f sid=%s exch=%s prod=%s",
                 transaction_type, dhan_order_type, order.asset, qty, price, trigger_price,
                 security_id, exchange, product_type)
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self._dhan.place_order(
                    security_id=security_id,
                    exchange_segment=exchange,
                    transaction_type=transaction_type,
                    quantity=qty,
                    order_type=dhan_order_type,
                    product_type=product_type,
                    price=price,
                    trigger_price=trigger_price,
                ),
            )
            resp = result if isinstance(result, dict) else {}
            log.info("DHAN RESPONSE — %s", resp)
            if resp.get("status") == "failure":
                remarks = resp.get("remarks", {})
                err_code = remarks.get("error_code", "UNKNOWN") if isinstance(remarks, dict) else str(remarks)
                err_msg = remarks.get("error_message", "") if isinstance(remarks, dict) else ""
                raise RuntimeError(f"{err_code}: {err_msg}")
            order_id = resp.get("data", {}).get("orderId", "") if isinstance(resp.get("data"), dict) else str(resp.get("orderId", ""))
            order.broker_order_id = order_id
            order.status = OrderStatus.SUBMITTED
        except Exception as e:
            log.exception("DHAN SUBMIT ERROR — %s %s: %s", order.asset, transaction_type, e)
            order.status = OrderStatus.REJECTED
            order.metadata["error"] = str(e)
        return order

    async def cancel_order(self, order_id: str) -> bool:
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, lambda: self._dhan.cancel_order(order_id))
            return True
        except Exception:
            return False

    async def get_open_orders(self) -> list[dict]:
        """Return all pending/transit orders from Dhan for today."""
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(None, self._dhan.get_order_list)
            rows = raw.get("data", []) if isinstance(raw, dict) else []
            if not isinstance(rows, list):
                return []
            open_statuses = {"TRANSIT", "PENDING", "PART_TRADED"}
            return [r for r in rows if r.get("orderStatus", "") in open_statuses]
        except Exception as exc:
            log.warning("get_open_orders failed: %s", exc)
            return []

    async def get_trade_history(self, days: int = 30) -> list[dict]:
        """Return executed trades from Dhan (today's trade book + historical)."""
        from datetime import date, timedelta
        loop = asyncio.get_event_loop()
        today = date.today()
        from_date = (today - timedelta(days=days)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")
        trades: list[dict] = []

        try:
            raw = await loop.run_in_executor(None, self._dhan.get_trade_book)
            rows = (raw.get("data", []) if isinstance(raw, dict) else []) or []
            if isinstance(rows, list):
                trades.extend(rows)
        except Exception as exc:
            log.warning("get_trade_book failed: %s", exc)

        try:
            raw = await loop.run_in_executor(
                None, lambda: self._dhan.get_trade_history(from_date, to_date, 0)
            )
            rows = (raw.get("data", []) if isinstance(raw, dict) else []) or []
            if isinstance(rows, list):
                trades.extend(rows)
        except Exception as exc:
            log.warning("get_trade_history failed: %s", exc)

        seen: set[str] = set()
        result: list[dict] = []
        for t in trades:
            tid = str(t.get("tradeId") or t.get("orderId") or id(t))
            if tid not in seen:
                seen.add(tid)
                result.append(t)
        return sorted(result, key=lambda x: x.get("createTime", ""), reverse=True)

    async def get_order_status(self, order_id: str) -> Order:
        loop = asyncio.get_event_loop()
        order = Order(broker_order_id=order_id)
        try:
            raw = await loop.run_in_executor(None, lambda: self._dhan.get_order_by_id(order_id))
            data = raw.get("data", {}) if isinstance(raw, dict) else {}
            if isinstance(data, list):
                data = data[0] if data else {}
            order.status = self._STATUS_MAP.get(data.get("orderStatus", ""), OrderStatus.SUBMITTED)
            order.filled_qty = float(data.get("filledQty", 0))
            order.avg_fill_price = float(data.get("price", 0))
            # Store the full trading symbol in metadata; keep order.asset as the
            # user-facing symbol so downstream resubmits resolve correctly.
            order.metadata["trading_symbol"] = data.get("tradingSymbol", "")
            if not order.asset:
                order.asset = data.get("tradingSymbol", "")
        except Exception as e:
            order.metadata["error"] = str(e)
        return order

    async def get_positions(self) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(None, self._dhan.get_positions)
            raw_data = raw.get("data") if isinstance(raw, dict) else None
            rows = raw_data if isinstance(raw_data, list) else []
            result = {}
            for p in rows:
                symbol = p.get("tradingSymbol", p.get("securityId", ""))
                net_qty = float(p.get("netQty", 0))
                if net_qty != 0:
                    avg = float(p.get("buyAvg", p.get("costPrice", 0)) or 0)
                    # Dhan positions do not include lastTradedPrice; use their
                    # pre-calculated unrealizedProfit and derive LTP from it.
                    unrealized = float(p.get("unrealizedProfit", 0) or 0)
                    realized   = float(p.get("realizedProfit",   0) or 0)
                    ltp = round(avg + unrealized / net_qty, 2) if avg and net_qty else avg
                    pnl_pct = round(unrealized / (avg * abs(net_qty)) * 100, 2) if avg and net_qty else 0.0
                    result[symbol] = {
                        "qty": net_qty,
                        "avg_price": avg,
                        "ltp": ltp,
                        "value": round(net_qty * ltp, 2) if ltp else round(net_qty * avg, 2),
                        "unrealized_pnl": round(unrealized, 2),
                        "unrealized_pnl_pct": pnl_pct,
                        "realized_pnl": round(realized, 2),
                        "security_id": str(p.get("securityId", "")),
                        "exchange": p.get("exchangeSegment", self._default_exchange),
                        "product": p.get("productType", "CNC"),
                        "day_buy_qty": float(p.get("dayBuyQty", 0) or 0),
                        "day_sell_qty": float(p.get("daySellQty", 0) or 0),
                    }
            return result
        except Exception:
            return {}

    async def get_account(self) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(None, self._dhan.get_fund_limits)
            raw_data = raw.get("data") if isinstance(raw, dict) else None
            data = raw_data if isinstance(raw_data, dict) else {}
            available = float(data.get("availabelBalance", data.get("availableBalance", 0)))
            used = float(data.get("utilizedAmount", 0))
            return {
                "equity": available + used,
                "cash": available,
                "buying_power": available,
            }
        except Exception:
            return {"equity": 0.0, "cash": 0.0, "buying_power": 0.0}

    async def close_position_native(self, symbol: str) -> Order:
        """Cancel open orders for symbol then place a market sell for the full net qty."""
        positions = await self.get_positions()
        pos = positions.get(symbol, {})
        net_qty = pos.get("qty", 0)
        order = Order(asset=symbol, side="sell", order_type=OrderType.MARKET)

        if net_qty <= 0:
            order.status = OrderStatus.REJECTED
            order.metadata["error"] = f"No open position for {symbol}"
            return order

        # Cancel any open orders for this symbol
        loop = asyncio.get_event_loop()
        try:
            open_orders = await loop.run_in_executor(None, self._dhan.get_order_list)
            orders_data = open_orders.get("data", []) if isinstance(open_orders, dict) else open_orders or []
            for o in orders_data:
                if (o.get("tradingSymbol") == symbol and
                        o.get("orderStatus") in ("PENDING", "TRANSIT", "PART_TRADED")):
                    try:
                        await loop.run_in_executor(
                            None, lambda oid=o["orderId"]: self._dhan.cancel_order(oid)
                        )
                    except Exception:
                        pass
        except Exception:
            pass

        security_id = pos.get("security_id") or self._resolve_instrument(symbol)[0]
        exchange = pos.get("exchange", self._default_exchange)
        close_order = Order(
            asset=symbol,
            side="sell",
            quantity=abs(net_qty),
            order_type=OrderType.MARKET,
            metadata={"security_id": security_id, "exchange": exchange},
        )
        return await self.submit_order(close_order)


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
        if entry.status == OrderStatus.REJECTED:
            raise RuntimeError(f"Entry order rejected: {entry.metadata.get('error', 'unknown')}")

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

        # Stop loss — mandatory. If it fails we close the position immediately
        # to avoid holding a naked position.
        sl_side = "sell" if side == "buy" else "buy"
        # Dhan accepts STOP_LOSS_MARKET (price=0) for SELL SL orders (long exits),
        # but rejects it for BUY SL orders (short exits) with "Price should be greater
        # than Trigger Price". For short exits use STOP_LOSS (limit) with a 0.2%
        # buffer above trigger so the fill is guaranteed near the trigger.
        if sl_side == "buy":
            sl_order_type = OrderType.STOP_LIMIT
            sl_limit = round(risk.stop_loss_price * 1.002, 2)
        else:
            sl_order_type = OrderType.STOP
            sl_limit = None
        sl = Order(
            asset=signal.asset,
            side=sl_side,
            quantity=entry.filled_qty,
            order_type=sl_order_type,
            stop_price=risk.stop_loss_price,
            limit_price=sl_limit,
            metadata={"type": "stop_loss", "parent": entry.id},
        )
        sl = await self._broker.submit_order(sl)
        if sl.status == OrderStatus.REJECTED:
            log.error("SL order rejected for %s — closing position to avoid naked exposure", signal.asset)
            try:
                close = Order(asset=signal.asset, side=sl_side, quantity=entry.filled_qty, order_type=OrderType.MARKET)
                await self._broker.submit_order(close)
            except Exception as close_err:
                log.error("Emergency close also failed for %s: %s", signal.asset, close_err)
            raise RuntimeError(f"Stop-loss order rejected for {signal.asset} — position closed")

        # Take profit order (best-effort, not mandatory)
        tp = Order(
            asset=signal.asset,
            side=sl_side,
            quantity=entry.filled_qty,
            order_type=OrderType.LIMIT,
            limit_price=risk.take_profit_price,
            metadata={"type": "take_profit", "parent": entry.id},
        )
        tp = await self._broker.submit_order(tp)
        if tp.status == OrderStatus.REJECTED:
            log.warning("TP order rejected for %s — SL is active, position not closed", signal.asset)

        return BracketOrder(
            entry=entry, stop_loss=sl, take_profit=tp, signal_id=signal.request_id
        )
