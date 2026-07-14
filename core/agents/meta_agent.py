from dataclasses import dataclass, field
from typing import Any, Optional, Dict, List
import time
import uuid
import logging
from .base_agent import AgentDecision, AgentName, Signal
from config.settings import settings

logger = logging.getLogger("trading_os.meta_agent")


@dataclass
class TradeSignal:
    """Final output of the consensus engine."""
    request_id: str
    asset: str
    timestamp: float

    final_decision: bool              # True = execute, False = reject
    action: Signal | None             # BUY / SELL / None if rejected
    confidence: float                 # weighted aggregate confidence

    agents: list[dict]                # individual agent decisions
    reason: str                       # human-readable explanation
    regime: str

    # Risk parameters computed by meta agent
    suggested_position_size_pct: float = 0.0
    suggested_stop_loss_pct: float = 0.0
    suggested_take_profit_pct: float = 0.0
    risk_reward: float = 0.0
    warnings: list[str] = field(default_factory=list)

    # Explainability
    conflict_notes: list[str] = field(default_factory=list)
    override_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "asset": self.asset,
            "timestamp": self.timestamp,
            "final_decision": "TRUE SIGNAL" if self.final_decision else "FALSE SIGNAL",
            "action": self.action.value if self.action else None,
            "confidence": round(self.confidence, 1),
            "agents": self.agents,
            "reason": self.reason,
            "regime": self.regime,
            "risk": {
                "position_size_pct": round(self.suggested_position_size_pct, 4),
                "stop_loss_pct": round(self.suggested_stop_loss_pct, 4),
                "take_profit_pct": round(self.suggested_take_profit_pct, 4),
                "risk_reward": round(self.risk_reward, 2),
            },
            "warnings": self.warnings,
            "conflict_notes": self.conflict_notes,
            "override_reason": self.override_reason,
        }


