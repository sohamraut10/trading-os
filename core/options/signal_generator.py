"""
Options Signal Generator — main orchestration class.
Implements BaseAgent so it slots directly into the existing ConsensusEngine.

Flow:
  MarketContext → OptionsRegimeClassifier → OptionChainAnalyzer
               → VolatilityEngine → GreeksEngine → StrategyManager
               → EntryEngine → AgentDecision (CE/PE/HOLD)

The ConsensusEngine treats this as one of the voting agents.
Its AgentDecision carries the full options context in .indicators.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from datetime import date
from typing import Any

from core.agents.base_agent import (
    AgentDecision, AgentName, BaseAgent, MarketContext, Signal,
)
from core.options.chain_analyzer import OptionChainAnalyzer
from core.options.entry_engine import EntryEngine
from core.options.expiry_engine import ExpiryEngine
from core.options.greeks_engine import GreeksEngine
from core.options.position_manager import PositionManager
from core.options.regime_classifier import OptionsRegimeClassifier
from core.options.strategy_manager import StrategyManager, StrategyType
from core.options.volatility_engine import VolatilityEngine

log = logging.getLogger(__name__)

# Index underlyings supported by this agent
_SUPPORTED_UNDERLYINGS = frozenset({
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX",
})


class OptionsAnalysisAgent(BaseAgent):
    """
    Options specialist agent.

    Analyzes the option chain, computes Greeks and IV metrics, classifies
    the options-specific regime, selects the best strategy, and evaluates
    entry confirmations.  Returns an AgentDecision with full options context
    in the indicators dict so the ConsensusEngine and Orchestrator can use it.

    Only votes on underlyings it supports (NIFTY, BANKNIFTY, etc.).
    For other assets it returns HOLD with zero confidence.
    """

    name = AgentName.OPTIONS

    def __init__(
        self,
        broker=None,                    # DhanBroker — for live chain fetching
        market_data=None,               # MarketDataProvider
        iv_history_days: int = 252,
        min_confirmations: int = 4,
        min_entry_score: float = 60.0,
        max_bid_ask_spread_pct: float = 0.05,
        india_vix_override: float = 0.0,  # 0 = fetch live; >0 = use this value
    ):
        self._broker = broker
        self._market_data = market_data
        self._chain_analyzer = OptionChainAnalyzer()
        self._greeks = GreeksEngine()
        self._vol_engine = VolatilityEngine(iv_history_days)
        self._regime_clf = OptionsRegimeClassifier()
        self._strategy_mgr = StrategyManager()
        self._entry_engine = EntryEngine(min_confirmations, min_entry_score)
        self._expiry_engine = ExpiryEngine()
        self._pos_mgr = PositionManager(greeks_engine=self._greeks)
        self._max_spread = max_bid_ask_spread_pct
        self._vix_override = india_vix_override

        # Per-asset IV history
        self._vol_engines: dict[str, VolatilityEngine] = {}

    # ── BaseAgent interface ───────────────────────────────────────────────────

    async def _analyze(self, ctx: MarketContext) -> AgentDecision:
        asset = ctx.asset.upper()

        # Only analyse supported index underlyings
        if asset not in _SUPPORTED_UNDERLYINGS:
            return AgentDecision(
                agent_name=self.name,
                signal=Signal.HOLD,
                confidence=0.0,
                reasoning=f"Options agent: {asset} is not a supported index — HOLD",
                indicators={"options_supported": False},
            )

        t0 = time.perf_counter()
        try:
            result = await self._run_pipeline(ctx, asset)
        except Exception as exc:
            log.exception("Options pipeline error for %s", asset)
            return AgentDecision(
                agent_name=self.name,
                signal=Signal.HOLD,
                confidence=0.0,
                reasoning=f"Options pipeline error: {exc}",
                indicators={"error": str(exc)},
            )

        latency = (time.perf_counter() - t0) * 1000
        log.info("Options agent %s: %s %.1f%% [%.0fms]",
                 asset, result.signal.value, result.confidence, latency)
        return result

    # ── Pipeline ──────────────────────────────────────────────────────────────

    async def _run_pipeline(self, ctx: MarketContext, asset: str) -> AgentDecision:
        # ── 1. Expiry context ─────────────────────────────────────────────────
        expiry_info = self._expiry_engine.get_info(asset)
        if expiry_info.is_expiry_day:
            return self._hold_decision(
                "Expiry day — no new options entries (gamma risk)",
                indicators={"expiry_day": True, **expiry_info.to_dict()},
            )

        # ── 2. India VIX ──────────────────────────────────────────────────────
        india_vix = self._vix_override
        if india_vix <= 0:
            india_vix = await self._fetch_vix(asset)
        iv_regime_str = VolatilityEngine.vol_regime(india_vix)

        # Safety: VIX > 30 = extreme — no new options exposure
        if india_vix >= 30.0:
            return self._hold_decision(
                f"India VIX={india_vix:.1f} in extreme territory — no new positions",
                indicators={"india_vix": india_vix, "vol_regime": "extreme"},
            )

        # ── 3. Option chain ───────────────────────────────────────────────────
        chain_summary = None
        expiry_str = expiry_info.recommended_expiry.isoformat()

        if self._broker is not None:
            chain_summary = await self._fetch_chain(asset, expiry_str, ctx.current_price)

        # ── 4. Volatility snapshot ────────────────────────────────────────────
        vol_engine = self._vol_engines.setdefault(asset, VolatilityEngine())
        atm_iv = 0.0
        straddle_premium = 0.0

        if chain_summary:
            # Get ATM IV from chain
            atm_strike_data = next(
                (s for s in chain_summary.strikes if s.strike == chain_summary.atm_strike), None
            )
            if atm_strike_data:
                atm_iv = (atm_strike_data.ce_iv + atm_strike_data.pe_iv) / 2.0 / 100.0
                straddle_premium = chain_summary.atm_straddle_premium

        # Fallback: compute IV from MarketContext.iv_rank
        if atm_iv <= 0 and ctx.iv_rank >= 0:
            atm_iv = 0.10 + (ctx.iv_rank / 100.0) * 0.30   # 10–40% range proxy

        closes = [c.close for c in ctx.candles]
        vol_snap = vol_engine.snapshot(
            current_iv=atm_iv,
            india_vix=india_vix,
            spot=ctx.current_price,
            straddle_premium=straddle_premium,
            closes=closes,
            days_to_expiry=expiry_info.days_to_weekly,
        )

        # ── 5. Regime classification ──────────────────────────────────────────
        regime = self._regime_clf.classify(
            candles=ctx.candles,
            india_vix=india_vix,
            asset=asset,
            expiry_date=expiry_str,
            iv_regime=iv_regime_str,
        )

        # ── 6. Strategy selection ─────────────────────────────────────────────
        sell_prem = VolatilityEngine.should_sell_premium(vol_snap)
        buy_prem = VolatilityEngine.should_buy_premium(vol_snap)
        best_strategy = self._strategy_mgr.best(
            primary_regime=regime.primary,
            iv_regime=iv_regime_str,
            dte=expiry_info.days_to_weekly,
            sell_premium=sell_prem,
            buy_premium=buy_prem,
        )

        if best_strategy is None:
            return self._hold_decision(
                f"No suitable strategy for regime={regime.primary}, iv={iv_regime_str}, dte={expiry_info.days_to_weekly}",
                indicators={
                    "regime": regime.to_dict(),
                    "vol_snap": vol_snap.to_dict(),
                    "expiry": expiry_info.to_dict(),
                },
            )

        # ── 7. Determine direction ────────────────────────────────────────────
        if best_strategy.is_delta_neutral:
            direction = "NEUTRAL"
            signal = Signal.HOLD   # neutral strategies need separate execution path
        elif best_strategy.is_delta_positive:
            direction = "BUY"
            signal = Signal.BUY
        else:
            direction = "SELL"
            signal = Signal.SELL

        # ── 8. Greeks for ATM strike ──────────────────────────────────────────
        atm_greeks = None
        if atm_iv > 0 and ctx.current_price > 0:
            dte_years = max(1, expiry_info.days_to_weekly) / 365.0
            atm_strike = round(ctx.current_price / 50) * 50   # round to nearest 50
            opt_type = "CE" if direction == "BUY" else "PE"
            atm_greeks = self._greeks.compute(
                S=ctx.current_price,
                K=atm_strike,
                T=dte_years,
                sigma=atm_iv,
                option_type=opt_type,
            )

        # ── 9. Entry confirmation gate ────────────────────────────────────────
        if direction != "NEUTRAL":
            entry_decision = self._entry_engine.evaluate(
                direction=direction,
                candles=ctx.candles,
                chain=chain_summary,
                vol=vol_snap,
                regime=regime,
                greeks=atm_greeks,
                consensus_confidence=float(ctx.portfolio_context.get("last_confidence", 70.0)),
                max_bid_ask_spread_pct=self._max_spread,
                news_clear=len(ctx.news_headlines) < 3,  # many headlines = news risk
            )
        else:
            # For neutral strategies, run a simplified check
            from core.options.entry_engine import EntryDecision
            entry_decision = EntryDecision(
                approved=vol_snap.iv_rank >= 50,
                score=vol_snap.iv_rank,
                reason=f"Neutral strategy: IV rank={vol_snap.iv_rank:.0f}",
                passed_names=["iv_rank"] if vol_snap.iv_rank >= 50 else [],
                failed_names=[] if vol_snap.iv_rank >= 50 else ["iv_rank"],
            )

        # ── 10. Build confidence ──────────────────────────────────────────────
        base_confidence = entry_decision.score

        # Boost for strong regime alignment
        if regime.primary in ("strong_bull", "strong_bear") and direction != "NEUTRAL":
            base_confidence = min(100.0, base_confidence + 10.0)

        # Boost for high IV rank on premium-selling strategies
        if sell_prem and best_strategy.prefer_sell_premium:
            base_confidence = min(100.0, base_confidence + 5.0)

        # Penalty for high gamma zone
        if regime.is_high_gamma:
            base_confidence *= 0.80

        confidence = round(max(0.0, min(100.0, base_confidence)), 1)

        # ── 11. Final decision ────────────────────────────────────────────────
        if not entry_decision.approved:
            # Entry gate failed — return HOLD with low confidence
            return AgentDecision(
                agent_name=self.name,
                signal=Signal.HOLD,
                confidence=min(40.0, confidence),
                reasoning=(f"Options entry REJECTED: {entry_decision.reason} | "
                            f"Strategy={best_strategy.label} | Regime={regime.primary}"),
                indicators=self._build_indicators(
                    regime, vol_snap, chain_summary, atm_greeks,
                    best_strategy, entry_decision, expiry_info,
                ),
            )

        reasoning = (
            f"Options SIGNAL: {best_strategy.label} | "
            f"Regime={regime.primary} | IV rank={vol_snap.iv_rank:.0f} | "
            f"DTE={expiry_info.days_to_weekly} | "
            f"Confirmations={entry_decision.score:.0f}% [{', '.join(entry_decision.passed_names[:4])}]"
        )

        return AgentDecision(
            agent_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            indicators=self._build_indicators(
                regime, vol_snap, chain_summary, atm_greeks,
                best_strategy, entry_decision, expiry_info,
            ),
        )

    # ── IO helpers ────────────────────────────────────────────────────────────

    async def _fetch_vix(self, asset: str) -> float:
        """Fetch India VIX from market data provider. Fallback to 20."""
        try:
            if self._market_data:
                vix_price = await self._market_data.get_current_price("INDIAVIX")
                if vix_price and vix_price > 0:
                    return float(vix_price)
        except Exception as exc:
            log.debug("VIX fetch failed: %s — using default 20", exc)
        return 20.0

    async def _fetch_chain(
        self, underlying: str, expiry: str, spot: float
    ) -> Any:
        """Fetch and parse option chain from Dhan broker."""
        try:
            from core.data.instruments import scrip_master
            inst = scrip_master.resolve(underlying)
            if not inst:
                return None
            sid = int(inst.security_id)
            exch = inst.exchange

            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                None,
                lambda: self._broker._dhan.option_chain(sid, exch, expiry),
            )
            return self._chain_analyzer.analyze(raw, underlying, expiry, spot)
        except Exception as exc:
            log.warning("Option chain fetch failed for %s: %s", underlying, exc)
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _hold_decision(reason: str, indicators: dict | None = None) -> AgentDecision:
        return AgentDecision(
            agent_name=AgentName.OPTIONS,
            signal=Signal.HOLD,
            confidence=0.0,
            reasoning=reason,
            indicators=indicators or {},
        )

    @staticmethod
    def _build_indicators(regime, vol_snap, chain, greeks, strategy, entry, expiry) -> dict:
        ind: dict[str, Any] = {
            "options_agent": True,
            "regime": regime.to_dict(),
            "volatility": vol_snap.to_dict(),
            "strategy": strategy.to_dict() if strategy else None,
            "entry": entry.to_dict() if entry else None,
            "expiry": expiry.to_dict() if expiry else None,
        }
        if chain:
            ind["chain"] = {
                "pcr_oi": round(chain.pcr_oi, 3),
                "max_pain": chain.max_pain,
                "atm_straddle": round(chain.atm_straddle_premium, 2),
                "call_resistance": chain.call_resistance_strikes[:3],
                "put_support": chain.put_support_strikes[:3],
                "iv_skew": round(chain.iv_skew, 4),
            }
        if greeks:
            ind["greeks"] = greeks.to_dict()
        return ind
