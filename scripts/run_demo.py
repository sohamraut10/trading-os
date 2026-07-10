"""
Demo runner — executes a full consensus cycle on mock BTC/USDT data
and prints the final output. No API keys required.

Usage:
    python scripts/run_demo.py
    python scripts/run_demo.py --asset ETHUSDT --trend bearish
"""
import asyncio
import json
import argparse
import sys
import time

sys.path.insert(0, "/home/ubuntu/trading-os")

import numpy as np
from core.agents.base_agent import MarketContext, OHLCV, OrderBook, OrderBookLevel
from core.agents.technical_agent import TechnicalAnalystAgent
from core.agents.sentiment_agent import SentimentAgent
from core.agents.quant_agent import QuantAgent
from core.agents.order_flow_agent import OrderFlowAgent
from core.agents.devils_advocate_agent import DevilsAdvocateAgent
from core.agents.meta_agent import ConsensusEngine
from core.monitoring.regime_detector import detect_regime
from core.risk.risk_engine import RiskEngine, PortfolioState
from core.data.market_data import MockProvider


def generate_candles(n: int = 300, trend: float = 0.0015, seed: int = 42) -> list[OHLCV]:
    rng = np.random.default_rng(seed)
    price = 65000.0
    candles = []
    for i in range(n):
        ret = rng.normal(trend, 0.012)
        o = price
        c = price * (1 + ret)
        h = max(o, c) * (1 + abs(rng.normal(0, 0.002)))
        l = min(o, c) * (1 - abs(rng.normal(0, 0.002)))
        v = rng.uniform(800, 4500)
        candles.append(OHLCV(time.time() - (n - i) * 3600, round(o, 2), round(h, 2), round(l, 2), round(c, 2), round(v, 2)))
        price = c
    return candles


async def run(asset: str, trend_arg: str):
    trend_map = {"bullish": 0.002, "bearish": -0.002, "sideways": 0.0}
    trend = trend_map.get(trend_arg, 0.001)
    seed = {"bullish": 42, "bearish": 99, "sideways": 7}.get(trend_arg, 42)

    candles = generate_candles(300, trend, seed)
    price = candles[-1].close

    bids = [OrderBookLevel(price * (1 - 0.0005 * (i+1)), 2.5 + i * 0.1) for i in range(20)]
    asks = [OrderBookLevel(price * (1 + 0.0005 * (i+1)), 2.5 - i * 0.05) for i in range(20)]
    ob = OrderBook(bids=bids, asks=asks, timestamp=time.time())

    regime = detect_regime(candles)
    print(f"\n{'='*60}")
    print(f"  TRADING OS — Multi-Agent Consensus Engine")
    print(f"{'='*60}")
    print(f"  Asset    : {asset}")
    print(f"  Price    : ${price:,.2f}")
    print(f"  Regime   : {regime.upper()}")
    print(f"  Scenario : {trend_arg.upper()}")
    print(f"{'='*60}\n")

    ctx = MarketContext(
        asset=asset,
        timeframe="1h",
        candles=candles,
        current_price=price,
        order_book=ob,
        news_headlines=[
            "Bitcoin institutional demand surges to record",
            "Federal Reserve holds rates steady",
            "Crypto ETF approval drives market optimism",
        ] if trend_arg == "bullish" else [
            "Crypto market selloff amid regulatory concerns",
            "Large exchange reports security breach",
            "SEC intensifies crypto scrutiny",
        ],
        macro_context={"vix": 18, "sp500_1d_change_pct": 0.5, "near_fed_event": False, "days_to_earnings": 30},
        portfolio_context={"active_strategy": "swing", "consecutive_losses": 0},
        regime=regime,
        request_id="demo-001",
    )

    agents = [
        TechnicalAnalystAgent(),
        SentimentAgent(api_key=""),
        QuantAgent(),
        OrderFlowAgent(),
    ]
    da = DevilsAdvocateAgent()
    meta = ConsensusEngine()
    risk_engine = RiskEngine()

    print("Running agents in parallel...")
    t0 = time.perf_counter()
    decisions = await asyncio.gather(*[a.analyze(ctx) for a in agents])
    da_decision = await da.analyze(ctx)
    elapsed = (time.perf_counter() - t0) * 1000

    print(f"Agent analysis complete in {elapsed:.1f}ms\n")

    for d in decisions:
        icon = {"BUY": "▲", "SELL": "▼", "HOLD": "●"}.get(d.signal.value, "?")
        print(f"  [{icon}] {d.agent_name.value:<12} | {d.signal.value:<5} | conf={d.confidence:>5.1f}% | {d.reasoning[:70]}")

    print(f"\n  [☠] DevilsAdvocate | {da_decision.signal.value:<5} | conf={da_decision.confidence:>5.1f}% | {da_decision.reasoning[:70]}")

    print(f"\n{'─'*60}")
    print("  META AGENT — Computing consensus...")
    signal = meta.evaluate(
        asset=asset,
        request_id="demo-001",
        regime=regime,
        decisions=list(decisions),
        da_decision=da_decision,
    )

    verdict_icon = "✅ TRUE SIGNAL" if signal.final_decision else "❌ FALSE SIGNAL"
    print(f"\n  {verdict_icon}")
    if signal.final_decision:
        print(f"  Action     : {signal.action.value if signal.action else 'N/A'}")
    print(f"  Confidence : {signal.confidence:.1f}%")
    print(f"  Reason     : {signal.reason[:120]}")

    if signal.conflict_notes:
        print(f"\n  Conflicts  :")
        for note in signal.conflict_notes:
            print(f"    • {note}")

    if signal.override_reason:
        print(f"  Override   : {signal.override_reason}")

    # Risk check
    portfolio = PortfolioState(
        equity=100_000.0, cash=90_000.0, open_trades=1,
        daily_pnl_pct=0.005, max_daily_drawdown_pct=0.005,
        positions={"ETH/USDT": 10_000.0},
    )
    risk_result = risk_engine.check(signal, portfolio, price)

    print(f"\n  Risk Check : {risk_result.status.value}")
    if risk_result.is_tradeable():
        print(f"  Size       : ${risk_result.approved_position_size_usd:,.0f} ({risk_result.approved_position_size_pct:.2%})")
        print(f"  Stop Loss  : ${risk_result.stop_loss_price:,.2f}")
        print(f"  Take Profit: ${risk_result.take_profit_price:,.2f}")
        print(f"  Risk/Reward: {risk_result.approved_position_size_usd / max(1, abs(price - risk_result.stop_loss_price) * risk_result.approved_position_size_usd / price):.1f}x")

    if risk_result.rejection_reasons:
        for r in risk_result.rejection_reasons:
            print(f"  ✗ {r}")

    # Final JSON output
    print(f"\n{'='*60}")
    print("  JSON OUTPUT:")
    print(f"{'─'*60}")
    output = signal.to_dict()
    output["risk_check"] = {
        "status": risk_result.status.value,
        "size_usd": risk_result.approved_position_size_usd,
        "sl_price": risk_result.stop_loss_price,
        "tp_price": risk_result.take_profit_price,
    }
    print(json.dumps(output, indent=2))
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trading OS Demo")
    parser.add_argument("--asset", default="BTC/USDT")
    parser.add_argument("--trend", default="bullish", choices=["bullish", "bearish", "sideways"])
    args = parser.parse_args()
    asyncio.run(run(args.asset, args.trend))
