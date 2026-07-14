"""
Options Strategy Library and Selector.
Defines all supported strategies as dataclasses with their parameters,
entry conditions, risk profiles, and regime affinities.

Strategies:
  Directional: atm_call_buy, atm_put_buy, call_spread, put_spread,
               momentum_breakout, orb (opening range breakout), ema_trend,
               vwap_trend, cpr_breakout, supertrend, multi_tf_trend
  Non-directional: iron_condor, long_iron_condor, iron_fly, short_straddle,
                   long_straddle, calendar_spread, diagonal_spread
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

log = logging.getLogger(__name__)


class StrategyType(str, Enum):
    # Directional — buy premium
    ATM_CALL_BUY = "atm_call_buy"
    ATM_PUT_BUY = "atm_put_buy"
    CALL_SPREAD = "call_spread"          # bull call spread
    PUT_SPREAD = "put_spread"            # bear put spread
    MOMENTUM_BREAKOUT = "momentum_breakout"
    ORB = "orb"                          # opening range breakout
    EMA_TREND = "ema_trend"
    VWAP_TREND = "vwap_trend"
    CPR_BREAKOUT = "cpr_breakout"
    SUPERTREND = "supertrend"
    MULTI_TF_TREND = "multi_tf_trend"
    # Non-directional — spread premium
    IRON_CONDOR = "iron_condor"
    LONG_IRON_CONDOR = "long_iron_condor"
    IRON_FLY = "iron_fly"
    SHORT_STRADDLE = "short_straddle"
    LONG_STRADDLE = "long_straddle"
    CALENDAR_SPREAD = "calendar_spread"
    DIAGONAL_SPREAD = "diagonal_spread"
    # No trade sentinel
    NO_TRADE = "no_trade"


@dataclass
class StrategyLegs:
    """Describes the option legs to trade."""
    action: str           # "buy" or "sell"
    option_type: str      # "CE" or "PE"
    strike_offset: int    # strikes from ATM (0=ATM, 1=1 OTM, etc.)
    quantity: int = 1     # relative lots (1 for single, 2 for spread hedge)


@dataclass
class StrategySpec:
    """Full specification of a strategy."""
    name: StrategyType
    label: str
    legs: list[StrategyLegs]

    # Regime compatibility
    preferred_regimes: list[str]        # primary regime matches
    excluded_regimes: list[str]         # never trade in these
    preferred_iv_regime: list[str]      # "low" | "normal" | "high" | "extreme"

    # Volatility preferences
    prefer_sell_premium: bool = False   # True = benefits from IV crush
    prefer_buy_premium: bool = False    # True = benefits from IV expansion

    # Greeks profile
    is_delta_positive: bool = True      # net delta direction
    is_delta_negative: bool = False
    is_delta_neutral: bool = False
    max_dte: int = 30                   # skip if DTE > this
    min_dte: int = 1                    # skip if DTE < this

    # Risk parameters
    max_risk_pct: float = 0.05          # % of equity at risk
    target_rr: float = 2.0             # target risk:reward ratio
    use_sl_pct: float = 0.50           # exit if premium falls by this %
    use_tp_pct: float = 1.00           # exit if premium doubles (100% gain)

    # Entry conditions required
    required_confirmations: int = 3     # minimum alignment count

    def is_compatible(self, primary_regime: str, iv_regime: str, dte: int) -> bool:
        if primary_regime in self.excluded_regimes:
            return False
        if self.preferred_regimes and primary_regime not in self.preferred_regimes:
            return False
        if self.preferred_iv_regime and iv_regime not in self.preferred_iv_regime:
            return False
        if not (self.min_dte <= dte <= self.max_dte):
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "name": self.name.value,
            "label": self.label,
            "preferred_regimes": self.preferred_regimes,
            "preferred_iv_regime": self.preferred_iv_regime,
            "is_directional": not self.is_delta_neutral,
            "sell_premium": self.prefer_sell_premium,
            "target_rr": self.target_rr,
        }


# ── Strategy Registry ─────────────────────────────────────────────────────────

STRATEGY_SPECS: dict[StrategyType, StrategySpec] = {

    StrategyType.ATM_CALL_BUY: StrategySpec(
        name=StrategyType.ATM_CALL_BUY,
        label="ATM Call Buy",
        legs=[StrategyLegs("buy", "CE", 0)],
        preferred_regimes=["bull", "strong_bull", "breakout", "gap_up"],
        excluded_regimes=["expiry_day", "bear", "strong_bear"],
        preferred_iv_regime=["low", "normal"],
        prefer_buy_premium=True,
        is_delta_positive=True,
        max_dte=30, min_dte=2,
        target_rr=2.0, use_sl_pct=0.50,
    ),

    StrategyType.ATM_PUT_BUY: StrategySpec(
        name=StrategyType.ATM_PUT_BUY,
        label="ATM Put Buy",
        legs=[StrategyLegs("buy", "PE", 0)],
        preferred_regimes=["bear", "strong_bear", "breakout", "gap_down", "high_volatility"],
        excluded_regimes=["expiry_day", "bull", "strong_bull"],
        preferred_iv_regime=["low", "normal"],
        prefer_buy_premium=True,
        is_delta_negative=True,
        max_dte=30, min_dte=2,
        target_rr=2.0, use_sl_pct=0.50,
    ),

    StrategyType.CALL_SPREAD: StrategySpec(
        name=StrategyType.CALL_SPREAD,
        label="Bull Call Spread",
        legs=[StrategyLegs("buy", "CE", 0), StrategyLegs("sell", "CE", 2)],
        preferred_regimes=["bull", "strong_bull"],
        excluded_regimes=["expiry_day", "bear", "strong_bear"],
        preferred_iv_regime=["normal", "high"],
        prefer_buy_premium=False,
        is_delta_positive=True,
        max_dte=21, min_dte=3,
        target_rr=1.5, use_sl_pct=0.60,
    ),

    StrategyType.PUT_SPREAD: StrategySpec(
        name=StrategyType.PUT_SPREAD,
        label="Bear Put Spread",
        legs=[StrategyLegs("buy", "PE", 0), StrategyLegs("sell", "PE", 2)],
        preferred_regimes=["bear", "strong_bear"],
        excluded_regimes=["expiry_day", "bull", "strong_bull"],
        preferred_iv_regime=["normal", "high"],
        is_delta_negative=True,
        max_dte=21, min_dte=3,
        target_rr=1.5, use_sl_pct=0.60,
    ),

    StrategyType.MOMENTUM_BREAKOUT: StrategySpec(
        name=StrategyType.MOMENTUM_BREAKOUT,
        label="Momentum Breakout",
        legs=[StrategyLegs("buy", "CE", 1)],   # slightly OTM call
        preferred_regimes=["breakout", "bull"],
        excluded_regimes=["expiry_day", "sideways", "low_volatility"],
        preferred_iv_regime=["low", "normal"],
        prefer_buy_premium=True,
        is_delta_positive=True,
        max_dte=14, min_dte=2,
        target_rr=3.0, use_sl_pct=0.40,
        required_confirmations=4,
    ),

    StrategyType.ORB: StrategySpec(
        name=StrategyType.ORB,
        label="Opening Range Breakout",
        legs=[StrategyLegs("buy", "CE", 1)],
        preferred_regimes=["bull", "breakout", "gap_up"],
        excluded_regimes=["expiry_day", "high_volatility"],
        preferred_iv_regime=["normal"],
        is_delta_positive=True,
        max_dte=7, min_dte=1,
        target_rr=2.0, use_sl_pct=0.50,
        required_confirmations=4,
    ),

    StrategyType.EMA_TREND: StrategySpec(
        name=StrategyType.EMA_TREND,
        label="EMA Trend Following",
        legs=[StrategyLegs("buy", "CE", 0)],
        preferred_regimes=["bull", "strong_bull", "bear", "strong_bear"],
        excluded_regimes=["expiry_day", "sideways"],
        preferred_iv_regime=["low", "normal"],
        is_delta_positive=True,
        max_dte=14, min_dte=2,
        target_rr=2.5, use_sl_pct=0.45,
    ),

    StrategyType.IRON_CONDOR: StrategySpec(
        name=StrategyType.IRON_CONDOR,
        label="Short Iron Condor",
        legs=[
            StrategyLegs("sell", "CE", 2), StrategyLegs("buy", "CE", 4),
            StrategyLegs("sell", "PE", 2), StrategyLegs("buy", "PE", 4),
        ],
        preferred_regimes=["sideways", "mean_reversion", "low_volatility"],
        excluded_regimes=["expiry_day", "breakout", "event_day", "high_volatility", "extreme"],
        preferred_iv_regime=["high", "normal"],
        prefer_sell_premium=True,
        is_delta_neutral=True,
        max_dte=21, min_dte=5,
        target_rr=0.5, use_sl_pct=2.0,
        required_confirmations=3,
    ),

    StrategyType.LONG_IRON_CONDOR: StrategySpec(
        name=StrategyType.LONG_IRON_CONDOR,
        label="Long Iron Condor",
        legs=[
            StrategyLegs("buy", "CE", 2), StrategyLegs("sell", "CE", 4),
            StrategyLegs("buy", "PE", 2), StrategyLegs("sell", "PE", 4),
        ],
        preferred_regimes=["breakout", "high_volatility"],
        excluded_regimes=["expiry_day", "sideways", "low_volatility"],
        preferred_iv_regime=["low"],
        prefer_buy_premium=True,
        is_delta_neutral=True,
        max_dte=21, min_dte=5,
        target_rr=1.5,
    ),

    StrategyType.IRON_FLY: StrategySpec(
        name=StrategyType.IRON_FLY,
        label="Iron Fly",
        legs=[
            StrategyLegs("sell", "CE", 0), StrategyLegs("buy", "CE", 3),
            StrategyLegs("sell", "PE", 0), StrategyLegs("buy", "PE", 3),
        ],
        preferred_regimes=["sideways", "mean_reversion"],
        excluded_regimes=["expiry_day", "breakout", "event_day"],
        preferred_iv_regime=["high", "normal"],
        prefer_sell_premium=True,
        is_delta_neutral=True,
        max_dte=14, min_dte=3,
        target_rr=0.7,
    ),

    StrategyType.SHORT_STRADDLE: StrategySpec(
        name=StrategyType.SHORT_STRADDLE,
        label="Short Straddle",
        legs=[StrategyLegs("sell", "CE", 0), StrategyLegs("sell", "PE", 0)],
        preferred_regimes=["sideways", "low_volatility"],
        excluded_regimes=["expiry_day", "breakout", "event_day", "high_volatility", "extreme"],
        preferred_iv_regime=["high"],
        prefer_sell_premium=True,
        is_delta_neutral=True,
        max_dte=14, min_dte=3,
        target_rr=0.4,
        required_confirmations=4,
    ),

    StrategyType.LONG_STRADDLE: StrategySpec(
        name=StrategyType.LONG_STRADDLE,
        label="Long Straddle",
        legs=[StrategyLegs("buy", "CE", 0), StrategyLegs("buy", "PE", 0)],
        preferred_regimes=["high_volatility", "event_day", "breakout"],
        excluded_regimes=["expiry_day", "sideways", "low_volatility"],
        preferred_iv_regime=["low"],
        prefer_buy_premium=True,
        is_delta_neutral=True,
        max_dte=21, min_dte=5,
        target_rr=1.5,
    ),

    StrategyType.CALENDAR_SPREAD: StrategySpec(
        name=StrategyType.CALENDAR_SPREAD,
        label="Calendar Spread",
        legs=[StrategyLegs("sell", "CE", 0), StrategyLegs("buy", "CE", 0)],
        preferred_regimes=["sideways", "low_volatility"],
        excluded_regimes=["expiry_day", "event_day"],
        preferred_iv_regime=["normal"],
        is_delta_neutral=True,
        max_dte=30, min_dte=10,
        target_rr=1.2,
    ),

    StrategyType.DIAGONAL_SPREAD: StrategySpec(
        name=StrategyType.DIAGONAL_SPREAD,
        label="Diagonal Spread",
        legs=[StrategyLegs("buy", "CE", 0), StrategyLegs("sell", "CE", 1)],
        preferred_regimes=["bull", "sideways"],
        excluded_regimes=["expiry_day", "event_day"],
        preferred_iv_regime=["normal"],
        is_delta_positive=True,
        max_dte=30, min_dte=10,
        target_rr=1.5,
    ),
}


class StrategyManager:
    """
    Selects the best strategy for given market conditions.
    Scores strategies by regime fit, IV regime fit, and DTE compatibility.
    """

    def select(
        self,
        primary_regime: str,
        iv_regime: str,
        dte: int,
        sell_premium: bool = False,
        buy_premium: bool = False,
        exclude_types: Sequence[StrategyType] | None = None,
    ) -> list[StrategySpec]:
        """
        Return ranked list of compatible strategies for the current regime.
        Strategies are scored and sorted by fit score descending.
        """
        excluded = set(exclude_types or [])
        candidates = []

        for stype, spec in STRATEGY_SPECS.items():
            if stype in excluded or stype == StrategyType.NO_TRADE:
                continue
            if not spec.is_compatible(primary_regime, iv_regime, dte):
                continue
            score = self._score(spec, primary_regime, iv_regime, sell_premium, buy_premium)
            candidates.append((score, spec))

        candidates.sort(key=lambda x: x[0], reverse=True)
        result = [s for _, s in candidates]

        if not result:
            log.info("No compatible strategy for regime=%s iv=%s dte=%d — NO TRADE",
                     primary_regime, iv_regime, dte)
        else:
            log.debug("Top strategy: %s (score=%.2f) for regime=%s iv=%s dte=%d",
                      candidates[0][1].name.value, candidates[0][0], primary_regime, iv_regime, dte)

        return result

    def best(
        self,
        primary_regime: str,
        iv_regime: str,
        dte: int,
        sell_premium: bool = False,
        buy_premium: bool = False,
    ) -> StrategySpec | None:
        ranked = self.select(primary_regime, iv_regime, dte, sell_premium, buy_premium)
        return ranked[0] if ranked else None

    @staticmethod
    def _score(
        spec: StrategySpec,
        primary_regime: str,
        iv_regime: str,
        sell_premium: bool,
        buy_premium: bool,
    ) -> float:
        score = 0.0
        if primary_regime in spec.preferred_regimes:
            score += 3.0
        if iv_regime in spec.preferred_iv_regime:
            score += 2.0
        if sell_premium and spec.prefer_sell_premium:
            score += 2.0
        if buy_premium and spec.prefer_buy_premium:
            score += 2.0
        # Penalize naked short premium — higher bar required
        if spec.prefer_sell_premium and not (sell_premium):
            score -= 1.0
        score += spec.target_rr * 0.5
        return score
