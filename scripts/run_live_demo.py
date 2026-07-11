"""
Live runner — executes a full consensus cycle on LIVE market data from Binance.
No API keys required for public market data.

Usage:
    python scripts/run_live_demo.py
    python scripts/run_live_demo.py --asset ETHUSDT
"""
import asyncio
import json
import argparse
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agents.base_agent import MarketContext, OHLCV, OrderBook, OrderBookLevel
from core.agents.technical_agent import TechnicalAnalystAgent
from core.agents.sentiment_agent import SentimentAgent
from core.agents.quant_agent import QuantAgent
from core.agents.order_flow_agent import OrderFlowAgent
from core.agents.devils_advocate_agent import DevilsAdvocateAgent
from core.agents.meta_agent import ConsensusEngine
from core.monitoring.regime_detector import detect_regime
from core.risk.risk_engine import RiskEngine, PortfolioState
from core.data.market_data import BinanceProvider


async def run(asset: str):
    # Normalize asset for Binance (e.g., BTC/USDT -> BTCUSDT)
    binance_symbol = asset.replace("/", "").upper()

    print(f"Connecting to Binance public API to fetch live data for {binance_symbol}...")
    provider = BinanceProvider()

    try:
        t_data_start = time.perf_counter()
        candles = await provider.get_candles(binance_symbol, timeframe="1h", limit=300)
        price = await provider.get_current_price(binance_symbol)
        ob = await provider.get_order_book(binance_symbol, depth=20)
        elapsed_data = (time.perf_counter() - t_data_start) * 1000
        print(f"Fetched live market data (300 candles + L2 book) in {elapsed_data:.1f}ms\n")
    except Exception as e:
        print(f"Error fetching data from Binance: {e}")
        print("Please check your internet connection or the symbol name.")
        sys.exit(1)

    regime = detect_regime(candles)
    print(f"{'='*60}")
    print(f"  TRADING OS — Multi-Agent Consensus Engine (LIVE DATA)")
    print(f"{'='*60}")
    print(f"  Asset    : {asset}")
    print(f"  Live Price: ${price:,.2f}")
    print(f"  Regime   : {regime.upper()}")
    print(f"{'='*60}\n")

    # Use standard relevant crypto news headlines
    news_headlines = [
        f"Bitcoin ETF inflows signal strong institutional demand for {asset}",
        "Global regulatory clarity boosts confidence in crypto assets",
        "Macro liquidity conditions support risk-on market assets",
    ]

    ctx = MarketContext(
        asset=asset,
        timeframe="1h",
        candles=candles,
        current_price=price,
        order_book=ob,
        news_headlines=news_headlines,
        macro_context={"vix": 15, "sp500_1d_change_pct": 0.2, "near_fed_event": False, "days_to_earnings": 30},
        portfolio_context={"active_strategy": "swing", "consecutive_losses": 0},
        regime=regime,
        request_id="live-demo-001",
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

    print("Running agents in parallel on live data...")
    t0 = time.perf_counter()
    decisions = await asyncio.gather(*[a.analyze(ctx) for a in agents])
    da_decision = await da.analyze(ctx)
    elapsed = (time.perf_counter() - t0) * 1000

    print(f"Agent analysis complete in {elapsed:.1f}ms\n")

    for d in decisions:
        icon = {"BUY": "▲", "SELL": "▼", "HOLD": "●"}.get(d.signal.value, "?")
        print(f"  [{icon}] {d.agent_name.value:<12} | {d.signal.value:<5} | conf={d.confidence:>5.1f}% | {d.reasoning[:70]}")

    print(f"  [☠] DevilsAdvocate | {da_decision.signal.value:<5} | conf={da_decision.confidence:>5.1f}% | {da_decision.reasoning[:70]}")

    print(f"\n{'─'*60}")
    print("  META AGENT — Computing consensus...")
    signal = meta.evaluate(
        asset=asset,
        request_id="live-demo-001",
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
    parser = argparse.ArgumentParser(description="Trading OS Live Demo")
    parser.add_argument("--asset", default="BTC/USDT", help="Binance asset pair (e.g. BTC/USDT or ETH/USDT)")
    args = parser.parse_args()
    asyncio.run(run(args.asset))