class ConsensusEngine:
    """
    Implements weighted voting, debate execution, and meta-agent consensus rules.
    """

    def __init__(self):
        self.cfg = settings.consensus
        self.risk_cfg = settings.risk
        self.weights = settings.agent_weights.normalize()

        self._weight_map = {
            AgentName.TECHNICAL: self.weights["technical"],
            AgentName.SENTIMENT: self.weights["sentiment"],
            AgentName.QUANT: self.weights["quant"],
            AgentName.ORDER_FLOW: self.weights["order_flow"],
            AgentName.OPTIONS: self.weights["options"],
        }

    async def evaluate(
        self,
        asset: str,
        request_id: str,
        regime: str,
        decisions: list[AgentDecision],
        da_decision: AgentDecision | None = None,
        hypothesis: Optional[Any] = None,
        event_bus: Optional[Any] = None,
        dynamic_weights: Optional[Dict[str, float]] = None,
    ) -> TradeSignal:
        
        if dynamic_weights:
            for k, v in dynamic_weights.items():
                try:
                    self._weight_map[AgentName(k)] = v
                except ValueError:
                    pass

        # Save screening confidences to calculate delta later
        screening_confidences = {d.agent_name: d.confidence for d in decisions}

        voting_decisions = [d for d in decisions if d.agent_name in self._weight_map]
        
        # ── Step 1: Trigger Debate or Skip ──────────────────────────────────
        buy_count = sum(1 for d in voting_decisions if d.signal == Signal.BUY)
        sell_count = sum(1 for d in voting_decisions if d.signal == Signal.SELL)
        avg_confidence = sum(d.confidence for d in voting_decisions) / len(voting_decisions) if voting_decisions else 50.0

        da_flag_count = 0
        da_veto = False
        if da_decision:
            da_flag_count = da_decision.indicators.get("flag_count", 0)
            da_veto = (da_decision.signal == Signal.SELL and da_decision.confidence >= self.cfg.devils_advocate_veto_threshold)

        hypothesis_conflict_count = 0
        if hypothesis:
            hyp_bias = hypothesis.direction_bias
            for d in voting_decisions:
                if d.signal != hyp_bias:
                    hypothesis_conflict_count += 1

        trigger_reasons = []
        if buy_count == 2 and sell_count == 2:
            trigger_reasons.append("Vote split 2-2 after screening")
        if 60.0 <= avg_confidence <= 68.0:
            trigger_reasons.append(f"Borderline average confidence: {avg_confidence:.1f}%")
        if da_flag_count >= 2 and not da_veto:
            trigger_reasons.append(f"Devil's Advocate raised {da_flag_count} flags below veto threshold")
        if hypothesis_conflict_count >= 2:
            trigger_reasons.append(f"Strategy hypothesis ({hyp_bias.value}) conflicts with {hypothesis_conflict_count} agent decisions")

        if trigger_reasons:
            if event_bus:
                await event_bus.publish("DebateTriggered", request_id, {"reasons": trigger_reasons})
                
            # Round 1: Opening (ArgumentPosted)
            arguments = []
            for d in voting_decisions:
                arg = {
                    "agent_name": d.agent_name.value,
                    "stance": d.signal.value,
                    "confidence": d.confidence,
                    "evidence": self._get_agent_evidence(d)
                }
                arguments.append(arg)
                if event_bus:
                    await event_bus.publish("ArgumentPosted", request_id, arg)

            # Round 2: Rebuttal (RebuttalPosted)
            from core.agents.debate_rules import adjust_confidence
            rebutted_arguments = []
            for d in voting_decisions:
                other_args = [a for a in arguments if a["agent_name"] != d.agent_name.value]
                new_conf, delta = adjust_confidence(d.agent_name.value, d.signal.value, d.confidence, other_args)
                d.confidence = new_conf
                rebutted_arg = {
                    "agent_name": d.agent_name.value,
                    "stance": d.signal.value,
                    "confidence": new_conf,
                    "confidence_delta": delta
                }
                rebutted_arguments.append(rebutted_arg)
                if event_bus:
                    await event_bus.publish("RebuttalPosted", request_id, rebutted_arg)

            # Round 3: Cross-examination (CrossExam)
            from core.agents.debate_rules import apply_da_challenge
            da_flags = da_decision.indicators.get("active_risk_flags", []) if da_decision else []
            
            post_rebuttal_stances = [d.signal for d in voting_decisions]
            buy_votes = post_rebuttal_stances.count(Signal.BUY)
            sell_votes = post_rebuttal_stances.count(Signal.SELL)
            majority_stance = None
            if buy_votes > sell_votes:
                majority_stance = Signal.BUY
            elif sell_votes > buy_votes:
                majority_stance = Signal.SELL

            cross_exam_challenged = []
            if majority_stance and da_flags:
                for d in voting_decisions:
                    if d.signal == majority_stance:
                        new_conf, delta = apply_da_challenge(d.agent_name.value, d.signal.value, d.confidence, da_flags)
                        d.confidence = new_conf
                        cross_exam_challenged.append({
                            "agent_name": d.agent_name.value,
                            "stance": d.signal.value,
                            "confidence": new_conf,
                            "confidence_delta": delta
                        })
            
            if event_bus:
                await event_bus.publish("CrossExam", request_id, {
                    "da_flags": da_flags,
                    "challenged_agents": cross_exam_challenged
                })
        else:
            if event_bus:
                await event_bus.publish("DebateSkipped", request_id, {"reason": "Clear consensus established"})

        # Record per-agent confidence delta
        agents_dict = []
        for d in decisions:
            d_dict = d.to_dict()
            if d.agent_name in screening_confidences:
                d_dict["confidence_delta"] = round(d.confidence - screening_confidences[d.agent_name], 1)
            else:
                d_dict["confidence_delta"] = 0.0
            agents_dict.append(d_dict)

        # ── Step 2: Devil's Advocate Veto Check ─────────────────────────────
        if da_decision and da_veto:
            if event_bus:
                await event_bus.publish("VetoRaised", request_id, {"reason": da_decision.reasoning, "confidence": da_decision.confidence})
            return TradeSignal(
                request_id=request_id,
                asset=asset,
                timestamp=time.time(),
                final_decision=False,
                action=None,
                confidence=0.0,
                agents=agents_dict,
                reason=f"Trade blocked by Devil's Advocate: {da_decision.reasoning}",
                regime=regime,
                override_reason=f"DA veto (confidence={da_decision.confidence:.0f})",
                warnings=da_decision.warnings,
            )

        # ── Step 3: Exclude low confidence voting agents (< 55%) ───────────
        eligible = [d for d in voting_decisions if d.confidence >= self.cfg.min_agent_confidence]
        excluded = [d for d in voting_decisions if d.confidence < self.cfg.min_agent_confidence]
        conflict_notes = [f"{d.agent_name.value} excluded (confidence {d.confidence:.0f} < threshold)" for d in excluded]

        if len(eligible) < 2:
            return self._reject(asset, request_id, regime, agents_dict,
                                "Too few agents meet minimum confidence threshold", conflict_notes)

        # ── Step 4: Weighted voting with regime multiplier ─────────────────
        buy_weight = 0.0
        sell_weight = 0.0
        hold_weight = 0.0
        total_weight = 0.0
        weighted_confidence = 0.0

        for d in eligible:
            base_w = self._weight_map.get(d.agent_name, 0.0)
            mult = self._get_regime_weight_multiplier(regime, d.agent_name)
            w = base_w * mult
            
            total_weight += w
            weighted_confidence += w * d.confidence
            if d.signal == Signal.BUY:
                buy_weight += w
            elif d.signal == Signal.SELL:
                sell_weight += w
            else:
                hold_weight += w

        if total_weight == 0:
            return self._reject(asset, request_id, regime, agents_dict, "Zero total weight", conflict_notes)

        # Normalize fractions
        buy_frac = buy_weight / total_weight
        sell_frac = sell_weight / total_weight
        hold_frac = hold_weight / total_weight
        avg_confidence = weighted_confidence / total_weight

        buy_count = sum(1 for d in eligible if d.signal == Signal.BUY)
        sell_count = sum(1 for d in eligible if d.signal == Signal.SELL)

        if buy_count > 0 and sell_count > 0:
            conflict_notes.append(f"Directional conflict: {buy_count} BUY vs {sell_count} SELL agents")

        # Determine leading direction
        if buy_frac > sell_frac and buy_frac > hold_frac:
            leading = Signal.BUY
            leading_count = buy_count
        elif sell_frac > buy_frac and sell_frac > hold_frac:
            leading = Signal.SELL
            leading_count = sell_count
        else:
            return self._reject(asset, request_id, regime, agents_dict,
                                "No dominant direction — weighted vote tied", conflict_notes)

        # Consensus check: requires >= 3/4 remaining agents in agreement AND average confidence >= 68%
        agreement_ratio = leading_count / len(eligible)
        passes_count = agreement_ratio >= 0.75
        passes_confidence = avg_confidence >= self.cfg.min_avg_confidence

        regime_multiplier = self._regime_multiplier(regime)
        adjusted_confidence = avg_confidence * regime_multiplier

        if not passes_count:
            return self._reject(
                asset, request_id, regime, agents_dict,
                f"Insufficient agreement: {leading_count}/{len(eligible)} agents aligned (need >= 75%)",
                conflict_notes,
            )

        if not passes_confidence:
            return self._reject(
                asset, request_id, regime, agents_dict,
                f"Low avg confidence {avg_confidence:.1f} < threshold {self.cfg.min_avg_confidence}",
                conflict_notes,
            )

        # Apply DA caution if DA is opposite to leading
        da_warnings = da_decision.warnings if da_decision else []
        if da_decision and da_decision.signal == Signal.SELL and leading == Signal.BUY:
            conflict_notes.append(f"DA caution: {da_decision.reasoning} (confidence={da_decision.confidence:.0f})")
            adjusted_confidence *= 0.92  # slight penalty

        # Sizing and coordinates
        atr_pct = self._get_atr_pct(decisions)
        stop_loss, take_profit, position_size = self._compute_risk(
            atr_pct, adjusted_confidence, regime
        )

        agent_summary = ", ".join(
            f"{d.agent_name.value}={d.signal.value}({d.confidence:.0f})" for d in eligible
        )
        reason = (
            f"{leading_count}/{len(eligible)} voting agents {leading.value} | "
            f"weighted confidence={adjusted_confidence:.1f} | "
            f"regime={regime} | agents: [{agent_summary}]"
        )

        all_warnings = list(set(
            sum((d.warnings for d in decisions), []) + da_warnings
        ))

        return TradeSignal(
            request_id=request_id,
            asset=asset,
            timestamp=time.time(),
            final_decision=True,
            action=leading,
            confidence=round(adjusted_confidence, 1),
            agents=agents_dict,
            reason=reason,
            regime=regime,
            suggested_position_size_pct=round(position_size, 4),
            suggested_stop_loss_pct=round(stop_loss, 4),
            suggested_take_profit_pct=round(take_profit, 4),
            risk_reward=round(take_profit / stop_loss, 2) if stop_loss > 0 else 0.0,
            warnings=all_warnings,
            conflict_notes=conflict_notes,
        )

    def _reject(
        self, asset: str, request_id: str, regime: str,
        agents: list[dict], reason: str, conflict_notes: list[str]
    ) -> TradeSignal:
        return TradeSignal(
            request_id=request_id,
            asset=asset,
            timestamp=time.time(),
            final_decision=False,
            action=None,
            confidence=0.0,
            agents=agents,
            reason=reason,
            regime=regime,
            conflict_notes=conflict_notes,
        )

    def _get_regime_weight_multiplier(self, regime: str, agent_name: AgentName) -> float:
        regime = regime.lower()
        if regime == "volatile":
            if agent_name == AgentName.TECHNICAL: return 0.6
            if agent_name == AgentName.QUANT: return 1.2
        elif regime == "sideways" or regime == "ranging":
            if agent_name == AgentName.QUANT: return 1.3
            if agent_name == AgentName.TECHNICAL: return 0.8
        elif regime == "bull" or regime == "bear":
            if agent_name == AgentName.TECHNICAL: return 1.2
            if agent_name == AgentName.QUANT: return 1.0
        return 1.0

    def _regime_multiplier(self, regime: str) -> float:
        return {
            "bull": 1.0,
            "bear": 0.95,
            "sideways": 0.90,
            "volatile": 0.80,
            "unknown": 0.85,
        }.get(regime, 0.85)

    def _get_atr_pct(self, decisions: list[AgentDecision]) -> float:
        for d in decisions:
            if d.agent_name == AgentName.TECHNICAL and "atr_14" in d.indicators:
                return 0.02
        return 0.02

    def _compute_risk(
        self, atr_pct: float, confidence: float, regime: str
    ) -> tuple[float, float, float]:
        sl_pct = max(atr_pct * 1.5, self.risk_cfg.max_trade_drawdown)
        tp_pct = sl_pct * self.risk_cfg.default_rr_ratio

        # Position sizing.
        # Equity: half-Kelly on notional (5% base) — conservative because losses
        # are uncapped without a stop (leverage risk).
        # Options: full-Kelly on premium (25% base) — safe because premium paid IS
        # the max loss; no further leverage risk beyond what you allocate.
        kelly_fraction = (confidence / 100 - 0.5) * 2  # [0,1]
        is_options = settings.trade_mode == "options"
        base_size = (
            settings.options_max_position_pct if is_options else self.risk_cfg.max_position_pct
        )
        kelly_damper = 1.0 if is_options else 0.5          # full-Kelly for options
        position_size = base_size * kelly_fraction * kelly_damper
        position_size = max(0.005, min(position_size, base_size))

        if regime == "volatile":
            position_size *= 0.5

        return sl_pct, tp_pct, position_size

    def _get_agent_evidence(self, d: AgentDecision) -> list[dict]:
        evidence = []
        if d.agent_name == AgentName.TECHNICAL:
            evidence.append({"metric": "rsi_14", "value": d.indicators.get("rsi_14")})
            evidence.append({"metric": "macd_hist", "value": d.indicators.get("macd_hist")})
        elif d.agent_name == AgentName.QUANT:
            evidence.append({"metric": "hurst_exponent", "value": d.indicators.get("hurst_exponent")})
            evidence.append({"metric": "z_score", "value": d.indicators.get("z_score")})
        elif d.agent_name == AgentName.SENTIMENT:
            evidence.append({"metric": "headline_count", "value": d.indicators.get("headline_count")})
        elif d.agent_name == AgentName.ORDER_FLOW:
            has_void = "liquidity_void" in d.warnings
            evidence.append({"metric": "liquidity_void", "value": has_void})
            evidence.append({"metric": "poc", "value": d.indicators.get("poc")})
        return evidence
