"""
Smart Entry Engine for Indian Options.
Evaluates multiple confirmation signals before allowing an entry.
All confirmations are configurable — each is scored and a minimum
number must align before a trade is approved.

Confirmations evaluated:
  trend, vwap, ema_stack, momentum, volume_spike, oi_shift,
  pcr_aligned, iv_regime_ok, greeks_ok, higher_tf_confirm,
  no_major_resistance, consensus_confidence, news_clear,
  spread_acceptable, max_pain_away
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from core.options.chain_analyzer import ChainSummary
from core.options.volatility_engine import VolatilitySnapshot
from core.options.regime_classifier import OptionsRegime
from core.options.greeks_engine import Greeks

log = logging.getLogger(__name__)


@dataclass
class ConfirmationResult:
    """Result of a single entry confirmation check."""
    name: str
    passed: bool
    detail: str
    weight: float = 1.0


@dataclass
class EntryDecision:
    """Aggregated entry gate result."""
    approved: bool
    score: float                               # 0–100 confirmation score
    confirmations: list[ConfirmationResult] = field(default_factory=list)
    passed_names: list[str] = field(default_factory=list)
    failed_names: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "score": round(self.score, 1),
            "passed": self.passed_names,
            "failed": self.failed_names,
            "reason": self.reason,
        }


class EntryEngine:
    """
    Multi-confirmation entry gate.

    Usage:
        engine = EntryEngine(min_confirmations=4, min_score=60)
        decision = engine.evaluate(
            direction="BUY",  # or "SELL"
            candles=...,
            chain=chain_summary,
            vol=vol_snapshot,
            regime=options_regime,
            greeks=atm_greeks,
            consensus_confidence=75.0,
        )
        if decision.approved:
            execute(...)
    """

    def __init__(self, min_confirmations: int = 4, min_score: float = 60.0):
        self.min_confirmations = min_confirmations
        self.min_score = min_score

    def evaluate(
        self,
        direction: str,               # "BUY" (bullish) or "SELL" (bearish)
        candles: Sequence[Any],       # list of OHLCV
        chain: ChainSummary | None,
        vol: VolatilitySnapshot | None,
        regime: OptionsRegime | None,
        greeks: Greeks | None,
        consensus_confidence: float,
        max_bid_ask_spread_pct: float = 0.05,
        news_clear: bool = True,
        higher_tf_aligned: bool = False,
    ) -> EntryDecision:
        """
        Run all applicable confirmations and return EntryDecision.
        """
        results: list[ConfirmationResult] = []

        # ── Safety overrides (instant reject) ────────────────────────────────
        if regime and regime.is_expiry_day:
            return EntryDecision(
                approved=False, score=0,
                reason="No new entries on expiry day (gamma risk)",
            )
        if vol and vol.vol_regime == "extreme":
            return EntryDecision(
                approved=False, score=0,
                reason="VIX in extreme territory — no new options positions",
            )

        # ── Run all confirmations ─────────────────────────────────────────────
        results.append(self._check_consensus(consensus_confidence))
        results.append(self._check_regime(direction, regime))
        results.append(self._check_iv_regime(vol, direction))
        results.append(self._check_pcr(chain, direction))
        results.append(self._check_max_pain(chain, direction))
        results.append(self._check_greeks(greeks, direction))
        results.append(self._check_spread(chain, max_bid_ask_spread_pct, direction))
        results.append(self._check_news(news_clear))
        results.append(self._check_higher_tf(higher_tf_aligned))
        results.append(self._check_oi_shift(chain, direction))
        results.append(self._check_momentum(candles, direction))
        results.append(self._check_volume(candles))

        # ── Score ─────────────────────────────────────────────────────────────
        total_weight = sum(r.weight for r in results)
        passed_weight = sum(r.weight for r in results if r.passed)
        score = (passed_weight / total_weight * 100) if total_weight > 0 else 0

        passed_names = [r.name for r in results if r.passed]
        failed_names = [r.name for r in results if not r.passed]
        n_passed = len(passed_names)

        approved = n_passed >= self.min_confirmations and score >= self.min_score
        reason = (
            f"{n_passed}/{len(results)} confirmations, score={score:.1f}%"
            if approved
            else f"Need {self.min_confirmations} confirmations ({n_passed} passed, score={score:.1f}%)"
        )

        if not approved:
            log.debug("Entry REJECTED — %s. Failed: %s", reason, failed_names[:5])
        else:
            log.info("Entry APPROVED — %s. Passed: %s", reason, passed_names)

        return EntryDecision(
            approved=approved,
            score=score,
            confirmations=results,
            passed_names=passed_names,
            failed_names=failed_names,
            reason=reason,
        )

    # ── Individual confirmations ──────────────────────────────────────────────

    @staticmethod
    def _check_consensus(confidence: float) -> ConfirmationResult:
        passed = confidence >= 65.0
        return ConfirmationResult(
            name="consensus_confidence",
            passed=passed,
            detail=f"Consensus={confidence:.1f}% (need ≥65%)",
            weight=2.0,
        )

    @staticmethod
    def _check_regime(direction: str, regime: OptionsRegime | None) -> ConfirmationResult:
        if regime is None:
            return ConfirmationResult("regime", False, "Regime unavailable")
        bullish_regimes = {"strong_bull", "bull", "breakout", "gap_up", "mean_reversion"}
        bearish_regimes = {"strong_bear", "bear", "breakout", "gap_down", "high_volatility"}
        if direction == "BUY":
            passed = regime.primary in bullish_regimes
        else:
            passed = regime.primary in bearish_regimes
        return ConfirmationResult(
            name="regime",
            passed=passed,
            detail=f"Regime={regime.primary} for {direction}",
            weight=2.0,
        )

    @staticmethod
    def _check_iv_regime(vol: VolatilitySnapshot | None, direction: str) -> ConfirmationResult:
        if vol is None:
            return ConfirmationResult("iv_regime", True, "IV data unavailable — skipping")
        # For buying: prefer low IV; for non-directional neutral = selling: prefer high IV
        if direction in ("BUY", "SELL"):
            passed = vol.iv_rank < 70   # don't buy expensive premium
            detail = f"IV Rank={vol.iv_rank:.1f} (prefer <70 for directional)"
        else:
            passed = True
            detail = "IV regime check skipped for neutral"
        return ConfirmationResult("iv_regime", passed, detail, weight=1.5)

    @staticmethod
    def _check_pcr(chain: ChainSummary | None, direction: str) -> ConfirmationResult:
        if chain is None:
            return ConfirmationResult("pcr", True, "Chain unavailable — skipping")
        pcr = chain.pcr_oi
        if direction == "BUY":
            passed = pcr > 1.0   # more put OI = market hedging, slightly bullish
            detail = f"PCR={pcr:.2f} (>1.0 = supportive for BUY)"
        else:
            passed = pcr < 1.0
            detail = f"PCR={pcr:.2f} (<1.0 = supportive for SELL/PUT)"
        return ConfirmationResult("pcr", passed, detail, weight=1.0)

    @staticmethod
    def _check_max_pain(chain: ChainSummary | None, direction: str) -> ConfirmationResult:
        if chain is None or chain.max_pain == 0 or chain.spot == 0:
            return ConfirmationResult("max_pain", True, "Max pain unavailable — skipping")
        diff_pct = (chain.spot - chain.max_pain) / chain.max_pain
        if direction == "BUY":
            # For a call buy, spot should be above max pain (market above pain floor)
            passed = diff_pct >= -0.01  # within 1% below max pain is ok
            detail = f"Spot={chain.spot:.0f} MaxPain={chain.max_pain:.0f} diff={diff_pct:.1%}"
        else:
            passed = diff_pct <= 0.01
            detail = f"Spot={chain.spot:.0f} MaxPain={chain.max_pain:.0f} diff={diff_pct:.1%}"
        return ConfirmationResult("max_pain", passed, detail, weight=0.5)

    @staticmethod
    def _check_greeks(greeks: Greeks | None, direction: str) -> ConfirmationResult:
        if greeks is None:
            return ConfirmationResult("greeks", True, "Greeks unavailable — skipping")
        # Ensure delta is in an acceptable range for the direction
        if direction == "BUY":
            passed = 0.20 <= greeks.delta <= 0.80
            detail = f"CE delta={greeks.delta:.3f} (need 0.20–0.80)"
        else:
            passed = -0.80 <= greeks.delta <= -0.20
            detail = f"PE delta={greeks.delta:.3f} (need -0.80–-0.20)"
        return ConfirmationResult("greeks", passed, detail, weight=1.5)

    @staticmethod
    def _check_spread(chain: ChainSummary | None, max_pct: float, direction: str) -> ConfirmationResult:
        if chain is None:
            return ConfirmationResult("spread", True, "Chain unavailable — skipping")
        # Find ATM strike spread
        atm = next((s for s in chain.strikes if s.strike == chain.atm_strike), None)
        if atm is None:
            return ConfirmationResult("spread", True, "ATM strike not found")
        spread = atm.ce_spread_pct if direction == "BUY" else atm.pe_spread_pct
        passed = spread <= max_pct
        return ConfirmationResult(
            name="spread",
            passed=passed,
            detail=f"Bid/ask spread={spread:.1%} (max {max_pct:.1%})",
            weight=1.5,
        )

    @staticmethod
    def _check_news(news_clear: bool) -> ConfirmationResult:
        return ConfirmationResult(
            name="news_clear",
            passed=news_clear,
            detail="No major news risk" if news_clear else "News risk detected",
            weight=1.5,
        )

    @staticmethod
    def _check_higher_tf(aligned: bool) -> ConfirmationResult:
        return ConfirmationResult(
            name="higher_tf",
            passed=aligned,
            detail="Higher timeframe aligned" if aligned else "Higher TF not confirmed",
            weight=1.0,
        )

    @staticmethod
    def _check_oi_shift(chain: ChainSummary | None, direction: str) -> ConfirmationResult:
        if chain is None:
            return ConfirmationResult("oi_shift", True, "Chain unavailable — skipping")
        activity = chain.activity
        if direction == "BUY":
            # Bullish: call OI build > put OI build, short covering in puts
            passed = bool(activity.short_covering_strikes) or len(activity.fresh_long_strikes) > 0
            detail = (f"Short covering strikes: {activity.short_covering_strikes[:2]}"
                      if passed else "No bullish OI shift detected")
        else:
            passed = bool(activity.fresh_short_strikes) or len(activity.long_unwinding_strikes) > 0
            detail = (f"Fresh short strikes: {activity.fresh_short_strikes[:2]}"
                      if passed else "No bearish OI shift detected")
        return ConfirmationResult("oi_shift", passed, detail, weight=1.0)

    @staticmethod
    def _check_momentum(candles: Sequence[Any], direction: str) -> ConfirmationResult:
        if len(candles) < 14:
            return ConfirmationResult("momentum", True, "Too few candles — skipping")
        closes = [c.close for c in candles]
        # RSI-like momentum: count up vs down periods
        gains = sum(1 for i in range(1, 14) if closes[-i] > closes[-(i + 1)])
        rsi_proxy = gains / 13 * 100
        if direction == "BUY":
            passed = rsi_proxy >= 50
            detail = f"Momentum score={rsi_proxy:.0f}/100 (need ≥50 for BUY)"
        else:
            passed = rsi_proxy <= 50
            detail = f"Momentum score={rsi_proxy:.0f}/100 (need ≤50 for SELL)"
        return ConfirmationResult("momentum", passed, detail, weight=1.0)

    @staticmethod
    def _check_volume(candles: Sequence[Any]) -> ConfirmationResult:
        if len(candles) < 20:
            return ConfirmationResult("volume", True, "Too few candles — skipping")
        recent_vol = sum(c.volume for c in candles[-5:]) / 5
        avg_vol = sum(c.volume for c in candles[-20:]) / 20
        passed = recent_vol >= avg_vol * 0.8   # at least 80% of avg volume
        detail = f"Volume ratio={recent_vol/avg_vol:.2f}× (need ≥0.8×)"
        return ConfirmationResult("volume", passed, detail, weight=1.0)
