"""
Trading OS Orchestrator — Main Event Loop
Coordinates: data fetch → regime detection → multi-timeframe validation →
agent pipeline → strategy filter → risk gate → execution → learning feedback.

One Orchestrator instance per watched symbol. Run multiple concurrently for a portfolio.
"""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from config.settings import settings
from core.agents import (
    TechnicalAnalystAgent, SentimentAgent, QuantAgent,
    OrderFlowAgent, DevilsAdvocateAgent, ConsensusEngine,
    MarketContext,
)
from core.agents.base_agent import Signal, OrderBook, OrderBookLevel
from core.agents.meta_agent import TradeSignal
from core.data.market_data import MarketDataProvider
from core.data.news_feed import NewsFeed
from core.monitoring.regime_detector import detect_regime, multi_timeframe_regimes, regime_consensus
from core.risk.risk_engine import RiskEngine, PortfolioState
from core.execution.broker_interface import SmartOrderRouter
from core.monitoring.alerts import AlertRouter
from core.monitoring.trade_journal import TradeJournal
from core.learning.adaptive_weights import AdaptiveWeightManager
from core.strategy.strategies import select_strategy, BaseStrategy
from core.streaming.kafka_bus import InMemoryBus, make_bus
from core.streaming.event_bus import EventBus
from core.strategy.selector import StrategySelector
from core.strategy.strategies import STRATEGY_REGISTRY
from core.persistence.repository import Repository

log = logging.getLogger("trading_os.orchestrator")


@dataclass
class CycleResult:
    """Output of one analysis cycle."""
    request_id: str
    asset: str
    timestamp: float
    signal: TradeSignal | None
    strategy_name: str
    strategy_accepted: bool
    strategy_reason: str
    executed: bool = False
    cycle_ms: float = 0.0
    error: str | None = None
    current_price: float = 0.0
    risk_result: Any = None

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "asset": self.asset,
            "timestamp": self.timestamp,
            "signal": self.signal.to_dict() if self.signal else None,
            "strategy": self.strategy_name,
            "strategy_accepted": self.strategy_accepted,
            "strategy_reason": self.strategy_reason,
            "executed": self.executed,
            "cycle_ms": round(self.cycle_ms, 2),
            "error": self.error,
        }


