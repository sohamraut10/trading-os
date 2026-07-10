"""
Devil's Advocate Agent
Actively tries to find reasons NOT to take the trade.
Acts as systemic risk auditor — flags tail risks others miss.
"""
import numpy as np
from dataclasses import dataclass
from .base_agent import BaseAgent, AgentDecision, AgentName, MarketContext, Signal, OHLCV


@dataclass
class RiskFlags:
    is_earnings_week: bool = False
    macro_headwind: bool = False
    overextended_move: bool = False         # price moved >3% in last candle
    low_liquidity_session: bool = False
    correlated_market_crash: bool = False
    consecutive_loss_streak: bool = False   # from portfolio context
    near_major_event: bool = False
    spread_too_wide: bool = False
    gap_risk: bool = False                  # overnight/weekend gap
    regime_mismatch: bool = False           # strategy type vs current regime


def _detect_overextension(candles: list[OHLCV]) -> bool:
    if len(candles) < 3:
        return False
    recent_move_pct = abs(candles[-1].close - candles[-3].close) / candles[-3].close * 100
    return recent_move_pct > 3.0


def _detect_gap_risk(candles: list[OHLCV]) -> bool:
    if len(candles) < 2:
        return False
    gap_pct = abs(candles[-1].open - candles[-2].close) / candles[-2].close * 100
    return gap_pct > 1.5


def _check_spread(ob) -> bool:
    if not ob or not ob.bids or not ob.asks:
        return False
    best_bid = ob.bids[0].price if ob.bids else 0
    best_ask = ob.asks[0].price if ob.asks else 0
    if best_ask == 0:
        return False
    spread_pct = (best_ask - best_bid) / best_ask * 100
    return spread_pct > 0.1  # >10bps spread = wide for liquid assets


def _macro_headwind(macro_ctx: dict) -> bool:
    """Flags if macro context contains known risk factors."""
    if not macro_ctx:
        return False
    risk_keywords = {"recession", "hawkish", "rate_hike", "inflation_spike", "geopolitical"}
    ctx_str = str(macro_ctx).lower()
    return any(kw in ctx_str for kw in risk_keywords)


def _near_earnings(macro_ctx: dict) -> bool:
    return macro_ctx.get("days_to_earnings", 30) <= 2


def _correlated_crash(macro_ctx: dict) -> bool:
    """Check if correlated markets are in distress."""
    vix = macro_ctx.get("vix", 20)
    sp500_change = macro_ctx.get("sp500_1d_change_pct", 0)
    return vix > 30 or sp500_change < -2.0


def _regime_mismatch(regime: str, portfolio_ctx: dict) -> bool:
    """Scalping in a trending regime, swing in volatile — flag mismatches."""
    strategy = portfolio_ctx.get("active_strategy", "swing")
    mismatches = {
        ("volatile", "scalping"): True,
        ("sideways", "trend_follow"): True,
    }
    return mismatches.get((regime, strategy), False)


def _loss_streak(portfolio_ctx: dict) -> bool:
    return portfolio_ctx.get("consecutive_losses", 0) >= 3


def gather_risk_flags(ctx: MarketContext) -> RiskFlags:
    return RiskFlags(
        is_earnings_week=_near_earnings(ctx.macro_context),
        macro_headwind=_macro_headwind(ctx.macro_context),
        overextended_move=_detect_overextension(ctx.candles),
        correlated_market_crash=_correlated_crash(ctx.macro_context),
        consecutive_loss_streak=_loss_streak(ctx.portfolio_context),
        near_major_event=ctx.macro_context.get("near_fed_event", False),
        spread_too_wide=_check_spread(ctx.order_book),
        gap_risk=_detect_gap_risk(ctx.candles),
        regime_mismatch=_regime_mismatch(ctx.regime, ctx.portfolio_context),
    )


def _score_risk(flags: RiskFlags) -> tuple[Signal, float, list[str]]:
    """
    DA agent outputs SELL as 'reject this trade' or HOLD as 'proceed with caution'.
    High confidence SELL = strong veto recommendation to Meta Agent.
    """
    rejection_pts = 0.0
    caution_pts = 0.0
    reasons = []

    critical_flags = {
        "correlated_market_crash": (flags.correlated_market_crash, 35, "CRITICAL: Correlated market crash — systemic risk"),
        "consecutive_loss_streak": (flags.consecutive_loss_streak, 25, "3+ consecutive losses — possible strategy breakdown"),
        "is_earnings_week": (flags.is_earnings_week, 20, "Earnings within 2 days — binary event risk"),
        "near_major_event": (flags.near_major_event, 20, "Major macro event imminent (FOMC/NFP)"),
    }

    warning_flags = {
        "macro_headwind": (flags.macro_headwind, 15, "Macro headwinds present"),
        "overextended_move": (flags.overextended_move, 15, "Price overextended >3% in 3 candles — mean reversion risk"),
        "spread_too_wide": (flags.spread_too_wide, 10, "Spread >10bps — execution cost degrades edge"),
        "gap_risk": (flags.gap_risk, 10, "Gap detected — fill price uncertainty"),
        "regime_mismatch": (flags.regime_mismatch, 15, "Strategy type mismatches current market regime"),
    }

    for key, (triggered, pts, msg) in critical_flags.items():
        if triggered:
            rejection_pts += pts
            reasons.append(msg)

    for key, (triggered, pts, msg) in warning_flags.items():
        if triggered:
            caution_pts += pts
            reasons.append(f"WARNING: {msg}")

    total_pts = rejection_pts + caution_pts

    if rejection_pts >= 35:
        # Strong veto
        confidence = min(55 + rejection_pts, 95)
        return Signal.SELL, round(confidence, 1), reasons
    elif total_pts >= 25:
        confidence = min(55 + total_pts * 0.7, 85)
        return Signal.SELL, round(confidence, 1), reasons
    elif total_pts > 0:
        return Signal.HOLD, round(50 + total_pts * 0.5, 1), reasons
    else:
        return Signal.HOLD, 30.0, ["No significant risk flags — trade environment acceptable"]


class DevilsAdvocateAgent(BaseAgent):
    """
    DA Agent: assumes trades should be rejected until proven safe.
    Returns SELL = 'reject this trade', not a short signal.
    Meta Agent treats DA SELL with high confidence as a veto.
    """
    name = AgentName.DEVILS_ADVOCATE

    async def _analyze(self, ctx: MarketContext) -> AgentDecision:
        flags = gather_risk_flags(ctx)
        signal, confidence, reasons = _score_risk(flags)

        active_flags = {k: v for k, v in flags.__dict__.items() if v is True}

        return AgentDecision(
            agent_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=" | ".join(reasons) if reasons else "No risk flags triggered",
            indicators={
                "active_risk_flags": list(active_flags.keys()),
                "flag_count": len(active_flags),
            },
            warnings=list(active_flags.keys()),
        )
