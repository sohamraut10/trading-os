import numpy as np
import pandas as pd
from typing import List, Dict, Any

class RobustnessSuite:
    def __init__(self, initial_capital: float = 1000000.0):
        self.initial_capital = initial_capital

    def run_monte_carlo(self, trades: List[Dict[str, Any]], iterations: int = 1000) -> Dict[str, Any]:
        """
        Runs Monte Carlo simulation by shuffling trade sequences.
        Returns the 5th percentile distribution of final equity and max drawdown,
        and the probability of ruin (hitting 0 capital).
        """
        if not trades:
            return {}
            
        # Extract net PnL from trades
        pnls = np.array([t.get('net_pnl', 0.0) for t in trades])
        n_trades = len(pnls)
        
        # We want seeded RNG for deterministic audits
        rng = np.random.default_rng(seed=42)
        
        final_equities = np.zeros(iterations)
        max_drawdowns = np.zeros(iterations)
        ruin_count = 0
        
        for i in range(iterations):
            # Shuffle trade sequence
            shuffled_pnls = rng.permutation(pnls)
            
            # Reconstruct equity curve
            equity_curve = self.initial_capital + np.cumsum(shuffled_pnls)
            
            # Check ruin
            if np.any(equity_curve <= 0):
                ruin_count += 1
                
            final_equities[i] = equity_curve[-1]
            
            # Calculate max drawdown for this path
            running_max = np.maximum.accumulate(equity_curve)
            drawdowns = (running_max - equity_curve) / running_max
            max_drawdowns[i] = np.max(drawdowns)
            
        p5_equity = np.percentile(final_equities, 5)
        p5_mdd = np.percentile(max_drawdowns, 95) # 95th percentile is worst for drawdown
        prob_ruin = ruin_count / iterations
        
        return {
            'mean_final_equity': np.mean(final_equities),
            'p5_final_equity': p5_equity,
            'mean_max_drawdown': np.mean(max_drawdowns),
            'p95_max_drawdown': p5_mdd,
            'probability_of_ruin': prob_ruin,
            'is_robust': p5_equity > self.initial_capital and prob_ruin < 0.01
        }

    def slice_by_regime(self, trades: List[Dict[str, Any]], regime_tags: Dict[str, str]) -> Dict[str, Any]:
        """
        Buckets trades by market regime (e.g., 'trending', 'ranging', 'high_iv')
        regime_tags: Dictionary mapping trade_id or timestamp to a regime string.
        """
        metrics_by_regime = {}
        
        # Group trades
        for t in trades:
            ts = t.get('entry_ts')
            # Fallback to 'unknown' if no tag provided for this timestamp
            regime = regime_tags.get(ts, 'unknown')
            
            if regime not in metrics_by_regime:
                metrics_by_regime[regime] = {'pnls': [], 'count': 0}
                
            metrics_by_regime[regime]['pnls'].append(t.get('net_pnl', 0.0))
            metrics_by_regime[regime]['count'] += 1
            
        # Summarize
        summary = {}
        for regime, data in metrics_by_regime.items():
            pnls = np.array(data['pnls'])
            summary[regime] = {
                'count': data['count'],
                'total_pnl': np.sum(pnls),
                'win_rate': np.sum(pnls > 0) / data['count'] if data['count'] > 0 else 0,
                'avg_trade': np.mean(pnls) if data['count'] > 0 else 0
            }
            
        return summary
        
    def parameter_sweep(self, base_strategy, param_name: str, values: List[float], backtest_fn):
        """
        Runs sensitivity analysis by modifying a parameter and re-running the engine.
        Returns dispersion of the Sharpe ratio.
        """
        # Pseudo-implementation. The orchestrator calls this with the backtest_fn.
        results = {}
        for val in values:
            # Modify strategy clone
            strategy_clone = base_strategy.clone()
            setattr(strategy_clone, param_name, val)
            
            # Run engine
            metrics = backtest_fn(strategy_clone)
            results[val] = metrics.get('sharpe_ratio', 0)
            
        return results
