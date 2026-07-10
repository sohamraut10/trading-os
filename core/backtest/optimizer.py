"""
Backtest Parameter Optimizer
Walk-forward grid search over (sl_pct, tp_pct, min_confidence) space.
Ranks by Sharpe ratio; avoids overfitting by using out-of-sample validation.
"""
import asyncio
import itertools
from dataclasses import dataclass, field
from typing import Any

from core.backtest.backtester import Backtester, BacktestResult
from core.agents.base_agent import OHLCV


@dataclass
class ParamGrid:
    sl_pcts:         list[float] = field(default_factory=lambda: [0.01, 0.015, 0.02, 0.03])
    tp_multipliers:  list[float] = field(default_factory=lambda: [1.5, 2.0, 2.5, 3.0])
    commission_pcts: list[float] = field(default_factory=lambda: [0.001])

    def combinations(self) -> list[dict]:
        combos = []
        for sl, tp_mult, comm in itertools.product(
            self.sl_pcts, self.tp_multipliers, self.commission_pcts
        ):
            combos.append({"sl_pct": sl, "tp_pct": sl * tp_mult, "commission_pct": comm, "tp_mult": tp_mult})
        return combos


@dataclass
class OptimizationResult:
    params: dict
    in_sample:  BacktestResult
    out_sample: BacktestResult
    combined_sharpe: float    # average of in + out Sharpe — penalises overfit
    overfit_score: float      # |in_sharpe - out_sharpe| — lower is better

    def summary(self) -> dict:
        return {
            "params": self.params,
            "in_sample_sharpe":  round(self.in_sample.sharpe_ratio, 3),
            "out_sample_sharpe": round(self.out_sample.sharpe_ratio, 3),
            "combined_sharpe":   round(self.combined_sharpe, 3),
            "overfit_score":     round(self.overfit_score, 3),
            "out_win_rate":      self.out_sample.summary()["win_rate"],
            "out_max_dd":        self.out_sample.summary()["max_drawdown_pct"],
            "out_trades":        self.out_sample.total_trades,
        }


class BacktestOptimizer:
    """
    Walk-forward optimization:
    1. Split candles into in-sample (70%) and out-of-sample (30%)
    2. Grid search params on in-sample data ranked by Sharpe
    3. Validate top-K params on out-of-sample
    4. Rank final results by combined Sharpe (penalises overfit)

    Concurrency: all parameter combinations run as parallel asyncio tasks.
    """

    def __init__(
        self,
        grid: ParamGrid | None = None,
        in_sample_pct: float = 0.70,
        warmup_bars: int = 200,
        top_k: int = 5,
        max_concurrent: int = 8,
    ):
        self.grid = grid or ParamGrid()
        self.in_sample_pct = in_sample_pct
        self.warmup = warmup_bars
        self.top_k = top_k
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def optimize(
        self, asset: str, candles: list[OHLCV], timeframe: str = "1h"
    ) -> list[OptimizationResult]:
        split = int(len(candles) * self.in_sample_pct)
        in_candles  = candles[:split]
        out_candles = candles[split - self.warmup:]   # include warmup overlap

        combos = self.grid.combinations()
        print(f"[Optimizer] {len(combos)} combinations · in-sample={len(in_candles)} out-sample={len(out_candles)} bars")

        # ── Phase 1: in-sample grid search ───────────────────────────────────
        in_results = await asyncio.gather(*[
            self._run_one(asset, in_candles, timeframe, p) for p in combos
        ])

        # Rank by in-sample Sharpe, take top-K
        ranked = sorted(
            zip(combos, in_results),
            key=lambda x: x[1].sharpe_ratio,
            reverse=True,
        )[:self.top_k]

        # ── Phase 2: out-of-sample validation ────────────────────────────────
        top_params  = [p for p, _ in ranked]
        top_in      = [r for _, r in ranked]
        out_results = await asyncio.gather(*[
            self._run_one(asset, out_candles, timeframe, p) for p in top_params
        ])

        # ── Phase 3: combine and rank ─────────────────────────────────────────
        final = []
        for params, in_r, out_r in zip(top_params, top_in, out_results):
            combined = (in_r.sharpe_ratio + out_r.sharpe_ratio) / 2
            overfit  = abs(in_r.sharpe_ratio - out_r.sharpe_ratio)
            final.append(OptimizationResult(
                params=params,
                in_sample=in_r,
                out_sample=out_r,
                combined_sharpe=combined,
                overfit_score=overfit,
            ))

        return sorted(final, key=lambda x: x.combined_sharpe, reverse=True)

    async def _run_one(
        self, asset: str, candles: list[OHLCV], timeframe: str, params: dict
    ) -> BacktestResult:
        async with self._semaphore:
            bt = Backtester(
                warmup_bars=self.warmup,
                sl_pct=params["sl_pct"],
                tp_pct=params["tp_pct"],
                commission_pct=params["commission_pct"],
            )
            return await bt.run(asset, candles, timeframe)

    def print_report(self, results: list[OptimizationResult]) -> None:
        print(f"\n{'─'*70}")
        print(f"  OPTIMIZATION RESULTS  (top {len(results)})")
        print(f"{'─'*70}")
        header = f"{'SL%':>6} {'TP×':>5} {'InSharpe':>9} {'OutSharpe':>10} {'Overfit':>8} {'OutWR':>7} {'Trades':>7}"
        print(header)
        print("─" * 70)
        for r in results:
            s = r.summary()
            print(
                f"{r.params['sl_pct']*100:>5.1f}%"
                f"{r.params['tp_mult']:>6.1f}×"
                f"{r.in_sample.sharpe_ratio:>10.3f}"
                f"{r.out_sample.sharpe_ratio:>11.3f}"
                f"{r.overfit_score:>9.3f}"
                f"  {s['out_win_rate']:>7}"
                f"  {s['out_trades']:>5}"
            )
        if results:
            best = results[0]
            print(f"\n  Best params: sl={best.params['sl_pct']*100:.1f}% "
                  f"tp={best.params['tp_pct']*100:.1f}% "
                  f"(×{best.params['tp_mult']:.1f}) "
                  f"combined_sharpe={best.combined_sharpe:.3f}")
        print("─" * 70)