class Orchestrator:
    """
    Main loop for a single asset.
    Designed to be run as an asyncio task: await orchestrator.run_forever().
    """

    def __init__(
        self,
        asset: str,
        timeframe: str,
        data_provider: MarketDataProvider,
        news_feed: NewsFeed,
        portfolio: PortfolioState,
        router: SmartOrderRouter,
        alerts: AlertRouter,
        journal: TradeJournal,
        weights_manager: AdaptiveWeightManager,
        candle_limit: int = 300,
        cycle_interval_sec: float = 60.0,
        auto_execute: bool = False,
        strategy_override: str | None = None,
        event_bus=None,
        repository: Repository | None = None,
    ):
        self.asset = asset
        self.timeframe = timeframe
        self._data = data_provider
        self._news = news_feed
        self._portfolio = portfolio
        self._router = router
        self._alerts = alerts
        self._journal = journal
        self._weights = weights_manager
        self._candle_limit = candle_limit
        self._interval = cycle_interval_sec
        self._auto_execute = auto_execute
        self._strategy_override = strategy_override
        # Wrap self._bus with our EventBus so we get typed events
        if event_bus is None or not hasattr(event_bus, "publish"):
            self._bus = EventBus(settings.kafka_bootstrap_servers)
        else:
            self._bus = event_bus
        # Note: this only stores the reference for per-cycle record_signal/
        # snapshot_portfolio calls. It does NOT register an event-bus hook —
        # callers sharing one EventBus across multiple Orchestrators (e.g. a
        # portfolio watchlist) must register that once themselves, or every
        # instance sharing the bus would double up on event persistence.
        self._repository = repository

        # Agent instances — reused across cycles
        self._tech = TechnicalAnalystAgent()
        self._sent = SentimentAgent(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            gemini_api_key=settings.gemini_api_key,
            gemini_model=settings.gemini_model,
            provider=settings.llm_provider,
        )
        self._quant = QuantAgent()
        self._of = OrderFlowAgent()
        self._da = DevilsAdvocateAgent()
        self._meta = ConsensusEngine()
        self._risk = RiskEngine()
        self._selector = StrategySelector()

        self._running = False
        self._cycle_count = 0
        self._last_signal: TradeSignal | None = None
        self._history: list[CycleResult] = []

    async def run_forever(self) -> None:
        self._running = True
        log.info("Orchestrator started: %s @ %s (interval=%ds)", self.asset, self.timeframe, self._interval)

        while self._running:
            try:
                result = await self.run_cycle()

                log.info(
                    "Cycle #%d | %s | %s | strategy_ok=%s | exec=%s | %.0fms",
                    self._cycle_count,
                    self.asset,
                    result.signal.to_dict()["final_decision"] if result.signal else "ERROR",
                    result.strategy_accepted,
                    result.executed,
                    result.cycle_ms,
                )
            except Exception as e:
                log.exception("Cycle error: %s", e)
                await self._alerts.circuit_breaker(f"Orchestrator error on {self.asset}: {e}")

            await asyncio.sleep(self._interval)

    def stop(self) -> None:
        self._running = False

    async def run_cycle(self, execute: bool | None = None) -> CycleResult:
        # Caller can override per-call; fall back to instance flag set at construction
        _execute = execute if execute is not None else self._auto_execute
        t0 = time.perf_counter()
        request_id = str(uuid.uuid4())
        self._cycle_count += 1

        try:
            # ── 1. Fetch data (parallel) ──────────────────────────────────────
            candles, price = await asyncio.gather(
                self._data.get_candles(self.asset, self.timeframe, self._candle_limit),
                self._data.get_current_price(self.asset),
            )

            # Fallback: brokers like Dhan limit intraday history to ~5 trading
            # days (~31 1h bars for NSE). When that's below what agents need,
            # retry with daily candles so Technical/Quant can still run.
            _MIN_CANDLES_NEEDED = 60
            if len(candles) < _MIN_CANDLES_NEEDED and self.timeframe != "1d":
                try:
                    daily = await self._data.get_candles(self.asset, "1d", self._candle_limit)
                    if len(daily) >= _MIN_CANDLES_NEEDED:
                        log.info(
                            "%s: only %d %s candles — falling back to %d daily bars",
                            self.asset, len(candles), self.timeframe, len(daily),
                        )
                        candles = daily
                except Exception:
                    pass

            # Emit BarClosed — the dashboard's event reducer seeds a new
            # cycle's `asset` field from whichever event arrives first for
            # that cycle_id, and this is the only event carrying it.
            await self._bus.publish("BarClosed", request_id, {
                "asset": self.asset,
                "bar": {
                    "timestamp": time.time(),
                    "open": price, "high": price, "low": price, "close": price,
                    "volume": 1.0,
                },
            })

            async def _order_book_or_synthetic():
                try:
                    return await self._data.get_order_book(self.asset)
                except Exception:
                    bids = [OrderBookLevel(price=price * (1 - 0.0001 * (i + 1)), size=1.0) for i in range(20)]
                    asks = [OrderBookLevel(price=price * (1 + 0.0001 * (i + 1)), size=1.0) for i in range(20)]
                    return OrderBook(bids=bids, asks=asks, timestamp=time.time())

            ob, headlines, sentiment, macro = await asyncio.gather(
                _order_book_or_synthetic(),
                self._news.get_news_headlines(self.asset),
                self._news.get_social_sentiment(self.asset),
                self._news.get_macro_context(),
            )

            # ── 2. Multi-timeframe regime ──────────────────────────────────────
            secondary_tf = "4h" if self.timeframe in ("1h", "15m") else "1d"
            try:
                candles_4h = await self._data.get_candles(self.asset, secondary_tf, 100)
                regimes = multi_timeframe_regimes(
                    {self.timeframe: candles, secondary_tf: candles_4h},
                    vix=macro.get("vix", 20),
                )
                regime = regime_consensus(regimes)
            except Exception:
                regime = detect_regime(candles, vix=macro.get("vix", 20))

            # Emit RegimeUpdated
            await self._bus.publish("RegimeUpdated", request_id, {"regime": regime})

            # ── 3. Strategy selection ─────────────────────────────────────────
            # Use local strategy selector instead of legacy select_strategy
            strategy, reason, hurst, vol_pct = self._selector.select(MarketContext(
                asset=self.asset, timeframe=self.timeframe, candles=candles, current_price=price, regime=regime
            ))
            if self._strategy_override:
                from core.strategy.strategy_base import StrategyType
                strategy = STRATEGY_REGISTRY.get(StrategyType(self._strategy_override), strategy)
                reason = "User pinned override"

            # Emit StrategySelected
            await self._bus.publish("StrategySelected", request_id, {
                "strategy": strategy.strategy_type.value,
                "reason": reason,
                "hurst": hurst,
                "vol_percentile": vol_pct
            })

            # Emit Hypothesis
            hypothesis = self._selector.emit_hypothesis(strategy, MarketContext(
                asset=self.asset, timeframe=self.timeframe, candles=candles, current_price=price, regime=regime
            ), hurst, vol_pct)
            await self._bus.publish("HypothesisEmitted", request_id, hypothesis.__dict__ if hasattr(hypothesis, "__dict__") else hypothesis)

            # ── 4. Build context ──────────────────────────────────────────────
            ctx = MarketContext(
                asset=self.asset,
                timeframe=self.timeframe,
                candles=candles,
                current_price=price,
                order_book=ob,
                news_headlines=headlines,
                sentiment_raw=sentiment,
                macro_context=macro,
                portfolio_context={
                    "active_strategy": strategy.strategy_type.value,
                    "consecutive_losses": self._portfolio.consecutive_losses,
                },
                regime=regime,
                request_id=request_id,
                hypothesis=hypothesis
            )

            # ── 5. Agent analysis (parallel) ──────────────────────────────────
            agent_decisions = await asyncio.gather(
                self._tech.analyze(ctx),
                self._sent.analyze(ctx),
                self._quant.analyze(ctx),
                self._of.analyze(ctx),
            )
            
            # Emit ScreeningResult for each voting agent
            for d in agent_decisions:
                await self._bus.publish("ScreeningResult", request_id, d.to_dict())

            da_decision = await self._da.analyze(ctx)
            # Emit ScreeningResult for Devil's Advocate
            await self._bus.publish("ScreeningResult", request_id, da_decision.to_dict())

            # ── 6. Consensus ──────────────────────────────────────────────────
            signal = await self._meta.evaluate(
                asset=self.asset,
                request_id=request_id,
                regime=regime,
                decisions=list(agent_decisions),
                da_decision=da_decision,
                hypothesis=hypothesis,
                event_bus=self._bus
            )
            self._last_signal = signal
            
            # Emit VerdictReached
            await self._bus.publish("VerdictReached", request_id, signal.to_dict())

            # ── 7. Strategy filter ────────────────────────────────────────────
            strat_ok, strat_reason = strategy.accepts(signal, ctx)
            if signal.final_decision and not strat_ok:
                log.info("STRATEGY BLOCK — %s %s | strategy=%s | reason=%s",
                         self.asset, signal.action, strategy.strategy_type.value, strat_reason)

            # ── 8. Risk check ─────────────────────────────────────────────────
            # Always computed (even for HOLD/rejected signals) so callers can see
            # what would have happened; execution itself stays gated below.
            executed = False
            risk_result = self._risk.check(signal, self._portfolio, price)
            if signal.final_decision and strat_ok and not risk_result.is_tradeable():
                log.info("RISK BLOCK — %s %s | status=%s | reasons=%s",
                         self.asset, signal.action, risk_result.status.value,
                         risk_result.rejection_reasons)

            if signal.final_decision and strat_ok:
                # Emit SanitizationApplied
                await self._bus.publish("SanitizationApplied", request_id, {
                    "status": risk_result.status.value,
                    "approved_size_pct": risk_result.approved_position_size_pct,
                    "stop_loss_price": risk_result.stop_loss_price,
                    "take_profit_price": risk_result.take_profit_price,
                    "sanitization_diff": risk_result.sanitization_diff
                })

                # ── 9. Execution ──────────────────────────────────────────────
                if _execute and risk_result.is_tradeable():
                    size_mult = strategy.position_size_multiplier(signal, ctx)
                    risk_result.approved_position_size_usd *= size_mult
                    risk_result.approved_position_size_pct *= size_mult

                    log.info(
                        "EXECUTING — %s %s | size=₹%.0f (%.1f%%) | sl=%.2f tp=%.2f",
                        self.asset, signal.action,
                        risk_result.approved_position_size_usd,
                        risk_result.approved_position_size_pct * 100,
                        risk_result.stop_loss_price, risk_result.take_profit_price,
                    )
                    try:
                        await self._router.execute_bracket(signal, risk_result, price)
                        self._portfolio.open_trades += 1
                        executed = True
                        log.info("ORDER PLACED — %s %s ₹%.0f @ %.2f", self.asset, signal.action, risk_result.approved_position_size_usd, price)
                    except Exception as exc:
                        log.exception("EXECUTION ERROR — %s %s: %s", self.asset, signal.action, exc)

                    # Emit OrderPlaced
                    await self._bus.publish("OrderPlaced", request_id, {
                        "asset": self.asset,
                        "side": signal.action.value if signal.action else "HOLD",
                        "size_usd": risk_result.approved_position_size_usd,
                        "price": price
                    })
                elif _execute and not risk_result.is_tradeable():
                    log.info("EXEC SKIP (risk) — %s | status=%s reasons=%s", self.asset, risk_result.status.value, risk_result.rejection_reasons)

            # ── 10. Learning loop ─────────────────────────────────────────────
            if signal.final_decision and signal.action:
                for d in agent_decisions:
                    self._weights.record_prediction(
                        d.agent_name.value, d.signal.value, d.confidence, request_id
                    )

            # ── 11. Publish to event bus + alerts + journal ───────────────────
            cycle_ms = (time.perf_counter() - t0) * 1000
            
            # Emit FinalCall
            await self._bus.publish("FinalCall", request_id, signal.to_dict())

            persistence_tasks = [
                self._alerts.signal_generated(signal),
                self._journal.log_signal(signal, price),
            ]
            if self._repository is not None:
                persistence_tasks.append(
                    self._repository.record_signal(signal, self.timeframe, strategy.strategy_type.value)
                )
                persistence_tasks.append(self._repository.snapshot_portfolio(self._portfolio))
            await asyncio.gather(*persistence_tasks, return_exceptions=True)
            result = CycleResult(
                request_id=request_id,
                asset=self.asset,
                timestamp=time.time(),
                signal=signal,
                strategy_name=strategy.strategy_type.value,
                strategy_accepted=strat_ok,
                strategy_reason=strat_reason,
                executed=executed,
                cycle_ms=cycle_ms,
                current_price=price,
                risk_result=risk_result,
            )
            self._append_history(result)
            return result

        except Exception as e:
            result = CycleResult(
                request_id=request_id,
                asset=self.asset,
                timestamp=time.time(),
                signal=None,
                strategy_name="unknown",
                strategy_accepted=False,
                strategy_reason="",
                error=str(e),
                cycle_ms=(time.perf_counter() - t0) * 1000,
            )
            self._append_history(result)
            return result

    def _append_history(self, result: CycleResult) -> None:
        self._history.append(result)
        if len(self._history) > 500:
            self._history = self._history[-500:]

    @property
    def last_signal(self) -> TradeSignal | None:
        return self._last_signal

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    def recent_history(self, n: int = 10) -> list[dict]:
        return [r.to_dict() for r in self._history[-n:]]


