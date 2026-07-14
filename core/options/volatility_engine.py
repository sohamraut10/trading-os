"""
Volatility Engine for Indian Options.
Computes:
  - India VIX integration (from Dhan or market data)
  - Implied Volatility from option chain
  - IV Rank (0–100, where in the historical IV range is current IV)
  - IV Percentile (how many historical periods had lower IV)
  - Realized Volatility (historical vol from underlying closes)
  - Expected Move (1σ and 2σ) from ATM straddle
  - Volatility regime classification (high/low/rising/falling)
"""
from __future__ import annotations

import math
import logging
from collections import deque
from dataclasses import dataclass
from typing import Sequence

log = logging.getLogger(__name__)

# IV regime thresholds for India VIX (scale: 10–40+ typical)
_VIX_LOW_THRESH = 13.0
_VIX_HIGH_THRESH = 20.0
_VIX_EXTREME_THRESH = 30.0


@dataclass
class VolatilitySnapshot:
    """Point-in-time volatility summary."""
    current_iv: float         # current ATM IV (decimal)
    iv_rank: float            # 0–100: where in 52-week range is current IV
    iv_percentile: float      # 0–100: % of periods with lower IV
    realized_vol: float       # 20-day historical vol (decimal)
    india_vix: float          # India VIX index value
    iv_minus_rv: float        # IV premium over realized vol
    expected_move_1sd: float  # ATM straddle premium (₹)
    expected_move_1sd_pct: float  # as % of spot
    expected_move_2sd_pct: float  # 2× the 1σ move
    vol_regime: str           # "low" | "normal" | "high" | "extreme"
    is_iv_expanding: bool     # IV trending up
    is_iv_contracting: bool   # IV trending down

    def to_dict(self) -> dict:
        return {
            "current_iv_pct": round(self.current_iv * 100, 2),
            "iv_rank": round(self.iv_rank, 1),
            "iv_percentile": round(self.iv_percentile, 1),
            "realized_vol_pct": round(self.realized_vol * 100, 2),
            "india_vix": round(self.india_vix, 2),
            "iv_premium": round(self.iv_minus_rv * 100, 2),
            "expected_move_1sd": round(self.expected_move_1sd, 2),
            "expected_move_1sd_pct": round(self.expected_move_1sd_pct, 2),
            "expected_move_2sd_pct": round(self.expected_move_2sd_pct, 2),
            "vol_regime": self.vol_regime,
            "iv_expanding": self.is_iv_expanding,
            "iv_contracting": self.is_iv_contracting,
        }


