"""
Meta Agent — Consensus Engine
Aggregates all agent decisions into a final TRUE/FALSE signal with full explainability.
This is the last gate before a trade goes to the execution engine.
"""
from dataclasses import dataclass, field
from typing import Any
import time
import uuid

from .base_agent import AgentDecision, AgentName, Signal
from config.settings import settings


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
                "position_size_pct": round(self.suggested_position_size_pct, 3),
                "stop_loss_pct": round(self.suggested_stop_loss_pct, 3),
                "take_profit_pct": round(self.suggested_take_profit_pct, 3),
                "risk_reward": round(self.risk_reward, 2),
            },
            "warnings": self.warnings,
            "conflict_notes": self.conflict_notes,
            "override_reason": self.override_reason,
        }


class ConsensusEngine:
    """
    Implements weighted voting, conflict resolution, and override logic.

    Voting agents: Technical, Sentiment, Quant, OrderFlow (4 agents)
    Audit agent:   DevilsAdvocate (can veto but doesn't vote on direction)
    """

    def __init__(self):
        self.cfg = settings.consensus
        self.risk_cfg = settings.risk
        self.weights = settings.agent_weights.normalize()

        # Maps AgentName → weight in consensus vote
        self._weight_map = {
            AgentName.TECHNICAL: self.weights["technical"],
            AgentName.SENTIMENT: self.weights["sentiment"],
            AgentName.QUANT: self.weights["quant"],
            AgentName.ORDER_FLOW: self.weights["order_flow"],
        }

    def evaluate(
        self,
        asset: str,
        request_id: str,
        regime: str,
        decisions: list[AgentDecision],
        da_decision: AgentDecision | None = None,
    ) -> TradeSignal:

        voting_decisions = [d for d in decisions if d.agent_name in self._weight_map]
        da = da_decision

        agents_dict = [d.to_dict() for d in decisions]
        if da:
            agents_dict.append(da.to_dict())

        # ── Step 1: Devil's Advocate veto check ─────────────────────────────
        if da and da.signal == Signal.SELL and da.confidence >= self.cfg.devils_advocate_veto_threshold:
            return TradeSignal(
                request_id=request_id,
                asset=asset,
                timestamp=time.time(),
                final_decision=False,
                action=None,
                confidence=0.0,
                agents=agents_dict,
                reason=f"Trade blocked by Devil's Advocate: {da.reasoning}",
                regime=regime,
                override_reason=f"DA veto (confidence={da.confidence:.0f})",
                warnings=da.warnings,
            )

        # ── Step 2: Filter low-confidence agents ────────────────────────────
        eligible = [d for d in voting_decisions if d.confidence >= self.cfg.min_agent_confidence]
        excluded = [d for d in voting_decisions if d.confidence < self.cfg.min_agent_confidence]
        conflict_notes = [f"{d.agent_name.value} excluded (confidence {d.confidence:.0f} < threshold)" for d in excluded]

        if len(eligible) < 2:
            return self._reject(asset, request_id, regime, agents_dict,
                                "Too few agents meet minimum confidence threshold", conflict_notes)

        # ── Step 3: Compute weighted directional vote ────────────────────────
        buy_weight = 0.0
        sell_weight = 0.0
        hold_weight = 0.0
        total_weight = 0.0
        weighted_confidence = 0.0

        for d in eligible:
            w = self._weight_map.get(d.agent_name, 0.0)
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

        # Normalize
        buy_frac = buy_weight / total_weight
        sell_frac = sell_weight / total_weight
        avg_confidence = weighted_confidence / total_weight

        # Count agent agreement (not just weight)
        buy_count = sum(1 for d in eligible if d.signal == Signal.BUY)
        sell_count = sum(1 for d in eligible if d.signal == Signal.SELL)
        hold_count = sum(1 for d in eligible if d.signal == Signal.HOLD)

        # ── Step 4: Conflict resolution ─────────────────────────────────────
        if buy_count > 0 and sell_count > 0:
            conflict_notes.append(f"Directional conflict: {buy_count} BUY vs {sell_count} SELL agents")

        # ── Step 5: Determine leading direction ─────────────────────────────
        if buy_frac > sell_frac and buy_frac > hold_frac if (hold_frac := hold_weight / total_weight) else buy_frac > sell_frac:
            leading = Signal.BUY
            leading_count = buy_count
            leading_weight = buy_frac
        elif sell_frac > buy_frac:
            leading = Signal.SELL
            leading_count = sell_count
            leading_weight = sell_frac
        else:
            return self._reject(asset, request_id, regime, agents_dict,
                                "No dominant direction — weighted vote tied", conflict_notes)

        # ── Step 6: Apply consensus thresholds ──────────────────────────────
        passes_count = leading_count >= self.cfg.min_agents_agree
        passes_confidence = avg_confidence >= self.cfg.min_avg_confidence

        # Regime adjustments
        regime_multiplier = self._regime_multiplier(regime)
        adjusted_confidence = avg_confidence * regime_multiplier

        if not passes_count:
            return self._reject(
                asset, request_id, regime, agents_dict,
                f"Insufficient agreement: {leading_count}/{len(eligible)} agents aligned (need {self.cfg.min_agents_agree})",
                conflict_notes,
            )

        if not passes_confidence:
            return self._reject(
                asset, request_id, regime, agents_dict,
                f"Low avg confidence {avg_confidence:.1f} < threshold {self.cfg.min_avg_confidence}",
                conflict_notes,
            )

        # ── Step 7: DA caution (not veto) ────────────────────────────────────
        da_warnings = da.warnings if da else []
        if da and da.signal == Signal.SELL:  # below veto threshold
            conflict_notes.append(f"DA caution: {da.reasoning} (confidence={da.confidence:.0f})")
            adjusted_confidence *= 0.92  # slight penalty

        # ── Step 8: Compute risk parameters ──────────────────────────────────
        atr_pct = self._get_atr_pct(decisions)
        stop_loss, take_profit, position_size = self._compute_risk(
            atr_pct, adjusted_confidence, regime
        )

        # ── Build reason string ───────────────────────────────────────────────
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

    def _regime_multiplier(self, regime: str) -> float:
        """Confidence discount by regime — higher uncertainty regimes are penalized."""
        return {
            "bull": 1.0,
            "bear": 0.95,
            "sideways": 0.90,
            "volatile": 0.80,
            "unknown": 0.85,
        }.get(regime, 0.85)

    def _get_atr_pct(self, decisions: list[AgentDecision]) -> float:
        """Extract ATR% from Technical agent indicators if available."""
        for d in decisions:
            if d.agent_name == AgentName.TECHNICAL and "atr_14" in d.indicators:
                # ATR in price units — need price, but use 2% as default
                return 0.02
        return 0.02

    def _compute_risk(
        self, atr_pct: float, confidence: float, regime: str
    ) -> tuple[float, float, float]:
        """
        Returns (stop_loss_pct, take_profit_pct, position_size_pct).
        Higher confidence → slightly larger position size.
        """
        sl_pct = max(atr_pct * 1.5, self.risk_cfg.max_trade_drawdown)
        tp_pct = sl_pct * self.risk_cfg.default_rr_ratio

        # Confidence-scaled position sizing (Kelly-inspired, conservative)
        kelly_fraction = (confidence / 100 - 0.5) * 2  # [0,1]
        base_size = self.risk_cfg.max_position_pct
        position_size = base_size * kelly_fraction * 0.5  # half-Kelly
        position_size = max(0.005, min(position_size, base_size))  # clamp

        # Volatile regime: halve the size
        if regime == "volatile":
            position_size *= 0.5

        return sl_pct, tp_pct, position_size