class PortfolioOrchestrator:
    """
    Runs multiple Orchestrators in parallel — one per asset.
    Coordinates shared portfolio state and prevents exposure overlaps.
    """

    def __init__(
        self,
        watchlist: list[dict],   # [{"asset": "BTCUSDT", "timeframe": "1h"}, ...]
        data_provider: MarketDataProvider,
        news_feed: NewsFeed,
        portfolio: PortfolioState,
        router: SmartOrderRouter,
        alerts: AlertRouter,
        journal: TradeJournal,
        weights_manager: AdaptiveWeightManager,
        auto_execute: bool = False,
    ):
        self._portfolio = portfolio
        self._alerts = alerts
        self._orchestrators: list[Orchestrator] = []

        for item in watchlist:
            self._orchestrators.append(Orchestrator(
                asset=item["asset"],
                timeframe=item.get("timeframe", "1h"),
                data_provider=data_provider,
                news_feed=news_feed,
                portfolio=portfolio,
                router=router,
                alerts=alerts,
                journal=journal,
                weights_manager=weights_manager,
                auto_execute=auto_execute,
                strategy_override=item.get("strategy"),
            ))

    async def run_forever(self) -> None:
        tasks = [asyncio.create_task(o.run_forever()) for o in self._orchestrators]
        log.info("Portfolio orchestrator running %d watchers", len(tasks))
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            log.info("Portfolio orchestrator stopped")

    def stop_all(self) -> None:
        for o in self._orchestrators:
            o.stop()

    def status(self) -> list[dict]:
        return [
            {
                "asset": o.asset,
                "timeframe": o.timeframe,
                "cycles": o.cycle_count,
                "last_signal": o.last_signal.to_dict() if o.last_signal else None,
            }
            for o in self._orchestrators
        ]
