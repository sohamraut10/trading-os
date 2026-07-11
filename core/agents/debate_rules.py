from typing import List, Dict, Any, Tuple


def adjust_confidence(
    agent_name: str,
    current_stance: str,
    current_confidence: float,
    other_arguments: List[Dict[str, Any]]
) -> Tuple[float, float]:
    """
    Applies rebuttal adjustment rules to compute confidence delta.
    Max confidence swing per round: ±20%.
    Returns (new_confidence, confidence_delta).
    """
    import numpy as np
    
    delta = 0.0
    
    # Extract stance of other agents
    other_stances = {arg["agent_name"]: arg["stance"] for arg in other_arguments}
    other_evidences = {arg["agent_name"]: arg.get("evidence", []) for arg in other_arguments}

    if agent_name == "Technical":
        # Rule 1: Technical drops confidence 10% if OrderFlow shows a liquidity void inside the entry zone
        of_void = any(
            ev.get("metric") == "liquidity_void" and ev.get("value", False)
            for ev in other_evidences.get("OrderFlow", [])
        )
        if of_void:
            delta -= 10.0
            
        # Rule 2: Technical drops 10% if Quant shows Hurst < 0.45 (trend follow regime mismatch)
        quant_hurst = next(
            (ev.get("value", 0.5) for ev in other_evidences.get("Quant", []) if ev.get("metric") == "hurst_exponent"),
            0.5
        )
        if current_stance == "BUY" and quant_hurst < 0.45:
            delta -= 10.0

        # Rule 3: Sentiment counter-directional drops confidence 10%
        if other_stances.get("Sentiment") and other_stances.get("Sentiment") != current_stance:
            delta -= 10.0

    elif agent_name == "Quant":
        # Rule 1: Quant drops 15% if regime mismatch flagged
        regime_mismatch = any(
            ev.get("metric") == "regime_mismatch" and ev.get("value", False)
            for ev in other_evidences.get("Quant", [])
        )
        if regime_mismatch:
            delta -= 15.0
            
        # Rule 2: Quant drops 10% if Technical disagrees
        if other_stances.get("Technical") and other_stances.get("Technical") != current_stance:
            delta -= 10.0

    elif agent_name == "Sentiment":
        # Rule 1: Sentiment drops 10% if Technical and Quant both disagree
        tech_disagrees = other_stances.get("Technical") and other_stances.get("Technical") != current_stance
        quant_disagrees = other_stances.get("Quant") and other_stances.get("Quant") != current_stance
        if tech_disagrees and quant_disagrees:
            delta -= 15.0

    elif agent_name == "OrderFlow":
        # Rule 1: OrderFlow drops 10% if Technical disagrees
        if other_stances.get("Technical") and other_stances.get("Technical") != current_stance:
            delta -= 10.0

    # Enforce maximum swing cap of ±20%
    delta = float(np.clip(delta, -20.0, 20.0))
    new_confidence = float(np.clip(current_confidence + delta, 0.0, 100.0))
    return new_confidence, delta


def apply_da_challenge(
    agent_name: str,
    stance: str,
    confidence: float,
    da_flags: List[str]
) -> Tuple[float, float]:
    """
    Applies Devil's Advocate cross-examination adjustments.
    For each active risk flag raised by DA, majority stance confidence is reduced.
    Max swing cap: ±20%.
    """
    import numpy as np
    
    if stance == "HOLD" or not da_flags:
        return confidence, 0.0

    # Reduce confidence by 5% per flag raised
    delta = -5.0 * len(da_flags)
    delta = float(np.clip(delta, -20.0, 0.0))
    new_confidence = float(np.clip(confidence + delta, 0.0, 100.0))
    return new_confidence, delta
