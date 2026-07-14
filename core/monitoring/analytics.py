import numpy as np
import random
from typing import List, Dict, Any
from dataclasses import dataclass

@dataclass
class AnalyticsResult:
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    monte_carlo_var_95: float
    monte_carlo_median_return: float

class PortfolioAnalytics:
    def __init__(self, risk_free_rate: float = 0.0):
        self.risk_free_rate = risk_free_rate

    def calculate_metrics(self, trade_pnls: List[float], equity_curve: List[float]) -> AnalyticsResult:
        if not trade_pnls:
            return AnalyticsResult(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        returns = np.array(trade_pnls)
        wins = returns[returns > 0]
        losses = returns[returns < 0]

        win_rate = len(wins) / len(returns) if len(returns) > 0 else 0.0
        
        gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
        gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 1e-9
        profit_factor = gross_profit / gross_loss

        # Annualized metrics approximation (assuming 252 trading days, roughly 1 trade per day)
        trades_per_year = 252
        mean_ret = returns.mean()
        std_ret = returns.std()
        
        sharpe = (mean_ret - self.risk_free_rate) / std_ret * np.sqrt(trades_per_year) if std_ret > 0 else 0.0
        
        downside_returns = returns[returns < 0]
        downside_std = downside_returns.std() if len(downside_returns) > 0 else 1e-9
        sortino = (mean_ret - self.risk_free_rate) / downside_std * np.sqrt(trades_per_year) if downside_std > 0 else 0.0

        eq = np.array(equity_curve)
        peaks = np.maximum.accumulate(eq)
        drawdowns = (peaks - eq) / peaks * 100
        max_dd = float(drawdowns.max()) if len(drawdowns) > 0 else 0.0

        mc_median, mc_var_95 = self.run_monte_carlo(trade_pnls)

        return AnalyticsResult(
            sharpe_ratio=float(sharpe),
            sortino_ratio=float(sortino),
            max_drawdown_pct=max_dd,
            win_rate=win_rate,
            profit_factor=profit_factor,
            monte_carlo_median_return=mc_median,
            monte_carlo_var_95=mc_var_95
        )

    def run_monte_carlo(self, trade_pnls: List[float], simulations: int = 1000, steps: int = 100) -> tuple[float, float]:
        """
        Runs a Monte Carlo simulation by bootstrapping historical trade PnLs.
        Returns the median expected return and 95% Value at Risk (VaR).
        """
        if not trade_pnls or len(trade_pnls) < 2:
            return 0.0, 0.0

        final_returns = []
        for _ in range(simulations):
            # Sample with replacement
            simulated_trades = random.choices(trade_pnls, k=steps)
            cumulative_return = sum(simulated_trades)
            final_returns.append(cumulative_return)

        final_returns.sort()
        median_return = np.median(final_returns)
        # 5th percentile represents the 95% confidence VaR (worst case return)
        var_95 = np.percentile(final_returns, 5)

        return float(median_return), float(var_95)
