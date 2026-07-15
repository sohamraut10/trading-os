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

import httpx as _httpx

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
    """
    Alpaca paper/live trading adapter.
    Uses httpx directly — no alpaca-trade-api SDK dependency (which conflicts
    with google-genai's websockets requirement).
    """

    def __init__(self, api_key: str, secret_key: str, base_url: str):
        self._base = base_url.rstrip("/")
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        }
        self._http = _httpx.AsyncClient(
            headers=self._headers,
            timeout=15.0,
        )

    async def submit_order(self, order: Order) -> Order:
        qty_val = order.quantity
        # Use decimal formatting for fractional quantities (crypto) — Alpaca accepts these.
        # Cap at 6 decimal places to avoid float precision issues where the 8th+ decimal
        # rounds up past the available balance (e.g., 0.86421605 > 0.864216045 available).
        if qty_val >= 1 and qty_val == int(qty_val):
            qty_str = str(int(qty_val))
        else:
            qty_str = f"{qty_val:.6f}".rstrip("0").rstrip(".")
        payload: dict[str, Any] = {
            "symbol": order.asset,
            "qty": qty_str,
            "side": order.side,
            "type": order.order_type.value,
            "time_in_force": "gtc",
        }
        if order.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and order.limit_price:
            payload["limit_price"] = f"{order.limit_price:.2f}"
        if order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and order.stop_price:
            payload["stop_price"] = f"{order.stop_price:.2f}"
        try:
            resp = await self._http.post(f"{self._base}/v2/orders", json=payload)
            data = resp.json()
            if resp.status_code in (200, 201):
                order.broker_order_id = data.get("id", "")
                order.status = OrderStatus.SUBMITTED
                log.info("ALPACA ORDER — %s %s %s qty=%s → id=%s status=%s",
                         order.side.upper(), order.order_type.value.upper(), order.asset,
                         order.quantity, order.broker_order_id, data.get("status"))
            else:
                raise RuntimeError(data.get("message") or str(data))
        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.metadata["error"] = str(e)
            log.error("ALPACA ORDER REJECTED — %s: %s", order.asset, e)
        return order

    async def cancel_order(self, order_id: str) -> bool:
        try:
            resp = await self._http.delete(f"{self._base}/v2/orders/{order_id}")
            return resp.status_code in (200, 204)
        except Exception:
            return False

    async def get_order_status(self, order_id: str) -> Order:
        resp = await self._http.get(f"{self._base}/v2/orders/{order_id}")
        resp.raise_for_status()
        data = resp.json()
        order = Order(broker_order_id=data["id"], asset=data["symbol"])
        order.status = OrderStatus.SUBMITTED
        order.filled_qty = float(data.get("filled_qty") or 0)
        order.avg_fill_price = float(data.get("filled_avg_price") or 0)
        order.side = data.get("side", "buy")
        order.quantity = float(data.get("qty") or 0)
        return order

    async def cancel_orders_for_symbol(self, symbol: str) -> int:
        try:
            resp = await self._http.get(f"{self._base}/v2/orders", params={"status": "open", "limit": 200})
            resp.raise_for_status()
            orders = resp.json()
            # Normalize: Alpaca returns "BTC/USD", we may receive "BTCUSD"
            sym_norm = symbol.replace("/", "")
            sym_orders = [o for o in orders if o.get("symbol", "").replace("/", "") == sym_norm]
            for o in sym_orders:
                await self._http.delete(f"{self._base}/v2/orders/{o['id']}")
            return len(sym_orders)
        except Exception:
            return 0

    async def close_position_native(self, symbol: str) -> Order:
        order = Order(asset=symbol, side="sell", order_type=OrderType.MARKET)
        try:
            await self.cancel_orders_for_symbol(symbol)
            await asyncio.sleep(0.5)
            resp = await self._http.delete(f"{self._base}/v2/positions/{symbol}")
            resp.raise_for_status()
            data = resp.json()
            order.broker_order_id = data.get("id", "")
            order.status = OrderStatus.SUBMITTED
            order.filled_qty = float(data.get("filled_qty") or 0)
            order.avg_fill_price = float(data.get("filled_avg_price") or 0)
        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.metadata["error"] = str(e)
        return order

    async def get_positions(self) -> dict[str, Any]:
        resp = await self._http.get(f"{self._base}/v2/positions")
        resp.raise_for_status()
        positions = resp.json()
        return {
            p["symbol"].replace("/", ""): {  # normalize "BTC/USD" → "BTCUSD"
                "qty": float(p["qty"]),
                "avg_price": float(p.get("avg_entry_price") or 0),
                "value": float(p.get("market_value") or 0),
                "exchange": "ALPACA",
            }
            for p in positions
        }

    async def get_account(self) -> dict[str, Any]:
        resp = await self._http.get(f"{self._base}/v2/account")
        resp.raise_for_status()
        data = resp.json()
        return {
            "equity": float(data["equity"]),
            "cash": float(data["cash"]),
            "buying_power": float(data["buying_power"]),
        }

    async def get_open_orders(self) -> list[dict]:
        # Map Alpaca types/statuses to Dhan-style names expected by position_monitor.
        _type_map = {
            "stop_limit": "STOP_LOSS", "stop": "STOP_LOSS_MARKET",
            "limit": "LIMIT", "market": "MARKET",
        }
        _status_map = {
            "new": "PENDING", "accepted": "PENDING", "held": "PENDING",
            "pending_new": "TRANSIT", "partially_filled": "PART_TRADED",
        }
        try:
            resp = await self._http.get(f"{self._base}/v2/orders", params={"status": "open", "limit": 200})
            resp.raise_for_status()
            orders = resp.json()
            return [
                {
                    "orderType": _type_map.get(o.get("type", "").lower(), o.get("type", "").upper()),
                    "orderStatus": _status_map.get(o.get("status", "").lower(), o.get("status", "").upper()),
                    "tradingSymbol": o.get("symbol", "").replace("/", ""),
                    "orderId": o.get("id", ""),
                }
                for o in orders
            ]
        except Exception:
            return []


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
            order.side = "buy" if str(data.get("transactionType", "")).upper() == "BUY" else "sell"
            order.quantity = float(data.get("quantity", 0))
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

        # For MCX futures the Dhan API expects qty in LOTS, not individual units.
        # 1 lot of LEAD = 5 MT; naively dividing budget by per-kg price sends
        # e.g. 44 lots (220 MT) instead of 0, triggering DH-906 margin failures.
        # Lookup the lot size from scrip master and size in lots; if even 1 lot
        # exceeds 40% of equity, reject before hitting the broker.
        lot_size = 1
        try:
            _, _entry_exch, _ = self._broker._resolve_instrument(signal.asset)
            if _entry_exch == "IDX_I":
                raise RuntimeError(
                    f"{signal.asset} is a spot index (IDX_I) — cannot be traded directly. "
                    "Use options or futures mode for index instruments."
                )
            if _entry_exch == "MCX_COMM":
                from core.data.instruments import scrip_master as _sm
                _ls = _sm.fno_lot_size(signal.asset)
                if _ls > 1:
                    lot_size = _ls
        except RuntimeError:
            raise
        except Exception:
            pass

        contract_value = current_price * lot_size
        if lot_size > 1 and contract_value > 0:
            # Size in lots; raise to 1-lot minimum, but cap at 40% equity
            num_lots = max(1, int(risk.approved_position_size_usd / contract_value))
            implied_equity = (
                risk.approved_position_size_usd / risk.approved_position_size_pct
                if risk.approved_position_size_pct > 0 else 0.0
            )
            one_lot_pct = contract_value / implied_equity if implied_equity > 0 else 1.0
            if one_lot_pct > 0.40:
                raise RuntimeError(
                    f"1 lot {signal.asset} costs ₹{contract_value:,.0f} "
                    f"({one_lot_pct:.0%} of equity) — exceeds 40% cap"
                )
            qty = num_lots * lot_size
        else:
            qty = risk.approved_position_size_usd / current_price

        # Use MARKET entry — LIMIT+30s-poll+MARKET-fallback was placing 2 charged
        # orders per trade (LIMIT placed → never fills in illiquid MCX contracts →
        # MARKET fallback). MARKET entry costs 1 order instead of 2.
        entry = Order(
            asset=signal.asset,
            side=side,
            quantity=round(qty, 6),
            order_type=OrderType.MARKET,
            metadata={"signal_id": signal.request_id, "strategy": "consensus"},
        )

        # Submit entry
        entry = await self._broker.submit_order(entry)
        if entry.status == OrderStatus.REJECTED:
            raise RuntimeError(f"Entry order rejected: {entry.metadata.get('error', 'unknown')}")

        # MARKET orders confirm on submission; Dhan returns filled_qty=0 until the
        # next poll, so fall back to the calculated qty and signal price for SL/TP sizing.
        # For Alpaca, poll after 1s to get the actual fill qty (avoids "insufficient balance"
        # when the SL qty slightly exceeds what was filled after fees).
        if isinstance(self._broker, AlpacaBroker) and entry.broker_order_id:
            # Poll up to 3× for fill qty — crypto paper orders fill within a few seconds.
            for _poll in range(3):
                await asyncio.sleep(1)
                try:
                    _filled = await self._broker.get_order_status(entry.broker_order_id)
                    if _filled.filled_qty > 0:
                        entry.filled_qty = _filled.filled_qty
                        entry.avg_fill_price = _filled.avg_fill_price or entry.avg_fill_price
                        break
                except Exception:
                    pass

        # Alpaca crypto commissions (~0.25%) reduce actual fill below the calculated qty.
        # Apply a 0.3% haircut to the fallback qty so the SL never exceeds available balance.
        _fill_qty = (
            entry.filled_qty
            if entry.filled_qty > 0
            else round(qty * 0.997, 6)
        )
        fill_price = entry.avg_fill_price or current_price

        # Stop loss — mandatory. If it fails we close the position immediately
        # to avoid holding a naked position.
        sl_side = "sell" if side == "buy" else "buy"
        # Dhan accepts STOP_LOSS_MARKET (price=0) for SELL SL orders (long exits),
        # but rejects it for BUY SL orders (short exits) with "Price should be greater
        # than Trigger Price". For short exits use STOP_LOSS (limit) with a 0.2%
        # buffer above trigger so the fill is guaranteed near the trigger.
        # Cap MCX SL within circuit band to prevent "rate not within chk limit"
        # on the trigger/limit price. Uses same 4.8% threshold as TP cap above.
        sl_trigger = risk.stop_loss_price
        try:
            _, _sl_exchange, _ = self._broker._resolve_instrument(signal.asset)
            if _sl_exchange == "MCX_COMM":
                if sl_side == "sell":   # long exit SL — must not be too far below entry
                    sl_trigger = max(sl_trigger, fill_price * 0.952)
                else:                   # short exit SL — must not be too far above entry
                    sl_trigger = min(sl_trigger, fill_price * 1.048)
        except Exception:
            pass

        # Alpaca crypto doesn't support plain STOP orders — use STOP_LIMIT for both directions.
        _is_alpaca = isinstance(self._broker, AlpacaBroker)
        if sl_side == "buy":
            sl_order_type = OrderType.STOP_LIMIT
            sl_limit = round(sl_trigger * 1.002, 2)
        elif _is_alpaca:
            sl_order_type = OrderType.STOP_LIMIT
            sl_limit = round(sl_trigger * 0.995, 2)  # 0.5% below trigger to guarantee fill
        else:
            sl_order_type = OrderType.STOP
            sl_limit = None
        # SL placement with retry — avoids the buy+immediate-close pattern that
        # wastes ₹40 brokerage with zero market PnL. Three attempts:
        #   1. Original trigger price
        #   2. Retry after 2s (clears transient API errors)
        #   3. Widen trigger 0.5% after another 2s (clears price-band rejections)
        # Emergency close only fires if all three fail.
        sl = None
        _sl_trigger_try = sl_trigger
        for _attempt in range(3):
            if sl_side == "buy":
                _sl_limit_try = round(_sl_trigger_try * 1.002, 2)
            elif sl_order_type == OrderType.STOP_LIMIT:
                # Alpaca crypto sell SL: limit below trigger to guarantee fill
                _sl_limit_try = round(_sl_trigger_try * 0.995, 2)
            else:
                _sl_limit_try = None
            _sl_candidate = Order(
                asset=signal.asset,
                side=sl_side,
                quantity=_fill_qty,
                order_type=sl_order_type,
                stop_price=_sl_trigger_try,
                limit_price=_sl_limit_try if sl_order_type == OrderType.STOP_LIMIT else sl_limit,
                metadata={"type": "stop_loss", "parent": entry.id},
            )
            sl = await self._broker.submit_order(_sl_candidate)
            if sl.status != OrderStatus.REJECTED:
                break
            log.warning("SL attempt %d/3 rejected for %s — %s",
                        _attempt + 1, signal.asset, sl.metadata.get("error", ""))
            if _attempt < 2:
                await asyncio.sleep(2)
            if _attempt == 1:
                # Widen trigger 0.5% away from fill to escape circuit-band rejection
                _sl_trigger_try = round(
                    sl_trigger * 0.995 if sl_side == "sell" else sl_trigger * 1.005, 2
                )

        if sl is None or sl.status == OrderStatus.REJECTED:
            log.error("SL failed after 3 attempts for %s — closing position to avoid naked exposure", signal.asset)
            try:
                close = Order(asset=signal.asset, side=sl_side, quantity=_fill_qty, order_type=OrderType.MARKET)
                await self._broker.submit_order(close)
            except Exception as close_err:
                log.error("Emergency close also failed for %s: %s", signal.asset, close_err)
            raise RuntimeError(f"Stop-loss order rejected for {signal.asset} — position closed")

        # Take profit order (best-effort, not mandatory)
        # MCX has a ±5% price-check band ("rate not within chk limit"); cap TP to 4.8%
        # from fill so the limit order isn't rejected before it can rest on the book.
        tp_price = risk.take_profit_price
        try:
            _, tp_exchange, _ = self._broker._resolve_instrument(signal.asset)
            if tp_exchange == "MCX_COMM":
                if sl_side == "sell":   # long trade → TP is a sell above entry
                    tp_price = min(tp_price, fill_price * 1.048)
                else:                   # short trade → TP is a buy below entry
                    tp_price = max(tp_price, fill_price * 0.952)
        except Exception:
            pass  # if resolution fails, use original TP price

        tp = Order(
            asset=signal.asset,
            side=sl_side,
            quantity=_fill_qty,
            order_type=OrderType.LIMIT,
            limit_price=tp_price,
            metadata={"type": "take_profit", "parent": entry.id},
        )
        tp = await self._broker.submit_order(tp)
        if tp.status == OrderStatus.REJECTED:
            log.warning("TP order rejected for %s — SL is active, position not closed", signal.asset)

        return BracketOrder(
            entry=entry, stop_loss=sl, take_profit=tp, signal_id=signal.request_id
        )
