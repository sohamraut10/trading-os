"""
Active position monitor.

Runs as a background task every POLL_INTERVAL seconds:
  1. Syncs portfolio.open_trades from the live broker
  2. For every open position — verifies an active stop-loss order exists;
     if missing, places one immediately using the configured max_trade_drawdown
  3. Emergency-closes any position whose unrealised loss exceeds
     EMERGENCY_DRAWDOWN_MULT × max_trade_drawdown

Dhan SL order format:
  - MCX (FUTCOM / INTRADAY): STOP_LOSS_MARKET (trigger only, price=0)
  - NSE equity (CNC): STOP_LOSS with limit price 0.5% below trigger to satisfy
    Dhan's "trigger > price" validation and avoid immediate fills
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from core.monitoring.alerts import Alert

if TYPE_CHECKING:
    from core.risk.risk_engine import RiskConfig, PortfolioState
    from core.monitoring.alerts import AlertRouter

log = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 30
EMERGENCY_DRAWDOWN_MULT = 2.0   # close if loss > 2× SL level
SL_RETRY_COOLDOWN_SEC = 300     # don't retry a failed SL within 5 minutes


class PositionMonitor:
    def __init__(self, broker, market_data, portfolio, risk_cfg, alert_router=None,
                 weights_manager=None) -> None:
        self._broker = broker
        self._market = market_data
        self._portfolio = portfolio
        self._risk_cfg = risk_cfg
        self._alerts = alert_router
        self._weights = weights_manager
        # symbol → timestamp of last SL failure (throttle retries)
        self._sl_fail_ts: dict[str, float] = {}
        # symbol → entry price snapshot for P&L calculation on close
        self._entry_prices: dict[str, float] = {}

    async def run_forever(self) -> None:
        log.info("Position monitor started (poll every %ds)", POLL_INTERVAL_SEC)
        while True:
            try:
                await self._check()
            except Exception:
                log.exception("Position monitor cycle error")
            await asyncio.sleep(POLL_INTERVAL_SEC)

    async def _check(self) -> None:
        from core.execution.broker_interface import DhanBroker, Order, OrderType, OrderStatus

        positions = await self._broker.get_positions()
        self._portfolio.open_trades = len(positions)

        # Sync portfolio.positions and cash from broker so the risk engine sees
        # current exposure — prevents over-allocation when in-memory state drifts.
        total_pos_value = 0.0
        synced_positions: dict[str, float] = {}
        for sym, pos in positions.items():
            qty = float(pos.get("qty", 0))
            avg = float(pos.get("avg_price", 0))
            val = float(pos.get("value", 0)) or (abs(qty) * avg)
            synced_positions[sym] = val
            total_pos_value += val
        self._portfolio.positions = synced_positions
        self._portfolio.cash = max(0.0, self._portfolio.equity - total_pos_value)

        if not positions:
            return

        # Fetch open orders once — detect existing active SL orders
        open_orders: list[dict] = []
        if isinstance(self._broker, DhanBroker):
            open_orders = await self._broker.get_open_orders()

        sl_covered: set[str] = set()
        for o in open_orders:
            if o.get("orderType") in ("STOP_LOSS", "SL", "STOP_LOSS_MARKET", "SLM") \
                    and o.get("orderStatus") in ("TRANSIT", "PENDING", "PART_TRADED"):
                sl_covered.add(o.get("tradingSymbol", ""))

        for symbol, pos in positions.items():
            qty = float(pos.get("qty", 0))
            avg_price = float(pos.get("avg_price", 0))
            if qty == 0 or avg_price == 0:
                continue

            is_long = qty > 0

            # ── Current price ─────────────────────────────────────────────────
            try:
                current_price = await self._market.get_current_price(symbol)
            except Exception:
                current_price = 0.0
            if current_price <= 0:
                log.warning("Price unavailable for %s — skipping drawdown check", symbol)
                pnl_pct = 0.0
            else:
                pnl_pct = (current_price - avg_price) / avg_price if is_long \
                    else (avg_price - current_price) / avg_price

            sl_pct = self._risk_cfg.max_trade_drawdown
            emergency_pct = sl_pct * EMERGENCY_DRAWDOWN_MULT

            # Track entry prices so we can compute P&L on close.
            # Reset on re-entry: if avg_price changed by >5% vs cached, treat as new position.
            cached_entry = self._entry_prices.get(symbol, 0.0)
            if avg_price > 0 and (
                cached_entry == 0.0
                or abs(avg_price - cached_entry) / cached_entry > 0.05
            ):
                self._entry_prices[symbol] = avg_price

            # ── Emergency close ───────────────────────────────────────────────
            if current_price > 0 and pnl_pct < -emergency_pct:
                log.error(
                    "EMERGENCY CLOSE — %s loss=%.2f%% exceeds %.2f%% threshold",
                    symbol, pnl_pct * 100, emergency_pct * 100,
                )
                try:
                    await self._broker.close_position_native(symbol)
                    self._portfolio.open_trades = max(0, self._portfolio.open_trades - 1)
                    if self._weights:
                        self._weights.resolve_trade_by_asset(symbol, pnl_pct)
                    self._entry_prices.pop(symbol, None)
                    if self._alerts:
                        await self._alerts._broadcast(Alert(
                            level="critical",
                            title=f"EMERGENCY CLOSE — {symbol}",
                            body=f"Loss {pnl_pct*100:.1f}% exceeded {emergency_pct*100:.1f}% threshold. Closed at market.",
                            asset=symbol,
                        ))
                except Exception:
                    log.exception("Emergency close failed for %s", symbol)
                continue

            # ── Place missing SL ──────────────────────────────────────────────
            if symbol in sl_covered:
                continue

            # Throttle retries — don't hammer Dhan if SL keeps failing
            last_fail = self._sl_fail_ts.get(symbol, 0)
            if time.time() - last_fail < SL_RETRY_COOLDOWN_SEC:
                continue

            sl_price = avg_price * (1 - sl_pct) if is_long else avg_price * (1 + sl_pct)
            sl_side = "sell" if is_long else "buy"

            # If market price is already past SL level → close at market immediately
            if current_price > 0 and (
                (is_long and current_price <= sl_price)
                or (not is_long and current_price >= sl_price)
            ):
                log.error(
                    "PAST SL — %s price=%.2f already beyond SL=%.2f — closing at market",
                    symbol, current_price, sl_price,
                )
                try:
                    await self._broker.close_position_native(symbol)
                    self._portfolio.open_trades = max(0, self._portfolio.open_trades - 1)
                    if self._weights:
                        self._weights.resolve_trade_by_asset(symbol, pnl_pct)
                    self._entry_prices.pop(symbol, None)
                    if self._alerts:
                        await self._alerts._broadcast(Alert(
                            level="critical",
                            title=f"PAST SL CLOSE — {symbol}",
                            body=f"Price {current_price:.2f} already past SL {sl_price:.2f}. Closed at market.",
                            asset=symbol,
                        ))
                except Exception:
                    log.exception("Market close failed for %s", symbol)
                continue

            log.warning(
                "MISSING SL — %s qty=%.0f avg=%.2f — placing SL @ %.2f",
                symbol, abs(qty), avg_price, sl_price,
            )

            exchange = pos.get("exchange", "")
            itype = "FUTCOM" if exchange == "MCX_COMM" else "EQUITY"

            # CNC equity requires STOP_LOSS (limit type) with price < trigger.
            # MCX INTRADAY futures work with STOP_LOSS_MARKET (no limit price needed).
            # NSE equity tick = ₹0.05; MCX varies but MCX uses SLM (no price needed).
            _tick = 0.05
            sl_price_ticked = round(round(sl_price / _tick) * _tick, 10)
            if itype == "EQUITY":
                order_type = OrderType.STOP_LIMIT
                lp_raw = sl_price * 0.995   # 0.5% buffer below trigger
                limit_price = round(round(lp_raw / _tick) * _tick, 10)
            else:
                order_type = OrderType.STOP
                limit_price = None

            sl_order = Order(
                asset=symbol,
                side=sl_side,
                quantity=abs(qty),
                order_type=order_type,
                stop_price=sl_price_ticked,
                limit_price=limit_price,
                metadata={
                    "type": "stop_loss",
                    "placed_by": "position_monitor",
                    "security_id": pos.get("security_id", ""),
                    "exchange": exchange,
                    "instrument_type": itype,
                },
            )
            try:
                sl_order = await self._broker.submit_order(sl_order)
                if sl_order.status == OrderStatus.REJECTED:
                    err = sl_order.metadata.get("error", "unknown")
                    self._sl_fail_ts[symbol] = time.time()
                    # T+2 unsettled equity — holding not yet in demat, SL not possible yet
                    if "Insufficient Holding" in err or "Insufficient holding" in err:
                        log.warning(
                            "SL deferred for %s — holding not yet settled (T+2). Will retry after %d min.",
                            symbol, SL_RETRY_COOLDOWN_SEC // 60,
                        )
                    else:
                        log.error("SL placement failed for %s: %s", symbol, err)
                        if self._alerts:
                            await self._alerts._broadcast(Alert(
                                level="critical",
                                title=f"SL FAILED — {symbol}",
                                body=(
                                    f"Auto stop-loss placement failed. Manual action required!\n"
                                    f"Error: {err}\n"
                                    f"Will retry in {SL_RETRY_COOLDOWN_SEC // 60} min."
                                ),
                                asset=symbol,
                            ))
                else:
                    self._sl_fail_ts.pop(symbol, None)
                    log.info("SL placed for %s @ %.2f", symbol, sl_price)
                    if self._alerts:
                        await self._alerts._broadcast(Alert(
                            level="warning",
                            title=f"Auto-SL placed — {symbol}",
                            body=f"Stop-loss at {sl_price:.2f} ({'long' if is_long else 'short'} {abs(qty):.0f} qty)",
                            asset=symbol,
                        ))
            except Exception:
                log.exception("SL placement error for %s", symbol)
                self._sl_fail_ts[symbol] = time.time()
