"""
Option Chain Analyzer for NSE/BSE F&O.
Consumes the Dhan option chain API response and extracts:
  - Maximum Pain strike
  - Put-Call Ratio (volume and OI)
  - OI activity classification (fresh longs, shorts, unwinding, covering)
  - Support / Resistance levels derived from peak OI
  - Strike-by-strike analytics for display and decision-making
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class StrikeData:
    strike: float
    # CE leg
    ce_ltp: float = 0.0
    ce_oi: int = 0
    ce_oi_change: int = 0
    ce_volume: int = 0
    ce_iv: float = 0.0
    ce_bid: float = 0.0
    ce_ask: float = 0.0
    ce_security_id: str = ""
    ce_lot_size: int = 0
    # PE leg
    pe_ltp: float = 0.0
    pe_oi: int = 0
    pe_oi_change: int = 0
    pe_volume: int = 0
    pe_iv: float = 0.0
    pe_bid: float = 0.0
    pe_ask: float = 0.0
    pe_security_id: str = ""
    pe_lot_size: int = 0

    @property
    def ce_spread_pct(self) -> float:
        mid = (self.ce_bid + self.ce_ask) / 2
        return (self.ce_ask - self.ce_bid) / mid if mid > 0 else 999.0

    @property
    def pe_spread_pct(self) -> float:
        mid = (self.pe_bid + self.pe_ask) / 2
        return (self.pe_ask - self.pe_bid) / mid if mid > 0 else 999.0

    @property
    def straddle_premium(self) -> float:
        return self.ce_ltp + self.pe_ltp


@dataclass
class OIActivity:
    """OI activity classification relative to previous session."""
    fresh_long_strikes: list[float] = field(default_factory=list)
    fresh_short_strikes: list[float] = field(default_factory=list)
    long_unwinding_strikes: list[float] = field(default_factory=list)
    short_covering_strikes: list[float] = field(default_factory=list)


@dataclass
class ChainSummary:
    """Full option chain analysis output."""
    underlying: str
    spot: float
    atm_strike: float
    expiry: str

    # Core metrics
    max_pain: float = 0.0
    pcr_oi: float = 0.0        # put OI / call OI
    pcr_volume: float = 0.0    # put volume / call volume
    iv_skew: float = 0.0       # put IV - call IV at ATM ± 1 strike

    # Support/Resistance from OI
    call_resistance_strikes: list[float] = field(default_factory=list)  # high call OI
    put_support_strikes: list[float] = field(default_factory=list)      # high put OI

    # OI activity
    activity: OIActivity = field(default_factory=OIActivity)

    # All strikes
    strikes: list[StrikeData] = field(default_factory=list)

    # Totals
    total_ce_oi: int = 0
    total_pe_oi: int = 0
    total_ce_volume: int = 0
    total_pe_volume: int = 0

    # ATM straddle premium = expected move proxy
    atm_straddle_premium: float = 0.0

    def expected_move_1sd(self) -> float:
        """Expected 1 std-dev move from ATM straddle premium."""
        return self.atm_straddle_premium

    def is_bullish_chain(self) -> bool:
        return self.pcr_oi > 1.2

    def is_bearish_chain(self) -> bool:
        return self.pcr_oi < 0.8

    def to_dict(self) -> dict:
        return {
            "underlying": self.underlying,
            "spot": self.spot,
            "atm_strike": self.atm_strike,
            "expiry": self.expiry,
            "max_pain": self.max_pain,
            "pcr_oi": round(self.pcr_oi, 3),
            "pcr_volume": round(self.pcr_volume, 3),
            "iv_skew": round(self.iv_skew, 4),
            "call_resistance": self.call_resistance_strikes[:3],
            "put_support": self.put_support_strikes[:3],
            "atm_straddle_premium": round(self.atm_straddle_premium, 2),
            "expected_move_1sd": round(self.expected_move_1sd(), 2),
            "total_ce_oi": self.total_ce_oi,
            "total_pe_oi": self.total_pe_oi,
            "fresh_long_strikes": self.activity.fresh_long_strikes[:5],
            "fresh_short_strikes": self.activity.fresh_short_strikes[:5],
        }


# ── Analyzer ──────────────────────────────────────────────────────────────────

class OptionChainAnalyzer:
    """
    Parses the Dhan option chain response and produces ChainSummary.

    Dhan option chain format (from broker._dhan.option_chain()):
    {
      "data": {
        "data": {
          "last_price": <spot>,
          "oc": {
            "<strike>": {
              "ce": { "last_price", "oi", "volume", "iv", "security_id",
                      "bid_price", "ask_price", "lot_size", "change_in_oi" },
              "pe": { ... same keys ... }
            }
          }
        }
      }
    }
    """

    # Top-N strikes by OI to count as support/resistance
    SR_TOP_N: int = 3
    # OI change threshold (relative to avg) to classify as fresh/unwinding
    OI_ACTIVITY_THRESHOLD: float = 0.30

    def analyze(
        self,
        raw_chain: dict[str, Any],
        underlying: str,
        expiry: str,
        spot_override: float = 0.0,
    ) -> ChainSummary:
        """
        Parse a raw Dhan option_chain() response and return ChainSummary.
        Falls back gracefully if keys are missing.
        """
        data = (raw_chain.get("data") or {}).get("data") or {}
        oc: dict[str, Any] = data.get("oc") or {}
        spot = float(data.get("last_price") or spot_override or 0.0)

        if not oc or spot <= 0:
            log.warning("Empty option chain or missing spot for %s", underlying)
            return ChainSummary(underlying=underlying, spot=spot,
                                atm_strike=0, expiry=expiry)

        # ── Parse all strikes ─────────────────────────────────────────────────
        strikes_data: list[StrikeData] = []
        for k_str, legs in oc.items():
            try:
                strike = float(k_str)
            except ValueError:
                continue
            ce = legs.get("ce") or {}
            pe = legs.get("pe") or {}
            sd = StrikeData(
                strike=strike,
                ce_ltp=float(ce.get("last_price") or 0),
                ce_oi=int(ce.get("oi") or 0),
                ce_oi_change=int(ce.get("change_in_oi") or 0),
                ce_volume=int(ce.get("volume") or 0),
                ce_iv=float(ce.get("iv") or 0),
                ce_bid=float(ce.get("bid_price") or 0),
                ce_ask=float(ce.get("ask_price") or 0),
                ce_security_id=str(ce.get("security_id") or ""),
                ce_lot_size=int(ce.get("lot_size") or 0),
                pe_ltp=float(pe.get("last_price") or 0),
                pe_oi=int(pe.get("oi") or 0),
                pe_oi_change=int(pe.get("change_in_oi") or 0),
                pe_volume=int(pe.get("volume") or 0),
                pe_iv=float(pe.get("iv") or 0),
                pe_bid=float(pe.get("bid_price") or 0),
                pe_ask=float(pe.get("ask_price") or 0),
                pe_security_id=str(pe.get("security_id") or ""),
                pe_lot_size=int(pe.get("lot_size") or 0),
            )
            strikes_data.append(sd)

        strikes_data.sort(key=lambda s: s.strike)

        # ── ATM strike ────────────────────────────────────────────────────────
        atm = min(strikes_data, key=lambda s: abs(s.strike - spot), default=None)
        atm_strike = atm.strike if atm else 0.0

        summary = ChainSummary(
            underlying=underlying,
            spot=spot,
            atm_strike=atm_strike,
            expiry=expiry,
            strikes=strikes_data,
        )

        # ── Aggregates ────────────────────────────────────────────────────────
        summary.total_ce_oi = sum(s.ce_oi for s in strikes_data)
        summary.total_pe_oi = sum(s.pe_oi for s in strikes_data)
        summary.total_ce_volume = sum(s.ce_volume for s in strikes_data)
        summary.total_pe_volume = sum(s.pe_volume for s in strikes_data)

        if summary.total_ce_oi > 0:
            summary.pcr_oi = summary.total_pe_oi / summary.total_ce_oi
        if summary.total_ce_volume > 0:
            summary.pcr_volume = summary.total_pe_volume / summary.total_ce_volume

        # ── Max Pain ──────────────────────────────────────────────────────────
        summary.max_pain = self._max_pain(strikes_data)

        # ── Support / Resistance ──────────────────────────────────────────────
        # Resistance: strikes with highest call OI above spot
        ce_above = [(s.strike, s.ce_oi) for s in strikes_data if s.strike >= spot and s.ce_oi > 0]
        ce_above.sort(key=lambda x: x[1], reverse=True)
        summary.call_resistance_strikes = [k for k, _ in ce_above[:self.SR_TOP_N]]

        # Support: strikes with highest put OI below spot
        pe_below = [(s.strike, s.pe_oi) for s in strikes_data if s.strike <= spot and s.pe_oi > 0]
        pe_below.sort(key=lambda x: x[1], reverse=True)
        summary.put_support_strikes = [k for k, _ in pe_below[:self.SR_TOP_N]]

        # ── OI Activity ──────────────────────────────────────────────────────
        summary.activity = self._oi_activity(strikes_data)

        # ── ATM straddle premium ──────────────────────────────────────────────
        if atm:
            summary.atm_straddle_premium = atm.ce_ltp + atm.pe_ltp

        # ── IV skew (±1 strike from ATM) ─────────────────────────────────────
        summary.iv_skew = self._iv_skew(strikes_data, atm_strike)

        return summary

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _max_pain(self, strikes: list[StrikeData]) -> float:
        """
        Maximum pain = strike price where total option writers' pain is minimized
        (= where total intrinsic value of all options is minimized at expiry).
        """
        all_strikes = [s.strike for s in strikes]
        if not all_strikes:
            return 0.0

        min_pain = float("inf")
        max_pain_strike = all_strikes[0]

        for exp_price in all_strikes:
            pain = 0.0
            for s in strikes:
                # Call writers pay: max(0, exp_price - strike) × CE OI
                pain += max(0.0, exp_price - s.strike) * s.ce_oi
                # Put writers pay: max(0, strike - exp_price) × PE OI
                pain += max(0.0, s.strike - exp_price) * s.pe_oi
            if pain < min_pain:
                min_pain = pain
                max_pain_strike = exp_price

        return max_pain_strike

    def _oi_activity(self, strikes: list[StrikeData]) -> OIActivity:
        """
        Classify OI changes into fresh longs, fresh shorts, unwinding, covering.
        Uses price-move + OI-change cross-tabulation (simplified without previous close).

        When change_in_oi > threshold (positive): fresh OI built
        When change_in_oi < -threshold (negative): OI unwound/covered

        For CE: positive OI change + rising price = fresh long; flat/down price = fresh short
        For PE: positive OI change + rising price = short covering; down price = fresh long
        We approximate direction from spread position relative to ATM.
        """
        activity = OIActivity()
        if not strikes:
            return activity

        avg_ce_oi = max(1, sum(s.ce_oi for s in strikes) // len(strikes))
        avg_pe_oi = max(1, sum(s.pe_oi for s in strikes) // len(strikes))
        thresh = self.OI_ACTIVITY_THRESHOLD

        for s in strikes:
            # Call OI activity
            if s.ce_oi > 0:
                ce_change_ratio = s.ce_oi_change / avg_ce_oi
                if ce_change_ratio > thresh:
                    # significant build-up in CE OI
                    activity.fresh_short_strikes.append(s.strike)   # sellers writing calls
                elif ce_change_ratio < -thresh:
                    activity.long_unwinding_strikes.append(s.strike)

            # Put OI activity
            if s.pe_oi > 0:
                pe_change_ratio = s.pe_oi_change / avg_pe_oi
                if pe_change_ratio > thresh:
                    activity.fresh_short_strikes.append(s.strike)   # sellers writing puts
                elif pe_change_ratio < -thresh:
                    activity.short_covering_strikes.append(s.strike)

        # De-duplicate
        for attr in ("fresh_long_strikes", "fresh_short_strikes",
                     "long_unwinding_strikes", "short_covering_strikes"):
            setattr(activity, attr, sorted(set(getattr(activity, attr))))

        return activity

    def _iv_skew(self, strikes: list[StrikeData], atm_strike: float) -> float:
        """
        IV skew = put IV - call IV (positive = put bid up = fear/downside hedge demand).
        Computed as average across ±2 strikes around ATM.
        """
        near = [s for s in strikes if abs(s.strike - atm_strike) <= 2 * 50]
        if not near:
            return 0.0
        ce_ivs = [s.ce_iv for s in near if s.ce_iv > 0]
        pe_ivs = [s.pe_iv for s in near if s.pe_iv > 0]
        if not ce_ivs or not pe_ivs:
            return 0.0
        return (sum(pe_ivs) / len(pe_ivs)) - (sum(ce_ivs) / len(ce_ivs))

    def find_liquid_strikes(
        self,
        summary: ChainSummary,
        option_type: str,
        max_spread_pct: float = 0.05,
        min_oi: int = 500,
        n_otm: int = 2,
    ) -> list[StrikeData]:
        """
        Return the top liquid strikes for CE or PE, within ±n_otm strikes of ATM,
        filtered by minimum OI and maximum bid/ask spread.
        """
        candidates = []
        for s in summary.strikes:
            if option_type == "CE":
                if s.strike < summary.atm_strike:
                    continue
                liq = s.ce_oi >= min_oi and s.ce_spread_pct <= max_spread_pct and s.ce_ltp > 0
                if liq:
                    candidates.append(s)
            else:
                if s.strike > summary.atm_strike:
                    continue
                liq = s.pe_oi >= min_oi and s.pe_spread_pct <= max_spread_pct and s.pe_ltp > 0
                if liq:
                    candidates.append(s)

        # Sort by OI descending (most liquid first), keep near ATM
        if option_type == "CE":
            candidates.sort(key=lambda x: x.ce_oi, reverse=True)
        else:
            candidates.sort(key=lambda x: x.pe_oi, reverse=True)
        return candidates[:n_otm + 2]
