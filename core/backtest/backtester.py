"""
Backtesting Engine
Runs the full agent pipeline over historical data to compute strategy performance metrics.
Vectorized where possible; event-driven loop for realistic simulation.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Any
import numpy as np

from core.agents.base_agent import OHLCV, MarketContext, OrderBook
from core.agents.technical_agent import TechnicalAnalystAgent
from core.agents.sentiment_agent import SentimentAgent
from core.agents.quant_agent import QuantAgent
from core.agents.order_flow_agent import OrderFlowAgent
from core.agents.devils_advocate_agent import DevilsAdvocateAgent
from core.agents.meta_agent import ConsensusEngine, TradeSignal
from core.monitoring.regime_detector import detect_regime


@dataclass
class Trade:
    entry_price: float
    exit_price: float
    side: str               # "long" | "short"
    position_size_pct: float
    entry_bar: int
    exit_bar: int
    pnl_pct: float = 0.0
    hit_tp: bool = False
    hit_sl: bool = False
    signal: dict = field(default_factory=dict)

    def __post_init__(self):
        direction = 1 if self.side == "long" else -1
        self.pnl_pct = direction * (self.exit_price - self.entry_price) / self.entry_price * 100


@dataclass
class BacktestResult:
    total_trades: int
    win_rate: float
    avg_return_pct: float
    total_return_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    profit_factor: float
    avg_hold_bars: float
    trades: list[Trade]
    equity_curve: list[float]

    def summary(self) -> dict:
        return {
            "total_trades": self.total_trades,
            "win_rate": f"{self.win_rate:.1%}",
            "avg_return_pct": f"{self.avg_return_pct:.3f}%",
            "total_return_pct": f"{self.total_return_pct:.2f}%",
            "sharpe_ratio": f"{self.sharpe_ratio:.3f}",
            "sortino_ratio": f"{self.sortino_ratio:.3f}",
            "max_drawdown_pct": f"{self.max_drawdown_pct:.2f}%",
            "profit_factor": f"{self.profit_factor:.3f}",
            "avg_hold_bars": f"{self.avg_hold_bars:.1f}",
        }


class Backtester:
    """
    Walk-forward backtester.
    Simulates the full agent pipeline bar-by-bar using only past data.
    No lookahead bias — at bar i, agents only see candles[0:i].
    """

    def __init__(
        self,
        warmup_bars: int = 200,
        sl_pct: float = 0.02,
        tp_pct: float = 0.04,
        commission_pct: float = 0.001,
        position_size_pct: float = 0.05,
    ):
        self.warmup = warmup_bars
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct
        self.commission = commission_pct
        self.position_size = position_size_pct

        self._tech = TechnicalAnalystAgent()
        self._sent = SentimentAgent()
        self._quant = QuantAgent()
        self._of = OrderFlowAgent()
        self._da = DevilsAdvocateAgent()
        self._meta = ConsensusEngine()

    async def run(
        self,
        asset: str,
        candles: list[OHLCV],
        timeframe: str = "1h",
    ) -> BacktestResult:
        trades: list[Trade] = []
        equity = 1.0
        equity_curve = [equity]
        in_trade = False
        entry_price = 0.0
        trade_side = ""
        sl_price = 0.0
        tp_price = 0.0
        entry_bar = 0

        for i in range(self.warmup, len(candles)):
            bar = candles[i]
            history = candles[:i]

            # ── Check exit conditions if in trade ───────────────────────────
            if in_trade:
                hit_sl = (
                    (trade_side == "long" and bar.low <= sl_price) or
                    (trade_side == "short" and bar.high >= sl_price)
                )
                hit_tp = (
                    (trade_side == "long" and bar.high >= tp_price) or
                    (trade_side == "short" and bar.low <= tp_price)
                )

                if hit_sl or hit_tp:
                    exit_price = sl_price if hit_sl else tp_price
                    trade = Trade(
                        entry_price=entry_price,
                        exit_price=exit_price,
                        side=trade_side,
                        position_size_pct=self.position_size,
                        entry_bar=entry_bar,
                        exit_bar=i,
                        hit_tp=hit_tp,
                        hit_sl=hit_sl,
                    )
                    trade_return = trade.pnl_pct / 100 * self.position_size - self.commission
                    equity *= (1 + trade_return)
                    equity_curve.append(equity)
                    trades.append(trade)
                    in_trade = False
                continue

            # ── Run agent pipeline every N bars ──────────────────────────────
            if i % 4 != 0:  # check every 4 bars to save compute
                continue

            regime = detect_regime(history)
            ctx = MarketContext(
                asset=asset,
                timeframe=timeframe,
                candles=history,
                current_price=bar.close,
                regime=regime,
            )

            try:
                decisions = await asyncio.gather(
                    self._tech.analyze(ctx),
                    self._sent.analyze(ctx),
                    self._quant.analyze(ctx),
                    self._of.analyze(ctx),
                )
                da_decision = await self._da.analyze(ctx)
                signal = self._meta.evaluate(
                    asset=asset,
                    request_id=f"bt_{i}",
                    regime=regime,
                    decisions=list(decisions),
                    da_decision=da_decision,
                )
            except Exception:
                continue

            if not signal.final_decision or not signal.action:
                continue

            # ── Enter trade ───────────────────────────────────────────────────
            from core.agents.base_agent import Signal as Sig
            entry_price = bar.close * (1 + self.commission)  # buy at close + commission
            trade_side = "long" if signal.action == Sig.BUY else "short"
            entry_bar = i

            if trade_side == "long":
                sl_price = entry_price * (1 - self.sl_pct)
                tp_price = entry_price * (1 + self.tp_pct)
            else:
                sl_price = entry_price * (1 + self.sl_pct)
                tp_price = entry_price * (1 - self.tp_pct)

            in_trade = True

        # Close any open trade at last bar
        if in_trade and candles:
            exit_p = candles[-1].close
            trade = Trade(
                entry_price=entry_price,
                exit_price=exit_p,
                side=trade_side,
                position_size_pct=self.position_size,
                entry_bar=entry_bar,
                exit_bar=len(candles) - 1,
            )
            equity *= (1 + trade.pnl_pct / 100 * self.position_size)
            equity_curve.append(equity)
            trades.append(trade)

        return self._compute_stats(trades, equity_curve)

    def _compute_stats(self, trades: list[Trade], equity_curve: list[float]) -> BacktestResult:
        if not trades:
            return BacktestResult(
                total_trades=0, win_rate=0, avg_return_pct=0, total_return_pct=0,
                sharpe_ratio=0, sortino_ratio=0, max_drawdown_pct=0,
                profit_factor=0, avg_hold_bars=0, trades=[], equity_curve=equity_curve,
            )

        returns = np.array([t.pnl_pct for t in trades])
        wins = returns[returns > 0]
        losses = returns[returns < 0]

        win_rate = len(wins) / len(trades)
        avg_return = float(returns.mean())
        total_return = (equity_curve[-1] - 1.0) * 100 if equity_curve else 0.0

        # Sharpe (annualized, assuming bars = 1h → 252*24 bars/year)
        bars_per_year = 252 * 24
        avg_bars = np.mean([t.exit_bar - t.entry_bar for t in trades])
        trades_per_year = bars_per_year / avg_bars if avg_bars > 0 else 1
        sharpe = (returns.mean() / returns.std() * np.sqrt(trades_per_year)) if returns.std() > 0 else 0.0

        # Sortino
        downside_returns = returns[returns < 0]
        downside_std = downside_returns.std() if len(downside_returns) > 0 else 1e-9
        sortino = (returns.mean() / downside_std * np.sqrt(trades_per_year)) if downside_std > 0 else 0.0

        # Max drawdown
        eq = np.array(equity_curve)
        peaks = np.maximum.accumulate(eq)
        drawdowns = (peaks - eq) / peaks * 100
        max_dd = float(drawdowns.max())

        # Profit factor
        gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
        gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 1e-9
        pf = gross_profit / gross_loss

        return BacktestResult(
            total_trades=len(trades),
            win_rate=win_rate,
            avg_return_pct=avg_return,
            total_return_pct=total_return,
            sharpe_ratio=float(sharpe),
            sortino_ratio=float(sortino),
            max_drawdown_pct=max_dd,
            profit_factor=pf,
            avg_hold_bars=float(avg_bars),
            trades=trades,
            equity_curve=list(equity_curve),
        )
