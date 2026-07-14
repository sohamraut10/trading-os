"""
Smart Exit Engine for Indian Options.
Monitors open option positions and generates exit signals based on:
  - Fixed target (% gain on premium)
  - Trailing stop on premium
  - Theta decay (daily time decay > threshold)
  - Volatility crush (IV collapsed post-event)
  - Gamma risk (high gamma near expiry)
  - OI shift exit (OI unwinding against position)
  - Consensus reversal (signal direction flipped)
  - Maximum daily loss stop
  - Time exit (day end / hours before expiry)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

log = logging.getLogger(__name__)


class ExitReason(str, Enum):
    TARGET_HIT = "target_hit"
    TRAILING_STOP = "trailing_stop"
    FIXED_STOP = "fixed_stop"
    THETA_DECAY = "theta_decay"
    VOL_CRUSH = "vol_crush"
    GAMMA_RISK = "gamma_risk"
    OI_SHIFT = "oi_shift"
    CONSENSUS_REVERSAL = "consensus_reversal"
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    TIME_EXIT = "time_exit"
    EMERGENCY_FLATTEN = "emergency_flatten"


@dataclass
class OptionPosition:
    """Tracks a single open option position."""
    symbol: str                 # e.g. "NIFTY-24500-CE"
    underlying: str             # e.g. "NIFTY"
    option_type: str            # "CE" or "PE"
    strike: float
    expiry: str                 # "YYYY-MM-DD"
    entry_premium: float        # premium paid per unit
    current_premium: float      # live LTP
    quantity: int               # signed (positive = long)
    lot_size: int
    entry_time: float           # unix timestamp
    days_to_expiry: int
    entry_iv: float             # IV at entry
    current_iv: float           # live IV
    # Tracking
    peak_premium: float = 0.0   # highest premium seen since entry
    trailing_sl: float = 0.0    # current trailing stop level
    daily_pnl: float = 0.0

    @property
    def pnl_pct(self) -> float:
        if self.entry_premium <= 0:
            return 0.0
        return (self.current_premium - self.entry_premium) / self.entry_premium

    @property
    def pnl_inr(self) -> float:
        return (self.current_premium - self.entry_premium) * abs(self.quantity) * self.lot_size

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    def update(self, new_premium: float, new_iv: float = 0.0) -> None:
        self.current_premium = new_premium
        if new_iv > 0:
            self.current_iv = new_iv
        if new_premium > self.peak_premium:
            self.peak_premium = new_premium


@dataclass
class ExitSignal:
    """An exit recommendation for a position."""
    position_symbol: str
    exit_now: bool
    reason: ExitReason
    urgency: str        # "immediate" | "next_tick" | "eod"
    detail: str
    recommended_price: float = 0.0   # 0 = market order

    def to_dict(self) -> dict:
        return {
            "symbol": self.position_symbol,
            "exit_now": self.exit_now,
            "reason": self.reason.value,
            "urgency": self.urgency,
            "detail": self.detail,
            "price": self.recommended_price,
        }


class ExitEngine:
    """
    Evaluates all exit conditions for each open option position.
    Call check() on every polling cycle.
    """

    def __init__(
        self,
        target_pct: float = 1.00,        # exit at 100% gain on premium
        fixed_sl_pct: float = 0.50,      # exit at 50% loss on premium
        trailing_sl_pct: float = 0.25,   # trail stop 25% below peak
        theta_daily_limit: float = 0.03, # exit if daily theta > 3% of premium
        vol_crush_iv_drop: float = 0.30, # exit if IV drops 30% from entry
        gamma_dte_limit: int = 2,        # exit if DTE ≤ 2 (high gamma risk)
        daily_loss_limit_inr: float = 10000.0,
        eod_exit_minutes_before_close: int = 15,
    ):
        self.target_pct = target_pct
        self.fixed_sl_pct = fixed_sl_pct
        self.trailing_sl_pct = trailing_sl_pct
        self.theta_daily_limit = theta_daily_limit
        self.vol_crush_iv_drop = vol_crush_iv_drop
        self.gamma_dte_limit = gamma_dte_limit
        self.daily_loss_limit_inr = daily_loss_limit_inr
        self.eod_exit_minutes = eod_exit_minutes_before_close

    def check(
        self,
        pos: OptionPosition,
        consensus_reversed: bool = False,
        minutes_to_close: int = 999,
        daily_pnl_inr: float = 0.0,
        chain_oi_unwinding: bool = False,
    ) -> ExitSignal | None:
        """
        Evaluate all exit conditions in priority order.
        Returns ExitSignal if an exit is warranted, else None.
        """
        # Update trailing stop
        pos.update(pos.current_premium)
        self._update_trailing_sl(pos)

        # Priority 1: Emergency / limits
        if pos.is_long and pos.current_premium <= 0.05:
            return ExitSignal(pos.symbol, True, ExitReason.EMERGENCY_FLATTEN,
                              "immediate", "Premium near zero — emergency exit")

        if daily_pnl_inr <= -self.daily_loss_limit_inr:
            return ExitSignal(pos.symbol, True, ExitReason.DAILY_LOSS_LIMIT,
                              "immediate",
                              f"Daily loss ₹{daily_pnl_inr:.0f} hit limit ₹{self.daily_loss_limit_inr:.0f}")

        # Priority 2: Gamma risk (DTE too low)
        if pos.days_to_expiry <= self.gamma_dte_limit and pos.is_long:
            return ExitSignal(pos.symbol, True, ExitReason.GAMMA_RISK,
                              "immediate",
                              f"DTE={pos.days_to_expiry} ≤ {self.gamma_dte_limit} — gamma risk exit")

        # Priority 3: Time exit (near market close)
        if minutes_to_close <= self.eod_exit_minutes:
            return ExitSignal(pos.symbol, True, ExitReason.TIME_EXIT,
                              "next_tick",
                              f"EOD exit: {minutes_to_close} min to close (limit={self.eod_exit_minutes})")

        # Priority 4: Fixed stop-loss
        if pos.is_long and pos.pnl_pct <= -self.fixed_sl_pct:
            return ExitSignal(pos.symbol, True, ExitReason.FIXED_STOP,
                              "immediate",
                              f"Premium loss {pos.pnl_pct:.1%} hit SL {-self.fixed_sl_pct:.1%}")

        # Priority 5: Trailing stop
        if pos.trailing_sl > 0 and pos.current_premium < pos.trailing_sl:
            return ExitSignal(pos.symbol, True, ExitReason.TRAILING_STOP,
                              "next_tick",
                              f"Premium ₹{pos.current_premium:.2f} < trailing SL ₹{pos.trailing_sl:.2f}")

        # Priority 6: Target
        if pos.is_long and pos.pnl_pct >= self.target_pct:
            return ExitSignal(pos.symbol, True, ExitReason.TARGET_HIT,
                              "next_tick",
                              f"Premium gain {pos.pnl_pct:.1%} hit target {self.target_pct:.1%}")

        # Priority 7: Volatility crush
        if pos.entry_iv > 0 and pos.current_iv > 0 and pos.is_long:
            iv_drop = (pos.entry_iv - pos.current_iv) / pos.entry_iv
            if iv_drop >= self.vol_crush_iv_drop:
                return ExitSignal(pos.symbol, True, ExitReason.VOL_CRUSH,
                                  "next_tick",
                                  f"IV dropped {iv_drop:.0%} from entry — vol crush exit")

        # Priority 8: OI unwinding signal
        if chain_oi_unwinding and pos.pnl_pct < 0:
            return ExitSignal(pos.symbol, True, ExitReason.OI_SHIFT,
                              "next_tick",
                              "OI unwinding against position + in loss — exit")

        # Priority 9: Consensus reversal
        if consensus_reversed:
            return ExitSignal(pos.symbol, True, ExitReason.CONSENSUS_REVERSAL,
                              "next_tick",
                              "Signal direction reversed — exit on consensus flip")

        # Priority 10: Theta decay check
        if pos.days_to_expiry <= 5 and pos.entry_premium > 0:
            daily_theta_pct = 1.0 / max(1, pos.days_to_expiry)  # rough: 1/DTE of remaining
            if daily_theta_pct >= self.theta_daily_limit and pos.pnl_pct < 0:
                return ExitSignal(pos.symbol, False, ExitReason.THETA_DECAY,
                                  "eod",
                                  f"DTE={pos.days_to_expiry}: theta risk elevated, position underwater")

        return None

    def _update_trailing_sl(self, pos: OptionPosition) -> None:
        """Set or tighten trailing stop below peak premium."""
        if not pos.is_long or pos.peak_premium <= 0:
            return
        new_trail = pos.peak_premium * (1.0 - self.trailing_sl_pct)
        if new_trail > pos.trailing_sl:
            pos.trailing_sl = new_trail

    def check_all(
        self,
        positions: Sequence[OptionPosition],
        **kwargs: Any,
    ) -> list[ExitSignal]:
        """Check all positions and return list of exit signals."""
        signals = []
        for pos in positions:
            sig = self.check(pos, **kwargs)
            if sig:
                signals.append(sig)
        return signals