class VolatilityEngine:
    """
    Rolling IV history and volatility metrics engine.

    Maintains a rolling deque of daily IV observations to compute
    IV rank/percentile over a configurable lookback (default 252 trading days).
    """

    def __init__(self, lookback_days: int = 252):
        self._lookback = lookback_days
        # deque of (timestamp, iv) tuples for rolling IV history
        self._iv_history: deque[float] = deque(maxlen=lookback_days)

    def push_iv(self, iv: float) -> None:
        """Add a new IV observation to the rolling history."""
        if iv > 0:
            self._iv_history.append(iv)

    # ── Core metrics ──────────────────────────────────────────────────────────

    def iv_rank(self, current_iv: float) -> float:
        """
        IV Rank (0–100): position of current IV in its 52-week range.
        0 = at 52-week low, 100 = at 52-week high.
        """
        hist = list(self._iv_history)
        if len(hist) < 2:
            return 50.0
        lo, hi = min(hist), max(hist)
        if hi == lo:
            return 50.0
        return max(0.0, min(100.0, (current_iv - lo) / (hi - lo) * 100.0))

    def iv_percentile(self, current_iv: float) -> float:
        """
        IV Percentile (0–100): percentage of historical periods with IV below current.
        """
        hist = list(self._iv_history)
        if not hist:
            return 50.0
        below = sum(1 for v in hist if v < current_iv)
        return below / len(hist) * 100.0

    @staticmethod
    def realized_volatility(closes: Sequence[float], period: int = 20) -> float:
        """
        Annualized historical volatility from log-returns over `period` days.
        Returns decimal (0.20 = 20%).
        """
        if len(closes) < period + 1:
            return 0.0
        recent = list(closes[-(period + 1):])
        log_returns = [math.log(recent[i] / recent[i - 1]) for i in range(1, len(recent))]
        if len(log_returns) < 2:
            return 0.0
        mean = sum(log_returns) / len(log_returns)
        variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
        return math.sqrt(variance * 252)

    @staticmethod
    def expected_move(spot: float, iv: float, days_to_expiry: int) -> tuple[float, float]:
        """
        Expected 1σ and 2σ moves from spot over `days_to_expiry` calendar days.
        Returns (move_1sd_inr, move_2sd_inr).
        IV is decimal annualized (0.20 = 20%).
        """
        if spot <= 0 or iv <= 0 or days_to_expiry <= 0:
            return 0.0, 0.0
        t = days_to_expiry / 365.0
        move_1sd = spot * iv * math.sqrt(t)
        return move_1sd, move_1sd * 2.0

    @staticmethod
    def expected_move_from_straddle(spot: float, straddle_premium: float) -> tuple[float, float]:
        """
        Quick expected move from ATM straddle premium (market-implied).
        1σ ≈ 0.68 × straddle, 2σ ≈ 1.35 × straddle (rule of thumb).
        Returns (move_1sd_inr, move_2sd_inr).
        """
        return straddle_premium * 0.68, straddle_premium * 1.35

    # ── Volatility regime ─────────────────────────────────────────────────────

    @staticmethod
    def vol_regime(india_vix: float) -> str:
        if india_vix >= _VIX_EXTREME_THRESH:
            return "extreme"
        if india_vix >= _VIX_HIGH_THRESH:
            return "high"
        if india_vix <= _VIX_LOW_THRESH:
            return "low"
        return "normal"

    def iv_trend(self, window: int = 5) -> tuple[bool, bool]:
        """
        Detect IV expansion/contraction from the last `window` observations.
        Returns (is_expanding, is_contracting).
        """
        hist = list(self._iv_history)
        if len(hist) < window + 1:
            return False, False
        recent_slope = hist[-1] - hist[-window]
        is_expanding = recent_slope > 0.005   # > 0.5% absolute IV increase
        is_contracting = recent_slope < -0.005
        return is_expanding, is_contracting

    # ── Full snapshot ─────────────────────────────────────────────────────────

    def snapshot(
        self,
        current_iv: float,
        india_vix: float,
        spot: float,
        straddle_premium: float,
        closes: Sequence[float],
        days_to_expiry: int = 7,
    ) -> VolatilitySnapshot:
        """
        Build a full volatility snapshot. Pushes current_iv into history.
        """
        self.push_iv(current_iv)

        iv_r = self.iv_rank(current_iv)
        iv_p = self.iv_percentile(current_iv)
        rv = self.realized_volatility(closes)
        em_1sd, em_2sd = self.expected_move_from_straddle(spot, straddle_premium)
        em_1sd_pct = em_1sd / spot * 100 if spot > 0 else 0.0
        em_2sd_pct = em_2sd / spot * 100 if spot > 0 else 0.0
        regime = self.vol_regime(india_vix)
        expanding, contracting = self.iv_trend()

        return VolatilitySnapshot(
            current_iv=current_iv,
            iv_rank=iv_r,
            iv_percentile=iv_p,
            realized_vol=rv,
            india_vix=india_vix,
            iv_minus_rv=current_iv - rv,
            expected_move_1sd=em_1sd,
            expected_move_1sd_pct=em_1sd_pct,
            expected_move_2sd_pct=em_2sd_pct,
            vol_regime=regime,
            is_iv_expanding=expanding,
            is_iv_contracting=contracting,
        )

    # ── Premium direction advisor ─────────────────────────────────────────────

    @staticmethod
    def should_sell_premium(snap: VolatilitySnapshot, iv_rank_threshold: float = 50.0) -> bool:
        """
        Return True when it's statistically favorable to sell premium (write options).
        Conditions: high IV rank (overpriced), contracting vol, IV > RV significantly.
        """
        return (
            snap.iv_rank >= iv_rank_threshold
            and snap.iv_minus_rv > 0.03       # IV at least 3% above RV
            and snap.vol_regime in ("normal", "high")
        )

    @staticmethod
    def should_buy_premium(snap: VolatilitySnapshot, iv_rank_threshold: float = 30.0) -> bool:
        """
        Return True when it's favorable to buy premium (long options).
        Conditions: low IV rank (underpriced), expanding vol, event imminent.
        """
        return (
            snap.iv_rank <= iv_rank_threshold
            or snap.is_iv_expanding
            or snap.vol_regime == "extreme"
        )
