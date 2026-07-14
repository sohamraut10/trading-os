import json
import logging
import random
from typing import Dict, Any

log = logging.getLogger("trading_os.learning")

class AgentWeightOptimizer:
    """
    Reinforcement Learning (RL) loop for tuning agent weights.
    Uses a simple epsilon-greedy multi-armed bandit or gradient ascent approach
    to adjust the influence of Technical, Sentiment, and Quant agents based on their
    historical hit rates (win rates) in the current market regime.
    """
    def __init__(self, learning_rate: float = 0.05, epsilon: float = 0.1):
        self.learning_rate = learning_rate
        self.epsilon = epsilon
        
        # Default baseline weights
        self.weights = {
            "Technical": 1.0,
            "Sentiment": 1.0,
            "Quant": 1.0,
            "OrderFlow": 1.0
        }

    def optimize_weights(self, historical_trades: list[dict], current_regime: str) -> Dict[str, float]:
        """
        Adjusts weights based on which agents correctly predicted winning trades
        in the given regime.
        """
        if not historical_trades:
            return self.weights

        # Explore vs Exploit
        if random.random() < self.epsilon:
            log.info("RL Optimizer: Exploring random weight adjustments")
            return self._explore()
            
        return self._exploit(historical_trades, current_regime)

    def _explore(self) -> Dict[str, float]:
        explored_weights = {}
        for agent, w in self.weights.items():
            # Random jitter +/- 10%
            jitter = random.uniform(-0.1, 0.1)
            explored_weights[agent] = max(0.1, min(2.0, w + jitter))
        return explored_weights

    def _exploit(self, historical_trades: list[dict], current_regime: str) -> Dict[str, float]:
        agent_scores = {k: 0.0 for k in self.weights.keys()}
        agent_counts = {k: 0 for k in self.weights.keys()}

        for trade in historical_trades:
            if trade.get("regime") != current_regime:
                continue
                
            pnl = trade.get("pnl_pct", 0.0)
            is_win = pnl > 0
            
            # Look at which agents voted for this trade's direction
            agents = trade.get("signal", {}).get("agents", [])
            for agent in agents:
                name = agent.get("name")
                decision = agent.get("decision")
                
                if name in agent_scores:
                    agent_counts[name] += 1
                    # Reward for agreeing with a winning trade, penalize for agreeing with a losing trade
                    if is_win:
                        agent_scores[name] += pnl
                    else:
                        agent_scores[name] -= abs(pnl)

        # Update weights based on scores
        new_weights = self.weights.copy()
        for name, score in agent_scores.items():
            if agent_counts[name] > 0:
                avg_score = score / agent_counts[name]
                # Gradient update
                new_weights[name] += self.learning_rate * avg_score
                new_weights[name] = max(0.1, min(2.0, new_weights[name]))

        self.weights = new_weights
        log.info(f"RL Optimizer updated weights for {current_regime}: {self.weights}")
        return self.weights
