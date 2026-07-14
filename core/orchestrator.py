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

import numpy as np

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
from core.execution.options_router import OptionsRouter
from core.monitoring.alerts import AlertRouter
from core.monitoring.trade_journal import TradeJournal
from core.learning.adaptive_weights import AdaptiveWeightManager
from core.strategy.strategies import select_strategy, BaseStrategy
from core.streaming.kafka_bus import InMemoryBus, make_bus
from core.streaming.event_bus import EventBus
from core.strategy.selector import StrategySelector
from core.strategy.strategies import STRATEGY_REGISTRY
from core.persistence.repository import Repository
from core.data.instruments import INDEX_UNDERLYINGS as _INDEX_SYMBOLS_ORCH

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
        # Cache for index options context (PCR, ATM IV, IV skew, max pain, raw chain). 5-min TTL.
        self._opts_ctx: dict = {
            "pcr": -1.0, "atm_iv": -1.0, "iv_skew": 0.0, "max_pain": 0.0,
            "expiry": "", "oc": {}, "spot": 0.0, "ts": 0.0,
        }

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
        log.info("CYCLE START — %s execute=%s", self.asset, _execute)

        try:
            # ── 1. Fetch data (parallel) ──────────────────────────────────────
            candles, price = await asyncio.gather(
                self._data.get_candles(self.asset, self.timeframe, self._candle_limit),
                self._data.get_current_price(self.asset),
            )

            # Candle quality upgrade:
            # - Index instruments: ALWAYS use daily bars regardless of intraday
            #   count. Intraday hourly bars for NIFTY/BANKNIFTY span only 5 days
            #   and produce a noisy z-score; daily bars give a clean 100-day view
            #   that Quant can score at 90%+ confidence.
            # - Other assets: fall back to daily only when intraday < 60 bars.
            _MIN_CANDLES_NEEDED = 60
            _is_index = self.asset.upper() in _INDEX_SYMBOLS_ORCH
            _needs_daily = (_is_index and self.timeframe != "1d") or \
                           (len(candles) < _MIN_CANDLES_NEEDED and self.timeframe != "1d")
            if _needs_daily:
                try:
                    daily = await self._data.get_candles(self.asset, "1d", self._candle_limit)
                    if len(daily) >= (_MIN_CANDLES_NEEDED if not _is_index else 30):
                        log.info(
                            "%s: using %d daily bars (%s)",
                            self.asset, len(daily),
                            "index — daily preferred" if _is_index else f"only {len(candles)} {self.timeframe} candles",
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
                    asset=self.asset,
                )
                regime = regime_consensus(regimes)
            except Exception:
                regime = detect_regime(candles, vix=macro.get("vix", 20), asset=self.asset)

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
            # For index instruments: fetch PCR + ATM IV from option chain (5-min cache)
            opts_ctx = await self._fetch_options_context(price)
            if opts_ctx:
                macro["pcr"]      = opts_ctx["pcr"]
                macro["atm_iv"]   = opts_ctx["atm_iv"]
                macro["iv_skew"]  = opts_ctx.get("iv_skew", 0.0)
                macro["max_pain"] = opts_ctx.get("max_pain", 0.0)

            # iv_rank: prefer actual ATM IV / 20-day HV ratio; fall back to HV percentile
            vol_rank = self._compute_vol_rank(candles)
            if opts_ctx.get("atm_iv", -1) > 0 and vol_rank >= 0:
                closes = np.array([c.close for c in candles], dtype=float)
                closes = closes[closes > 0]
                rets = np.diff(closes) / closes[:-1] if len(closes) > 1 else np.array([])
                hv_20d = float(rets[-20:].std() * np.sqrt(252) * 100) if len(rets) >= 20 else 0.0
                if hv_20d > 0:
                    # IV/HV ratio: 1.0 = fair, >1.5 = expensive, <0.7 = cheap
                    iv_hv = opts_ctx["atm_iv"] / hv_20d
                    vol_rank = round(min(100.0, max(0.0, (iv_hv - 0.5) / 1.5 * 100)), 1)

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
                hypothesis=hypothesis,
                iv_rank=vol_rank,
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
                event_bus=self._bus,
                dynamic_weights=self._weights.get_weights() if self._weights else None
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
            # Determine options routing once — used by execution, logging, and learning loop.
            # MCX commodities always route as futures even in options mode.
            from core.data.instruments import scrip_master
            use_options = (
                settings.trade_mode == "options"
                and hasattr(self._router._broker, "_dhan")
                and not scrip_master.is_mcx(self.asset)
            )
            risk_result = self._risk.check(signal, self._portfolio, price)
            if signal.final_decision and strat_ok and not risk_result.is_tradeable():
                log.info("RISK BLOCK — %s %s | status=%s | reasons=%s",
                         self.asset, signal.action, risk_result.status.value,
                         risk_result.rejection_reasons)

            if signal.final_decision and strat_ok:

                # Emit SanitizationApplied — show premium-based SL/TP for options
                san_payload = {
                    "status": risk_result.status.value,
                    "approved_size_pct": risk_result.approved_position_size_pct,
                    "stop_loss_price": risk_result.stop_loss_price,
                    "take_profit_price": risk_result.take_profit_price,
                    "sanitization_diff": risk_result.sanitization_diff,
                }
                if use_options:
                    san_payload["options_sl_pct"] = settings.options_sl_pct
                await self._bus.publish("SanitizationApplied", request_id, san_payload)

                # ── 9. Execution ──────────────────────────────────────────────
                log.info("EXEC GATE — %s fd=%s strat_ok=%s tradeable=%s execute=%s",
                         self.asset, signal.final_decision, strat_ok,
                         risk_result.is_tradeable(), _execute)
                if _execute and risk_result.is_tradeable():
                    size_mult = strategy.position_size_multiplier(signal, ctx)
                    risk_result.approved_position_size_usd *= size_mult
                    risk_result.approved_position_size_pct *= size_mult

                    if use_options:
                        log.info(
                            "EXECUTING — %s %s | size=₹%.0f (%.1f%%) | sl=%d%% of premium | tp=2× risk (1:2 R:R)",
                            self.asset, signal.action,
                            risk_result.approved_position_size_usd,
                            risk_result.approved_position_size_pct * 100,
                            int(settings.options_sl_pct * 100),
                        )
                    else:
                        log.info(
                            "EXECUTING — %s %s | size=₹%.0f (%.1f%%) | sl=₹%.2f | tp=₹%.2f",
                            self.asset, signal.action,
                            risk_result.approved_position_size_usd,
                            risk_result.approved_position_size_pct * 100,
                            risk_result.stop_loss_price, risk_result.take_profit_price,
                        )
                    try:
                        if use_options:
                            opt_router = OptionsRouter(
                                broker=self._router._broker,
                                market_data=self._data,
                                otm_strikes=settings.options_otm_strikes,
                                min_days_to_expiry=settings.options_min_days_to_expiry,
                                sl_pct=settings.options_sl_pct,
                            )
                            # Pass cached chain so options router skips the re-fetch
                            # (avoids Dhan rate-limit burst from back-to-back option_chain calls)
                            prefetched = opts_ctx if opts_ctx.get("oc") else None
                            result = await opt_router.execute(signal, risk_result, price, prefetched=prefetched)
                            tp_str = f"TP₹{result['tp_premium']:.2f}" if result.get("tp_premium") else "TP=signal-exit"
                            log.info(
                                "OPTIONS PLACED — %s %s %s | %d lots @ ₹%.2f | SL₹%.2f | %s | R:R %s | cost₹%.0f",
                                self.asset, result["option"], result["expiry"],
                                result["lots"], result["entry_premium"],
                                result["sl_premium"], tp_str, result.get("rr", "1:2"), result["cost"],
                            )
                        else:
                            await self._router.execute_bracket(signal, risk_result, price)
                        self._portfolio.open_trades += 1
                        # Update in-memory exposure immediately so the risk engine
                        # blocks over-allocation before the position monitor re-syncs.
                        self._portfolio.positions[self.asset] = risk_result.approved_position_size_usd
                        self._portfolio.cash = max(
                            0.0, self._portfolio.cash - risk_result.approved_position_size_usd
                        )
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
                trade_type = "options" if use_options else "equity"
                for d in agent_decisions:
                    self._weights.record_prediction(
                        d.agent_name.value, d.signal.value, d.confidence,
                        request_id, asset=self.asset, trade_type=trade_type,
                    )

            # ── 11. Publish to event bus + alerts + journal ───────────────────
            cycle_ms = (time.perf_counter() - t0) * 1000
            
            # Emit FinalCall
            await self._bus.publish("FinalCall", request_id, signal.to_dict())

            persistence_tasks = [
                self._alerts.signal_generated(signal, risk_result),
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

    async def _fetch_options_context(self, spot: float) -> dict:
        """
        For index instruments: fetch PCR (put-call OI ratio) and ATM implied volatility
        from the nearest weekly option chain. Results are cached for 5 minutes to avoid
        hammering the Dhan rate limit across 5 index orchestrators.

        Returns dict with keys: pcr (float), atm_iv (float %).
        Returns empty dict on any failure or for non-index instruments.
        """
        from core.data.instruments import scrip_master
        if not scrip_master.is_index(self.asset):
            return {}
        broker = getattr(self._router, "_broker", None)
        if not broker or not hasattr(broker, "_dhan"):
            return {}

        if time.time() - self._opts_ctx["ts"] < 300:  # 5-min cache
            return self._opts_ctx

        try:
            inst = scrip_master.resolve(self.asset)
            if not inst:
                return {}

            loop = asyncio.get_event_loop()
            raw_exp = await loop.run_in_executor(
                None, lambda: broker._dhan.expiry_list(int(inst.security_id), inst.exchange)
            )
            from datetime import date as _date
            expiries = (raw_exp.get("data", {}) or {}).get("data", []) or []
            expiry = None
            for exp_str in sorted(expiries):
                try:
                    if (_date.fromisoformat(exp_str[:10]) - _date.today()).days >= 2:
                        expiry = exp_str[:10]
                        break
                except ValueError:
                    continue
            if not expiry:
                return {}

            raw_chain = await loop.run_in_executor(
                None, lambda: broker._dhan.option_chain(int(inst.security_id), inst.exchange, expiry)
            )
            oc = (raw_chain.get("data", {}) or {}).get("data", {}).get("oc", {}) or {}
            if not oc:
                return {}

            # Find ATM strike
            strikes = sorted(float(k) for k in oc.keys())
            atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
            atm_strike = strikes[atm_idx]

            total_ce_oi = 0
            total_pe_oi = 0
            atm_iv = -1.0

            for strike_str, legs in oc.items():
                ce = legs.get("ce", {}) or {}
                pe = legs.get("pe", {}) or {}
                total_ce_oi += int(ce.get("oi", 0) or 0)
                total_pe_oi += int(pe.get("oi", 0) or 0)
                if abs(float(strike_str) - atm_strike) < 1.0:
                    iv = float(pe.get("implied_volatility", 0) or 0)
                    if iv > 0:
                        atm_iv = iv

            pcr = round(total_pe_oi / total_ce_oi, 3) if total_ce_oi > 0 else -1.0

            # IV Skew: OTM put IV − OTM call IV (3 strikes out from ATM)
            # Positive = put IV elevated (market pricing downside) → bearish
            # Negative = call IV elevated (market pricing upside) → bullish
            otm_call_strike = strikes[min(atm_idx + 3, len(strikes) - 1)]
            otm_put_strike  = strikes[max(atm_idx - 3, 0)]
            call_otm_iv = put_otm_iv = 0.0
            for k, v in oc.items():
                s = float(k)
                if abs(s - otm_call_strike) < 1.0:
                    call_otm_iv = float((v.get("ce") or {}).get("implied_volatility", 0) or 0)
                if abs(s - otm_put_strike) < 1.0:
                    put_otm_iv = float((v.get("pe") or {}).get("implied_volatility", 0) or 0)
            iv_skew = round(put_otm_iv - call_otm_iv, 2) if put_otm_iv > 0 and call_otm_iv > 0 else 0.0

            # Max pain: strike that minimises total options value at expiry
            # (where options sellers collectively suffer least → index tends to pin here)
            min_total_pain = None
            max_pain_strike = atm_strike
            for potential_price in strikes:
                call_pain = sum(
                    max(0.0, potential_price - float(k)) * int((v.get("ce") or {}).get("oi", 0) or 0)
                    for k, v in oc.items()
                )
                put_pain = sum(
                    max(0.0, float(k) - potential_price) * int((v.get("pe") or {}).get("oi", 0) or 0)
                    for k, v in oc.items()
                )
                total_pain = call_pain + put_pain
                if min_total_pain is None or total_pain < min_total_pain:
                    min_total_pain = total_pain
                    max_pain_strike = potential_price

            self._opts_ctx = {
                "pcr": pcr, "atm_iv": round(atm_iv, 2),
                "iv_skew": iv_skew, "max_pain": round(max_pain_strike, 1),
                "expiry": expiry, "oc": oc, "spot": spot,
                "ts": time.time(),
            }
            log.info(
                "%s OPTIONS CTX — PCR=%.2f ATM_IV=%.1f%% iv_skew=%.1f max_pain=%.0f expiry=%s",
                self.asset, pcr, atm_iv, iv_skew, max_pain_strike, expiry,
            )
            return self._opts_ctx
        except Exception as exc:
            log.debug("Options context fetch failed for %s: %s", self.asset, exc)
            return {}

    @staticmethod
    def _compute_vol_rank(candles) -> float:
        """
        HV percentile over a rolling 60-day window — used as iv_rank proxy.
        Returns 0–100 (where current 20-day HV sits vs its own recent range).
        Returns -1.0 when there is insufficient data.
        High rank (>75) → options expensive, IV crush risk on option buys.
        Low rank (<25) → options cheap, favorable for buying options.
        """
        if len(candles) < 40:
            return -1.0
        closes = np.array([c.close for c in candles], dtype=float)
        closes = closes[closes > 0]   # drop zero-price candles (corrupted data)
        if len(closes) < 21:
            return -1.0
        rets = np.diff(closes) / closes[:-1]
        if len(rets) < 20:
            return -1.0
        hvs = [
            float(rets[i:i + 20].std() * np.sqrt(252) * 100)
            for i in range(len(rets) - 19)
        ]
        if not hvs:
            return -1.0
        current_hv = hvs[-1]
        hv_min, hv_max = min(hvs), max(hvs)
        if hv_max == hv_min:
            return 50.0
        return round((current_hv - hv_min) / (hv_max - hv_min) * 100, 1)

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
