"""
Options execution router.

When TRADE_MODE=options, a directional signal on any F&O-eligible underlying
is converted to an options trade:
  BUY signal  → buy a CALL (CE) option
  SELL signal → buy a PUT (PE) option

Strike selection: `OPTIONS_OTM_STRIKES` steps OTM from the ATM strike.
Expiry selection: nearest weekly (index) or monthly (stock) with enough
  days remaining (configurable via OPTIONS_MIN_DAYS_TO_EXPIRY).

The entry order is a limit at LTP; SL is placed at
  entry_premium × (1 - OPTIONS_SL_PCT) as a STOP_LOSS_MARKET order.
Take-profit is skipped (let the signal re-evaluate; options are closed
when the underlying reverses).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.agents.base_agent import Signal
    from core.agents.meta_agent import TradeSignal
    from core.risk.risk_engine import RiskCheckResult
    from core.execution.broker_interface import DhanBroker

log = logging.getLogger(__name__)

# Index underlyings → weekly expiry preferred; stocks → monthly
_INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "NIFTYNXT50", "MIDCPNIFTY", "SENSEX"}


class OptionsRouter:
    """
    Converts a directional TradeSignal into an options buy order.
    """

    def __init__(self, broker, market_data, otm_strikes: int = 1,
                 min_days_to_expiry: int = 2, sl_pct: float = 0.50) -> None:
        self._broker = broker
        self._market = market_data
        self._otm_strikes = otm_strikes
        self._min_dte = min_days_to_expiry
        self._sl_pct = sl_pct

    async def execute(
        self,
        signal: "TradeSignal",
        risk: "RiskCheckResult",
        spot_price: float,
        prefetched: dict | None = None,
    ) -> dict[str, Any]:
        """
        Place an options order for the signal. Returns order summary dict.
        Raises RuntimeError if the option cannot be resolved or order fails.

        prefetched: optional dict with keys {expiry, oc, spot} already fetched by the
        orchestrator's PCR cache — avoids a second back-to-back Dhan API call that
        triggers silent rate-limit empty responses.
        """
        from core.agents.base_agent import Signal as Sig
        from core.execution.broker_interface import Order, OrderType, OrderStatus
        from core.data.instruments import scrip_master

        underlying = signal.asset.upper()
        is_call = signal.action == Sig.BUY

        # ── 1. Resolve underlying security_id ────────────────────────────────
        inst = scrip_master.resolve(underlying)
        if not inst:
            raise RuntimeError(f"Unknown underlying: {underlying}")
        security_id = inst.security_id
        exchange = inst.exchange

        # Look up the F&O lot size from FUTSTK rows (equity scrip rows have lot=1)
        fno_lot = scrip_master.fno_lot_size(underlying)
        if fno_lot <= 1 and underlying not in _INDEX_UNDERLYINGS:
            log.warning("No F&O lot size found for %s — defaulting to 1 (check scrip master)", underlying)

        from datetime import date as _date

        # ── 2. Pick expiry and compute DTE ───────────────────────────────────
        if prefetched and prefetched.get("expiry") and prefetched.get("oc"):
            expiry = prefetched["expiry"]
            log.info("OPTIONS reusing cached chain for %s expiry=%s (skipping API re-fetch)", underlying, expiry)
        else:
            expiry = await self._pick_expiry(security_id, exchange, underlying)
            if not expiry:
                raise RuntimeError(f"No suitable expiry found for {underlying}")

        days_to_expiry = max(0, (_date.fromisoformat(expiry) - _date.today()).days)

        # ── 3. Fetch option chain (or reuse cached) and pick strike ──────────
        if prefetched and prefetched.get("oc"):
            oc = prefetched["oc"]
            spot = float(prefetched.get("spot") or spot_price) or spot_price
        else:
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                None,
                lambda: self._broker._dhan.option_chain(int(security_id), exchange, expiry),
            )
            chain_data = (raw.get("data", {}) or {}).get("data", {}) or {}
            spot = float(chain_data.get("last_price", spot_price) or spot_price)
            oc = chain_data.get("oc", {}) or {}

        if not oc:
            raise RuntimeError(f"Empty option chain for {underlying} {expiry}")

        option_sid, premium, strike, lot_size = self._pick_strike(
            oc, spot, is_call, fno_lot
        )
        if not option_sid:
            raise RuntimeError(f"Could not find liquid {'CE' if is_call else 'PE'} strike for {underlying}")

        # ── 4. Size in lots ──────────────────────────────────────────────────
        cost_per_lot = premium * lot_size
        if cost_per_lot <= 0:
            raise RuntimeError(f"Zero premium for {underlying} {strike} {'CE' if is_call else 'PE'}")

        if cost_per_lot > risk.approved_position_size_usd:
            # Kelly budget is below 1-lot minimum. Infer total equity and check
            # if 1 lot is still safe (same 40% cap as equity Gate 3b in risk engine).
            implied_equity = (
                risk.approved_position_size_usd / risk.approved_position_size_pct
                if risk.approved_position_size_pct > 0 else 0.0
            )
            lot_pct_equity = cost_per_lot / implied_equity if implied_equity > 0 else 1.0
            if lot_pct_equity > 0.40:
                raise RuntimeError(
                    f"Minimum lot cost ₹{cost_per_lot:.0f} ({lot_pct_equity:.0%} of equity) "
                    f"exceeds 40% safety cap — skipping {underlying}"
                )
            log.warning(
                "OPTIONS raised to 1-lot minimum: %s ₹%.0f (%.1f%% of equity, kelly budget was ₹%.0f)",
                underlying, cost_per_lot, lot_pct_equity * 100, risk.approved_position_size_usd,
            )
            num_lots = 1
        else:
            num_lots = max(1, int(risk.approved_position_size_usd / cost_per_lot))

        qty = num_lots * lot_size

        log.info(
            "OPTIONS TRADE — %s %s | %s %d%s @ ₹%.2f | %d lots × %d = %d qty | expiry=%s DTE=%d",
            underlying, "CE" if is_call else "PE",
            "CALL" if is_call else "PUT", strike, "CE" if is_call else "PE",
            premium, num_lots, lot_size, qty, expiry, days_to_expiry,
        )

        # ── 5. Entry order (buy option) ──────────────────────────────────────
        opt_exchange = "NSE_FNO"
        itype = "OPTIDX" if underlying in _INDEX_UNDERLYINGS else "OPTSTK"

        entry = Order(
            asset=f"{underlying}-{strike}-{'CE' if is_call else 'PE'}",
            side="buy",
            quantity=qty,
            order_type=OrderType.LIMIT,
            limit_price=round(premium * 1.002, 2),   # tiny edge above LTP
            metadata={
                "security_id": str(option_sid),
                "exchange": opt_exchange,
                "instrument_type": itype,
                "type": "options_entry",
                "underlying": underlying,
                "strike": strike,
                "option_type": "CE" if is_call else "PE",
                "expiry": expiry,
            },
        )
        entry = await self._broker.submit_order(entry)
        if entry.status == OrderStatus.REJECTED:
            raise RuntimeError(f"Options entry rejected: {entry.metadata.get('error')}")

        fill_premium = entry.avg_fill_price or premium

        # ── 6. Stop-loss on option premium ───────────────────────────────────
        # Dynamic SL: tighter for short-dated options where theta eats 20-40%/day.
        # Floor of 25% allows intraday moves; scales up to self._sl_pct for 5+ DTE.
        dynamic_sl_pct = min(self._sl_pct, 0.25 + 0.05 * days_to_expiry)
        sl_trigger = round(fill_premium * (1 - dynamic_sl_pct), 2)
        log.info("SL calc — DTE=%d, sl_pct=%.0f%% (dynamic), trigger=₹%.2f", days_to_expiry, dynamic_sl_pct * 100, sl_trigger)
        sl = Order(
            asset=entry.asset,
            side="sell",
            quantity=qty,
            order_type=OrderType.STOP,
            stop_price=sl_trigger,
            metadata={
                "security_id": str(option_sid),
                "exchange": opt_exchange,
                "instrument_type": itype,
                "type": "stop_loss",
                "placed_by": "options_router",
            },
        )
        sl = await self._broker.submit_order(sl)
        if sl.status == OrderStatus.REJECTED:
            log.error("Options SL rejected for %s — closing position", entry.asset)
            close = Order(asset=entry.asset, side="sell", quantity=qty,
                          order_type=OrderType.MARKET,
                          metadata={"security_id": str(option_sid), "exchange": opt_exchange, "instrument_type": itype})
            await self._broker.submit_order(close)
            raise RuntimeError(f"Options SL rejected, position closed: {sl.metadata.get('error')}")

        # ── 7. Take-profit at 1:2 R:R on premium ────────────────────────────
        # Risk  = fill_premium × dynamic_sl_pct
        # Target = 2 × risk = fill_premium × 2 × dynamic_sl_pct
        # TP price = fill_premium + target = fill_premium × (1 + 2 × dynamic_sl_pct)
        tp_premium = round(fill_premium * (1 + 2 * dynamic_sl_pct), 2)
        tp = Order(
            asset=entry.asset,
            side="sell",
            quantity=qty,
            order_type=OrderType.LIMIT,
            limit_price=tp_premium,
            metadata={
                "security_id": str(option_sid),
                "exchange": opt_exchange,
                "instrument_type": itype,
                "type": "take_profit",
                "placed_by": "options_router",
            },
        )
        tp = await self._broker.submit_order(tp)
        if tp.status == OrderStatus.REJECTED:
            log.warning(
                "TP order rejected for %s — SL active, will exit on signal reversal",
                entry.asset,
            )
            tp_premium = None

        log.info(
            "R:R set — SL=₹%.2f (-%d%%) | TP=₹%.2f (+%d%%) | 1:2 R:R on premium",
            sl_trigger, int(dynamic_sl_pct * 100),
            tp_premium or 0, int(dynamic_sl_pct * 200),
        )

        return {
            "underlying": underlying,
            "option": f"{strike}{'CE' if is_call else 'PE'}",
            "expiry": expiry,
            "days_to_expiry": days_to_expiry,
            "lots": num_lots,
            "qty": qty,
            "entry_premium": fill_premium,
            "sl_premium": sl_trigger,
            "tp_premium": tp_premium,
            "sl_pct": round(dynamic_sl_pct * 100, 1),
            "rr": "1:2",
            "cost": round(cost_per_lot * num_lots, 2),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _pick_expiry(self, security_id: str, exchange: str, underlying: str) -> str | None:
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(
                None,
                lambda: self._broker._dhan.expiry_list(int(security_id), exchange),
            )
            expiries: list[str] = (raw.get("data", {}) or {}).get("data", []) or []
        except Exception as exc:
            log.error("expiry_list failed for %s: %s", underlying, exc)
            return None

        today = date.today()
        is_index = underlying in _INDEX_UNDERLYINGS

        for exp_str in sorted(expiries):
            try:
                exp_date = date.fromisoformat(exp_str[:10])
            except ValueError:
                continue
            days_left = (exp_date - today).days
            min_dte = self._min_dte if is_index else max(self._min_dte, 7)
            if days_left >= min_dte:
                return exp_str[:10]
        return None

    def _pick_strike(
        self, oc: dict, spot: float, is_call: bool, default_lot: int
    ) -> tuple[str | None, float, float, int]:
        """
        Returns (security_id, premium, strike, lot_size).
        Picks `_otm_strikes` steps OTM from ATM in the direction of the trade.
        Filters out strikes with zero OI or zero premium.
        """
        strikes = sorted(float(k) for k in oc.keys())
        if not strikes:
            return None, 0.0, 0.0, default_lot

        # Find ATM index (nearest strike to spot)
        atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))

        # OTM: for call → higher strikes; for put → lower strikes
        if is_call:
            target_idx = min(atm_idx + self._otm_strikes, len(strikes) - 1)
        else:
            target_idx = max(atm_idx - self._otm_strikes, 0)

        # Scan outward from target until we find a liquid strike
        direction = 1 if is_call else -1
        for offset in range(len(strikes)):
            idx = target_idx + offset * direction
            if not (0 <= idx < len(strikes)):
                break
            strike = strikes[idx]
            leg_key = "ce" if is_call else "pe"
            leg = (oc.get(str(int(strike))) or oc.get(str(strike)) or {}).get(leg_key) or {}
            if not leg:
                # Try string key as returned by Dhan (may not be int-formatted)
                for k, v in oc.items():
                    if abs(float(k) - strike) < 0.01:
                        leg = (v or {}).get(leg_key) or {}
                        break
            premium = float(leg.get("last_price", 0) or 0)
            oi = int(leg.get("oi", 0) or 0)
            sid = leg.get("security_id")
            lot = int(leg.get("lot_size", default_lot) or default_lot)
            if sid and premium > 0 and oi > 0:
                return str(sid), premium, strike, lot

        return None, 0.0, 0.0, default_lot
