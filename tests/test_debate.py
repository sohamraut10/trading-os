import pytest
import time
from core.agents.meta_agent import ConsensusEngine, AgentDecision, AgentName, Signal
from core.agents.debate_rules import adjust_confidence, apply_da_challenge
from core.strategy.selector import TradeHypothesis


def create_decisions(stances: dict, confidences: dict) -> list[AgentDecision]:
    decisions = []
    for name_str, stance in stances.items():
        agent_name = AgentName(name_str)
        decisions.append(AgentDecision(
            agent_name=agent_name,
            signal=stance,
            confidence=confidences.get(name_str, 70.0),
            reasoning="mock test reasoning",
            indicators={"rsi_14": 55, "macd_hist": 0.1, "hurst_exponent": 0.5, "z_score": 0.1, "headline_count": 5, "poc": 100.0}
        ))
    return decisions


def test_debate_rules_pure_functions():
    # Enforce maximum swing cap of ±20%
    new_conf, delta = adjust_confidence("Technical", "BUY", 80.0, [
        {"agent_name": "OrderFlow", "stance": "SELL", "evidence": [{"metric": "liquidity_void", "value": True}]},
        {"agent_name": "Quant", "stance": "BUY", "evidence": [{"metric": "hurst_exponent", "value": 0.3}]},
        {"agent_name": "Sentiment", "stance": "SELL"}
    ])
    # Delta should be clipped to -20.0, resulting in 60.0
    assert delta == -20.0
    assert new_conf == 60.0


def test_da_challenge_pure_function():
    # DA reduces confidence by 5% per flag. 5 flags = 25% drop -> clipped to 20%
    new_conf, delta = apply_da_challenge("Technical", "BUY", 80.0, ["flag1", "flag2", "flag3", "flag4", "flag5"])
    assert delta == -20.0
    assert new_conf == 60.0


@pytest.mark.asyncio
async def test_debate_skipped():
    engine = ConsensusEngine()
    
    # Decisions are all in strong agreement (no triggers met)
    stances = {"Technical": Signal.BUY, "Sentiment": Signal.BUY, "Quant": Signal.BUY, "OrderFlow": Signal.BUY}
    confidences = {"Technical": 85.0, "Sentiment": 85.0, "Quant": 85.0, "OrderFlow": 85.0}
    decisions = create_decisions(stances, confidences)
    
    da_decision = AgentDecision(
        agent_name=AgentName.DEVILS_ADVOCATE,
        signal=Signal.HOLD,
        confidence=30.0,
        reasoning="no flags",
        indicators={"flag_count": 0}
    )

    t0 = time.perf_counter()
    signal = await engine.evaluate(
        asset="BTCUSDT",
        request_id="test-req-1",
        regime="bull",
        decisions=decisions,
        da_decision=da_decision,
        hypothesis=None,
        event_bus=None
    )
    duration_ms = (time.perf_counter() - t0) * 1000

    assert signal.final_decision is True
    assert duration_ms < 200.0


@pytest.mark.asyncio
async def test_debate_triggered_by_split():
    engine = ConsensusEngine()
    
    # 2 BUY, 2 SELL -> Split 2-2 trigger
    stances = {"Technical": Signal.BUY, "Sentiment": Signal.BUY, "Quant": Signal.SELL, "OrderFlow": Signal.SELL}
    confidences = {"Technical": 75.0, "Sentiment": 75.0, "Quant": 75.0, "OrderFlow": 75.0}
    decisions = create_decisions(stances, confidences)
    
    da_decision = AgentDecision(
        agent_name=AgentName.DEVILS_ADVOCATE,
        signal=Signal.HOLD,
        confidence=30.0,
        reasoning="no flags",
        indicators={"flag_count": 0}
    )

    signal = await engine.evaluate(
        asset="BTCUSDT",
        request_id="test-req-2",
        regime="bull",
        decisions=decisions,
        da_decision=da_decision,
        hypothesis=None,
        event_bus=None
    )
    
    # Confidences should have delta recorded due to debate
    tech_decision = next(a for a in signal.agents if a["name"] == "Technical")
    assert "confidence_delta" in tech_decision
