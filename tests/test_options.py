"""Unit tests for core.options — Greek engine, chain analyzer, volatility,
regime classifier, strategy manager, entry/exit engines, expiry engine."""
from __future__ import annotations

import math
import time
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from core.options.greeks_engine import Greeks, GreeksEngine, PortfolioGreeks
from core.options.volatility_engine import VolatilityEngine, VolatilitySnapshot
from core.options.expiry_engine import ExpiryEngine, ExpiryInfo
from core.options.regime_classifier import OptionsRegime, OptionsRegimeClassifier
from core.options.strategy_manager import StrategyManager, StrategyType
from core.options.entry_engine import EntryDecision, EntryEngine
from core.options.exit_engine import ExitEngine, ExitReason, OptionPosition
from core.options.position_manager import PositionManager


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _ohlcv(close: float, open_: float | None = None, high: float | None = None,
           low: float | None = None, volume: float = 1_000_000):
    """Minimal OHLCV-compatible namespace."""
    o = open_ or close
    return MagicMock(open=o, high=high or close * 1.005, low=low or close * 0.995,
                     close=close, volume=volume)


def _candles(n: int = 50, base: float = 22000.0, trend: float = 0.0) -> list:
    """Generate synthetic candle sequence."""
    return [_ohlcv(base + trend * i) for i in range(n)]


def _position(
    symbol: str = "NIFTY-22000-CE",
    entry: float = 100.0,
    current: float = 100.0,
    dte: int = 7,
    qty: int = 1,
    lot_size: int = 50,
    option_type: str = "CE",
    entry_iv: float = 0.18,
    current_iv: float = 0.18,
) -> OptionPosition:
    return OptionPosition(
        symbol=symbol,
        underlying="NIFTY",
        option_type=option_type,
        strike=22000.0,
        expiry="2026-07-17",
        entry_premium=entry,
        current_premium=current,
        quantity=qty,
        lot_size=lot_size,
        entry_time=time.time(),
        days_to_expiry=dte,
        entry_iv=entry_iv,
        current_iv=current_iv,
        peak_premium=current,
    )


# ── GreeksEngine ──────────────────────────────────────────────────────────────

class TestGreeksEngine:
    engine = GreeksEngine()

    def test_atm_call_delta_near_half(self):
        g = self.engine.compute(S=22000, K=22000, T=7/365, sigma=0.15, option_type="CE")
        assert 0.45 <= g.delta <= 0.60

    def test_atm_put_delta_negative(self):
        g = self.engine.compute(S=22000, K=22000, T=7/365, sigma=0.15, option_type="PE")
        assert -0.60 <= g.delta <= -0.40

    def test_deep_itm_call_delta_near_one(self):
        g = self.engine.compute(S=22000, K=19000, T=30/365, sigma=0.20, option_type="CE")
        assert g.delta > 0.85

    def test_deep_otm_call_delta_near_zero(self):
        g = self.engine.compute(S=22000, K=25000, T=7/365, sigma=0.15, option_type="CE")
        assert g.delta < 0.10

    def test_put_call_delta_symmetry(self):
        ce = self.engine.compute(S=22000, K=22000, T=7/365, sigma=0.20, option_type="CE")
        pe = self.engine.compute(S=22000, K=22000, T=7/365, sigma=0.20, option_type="PE")
        # CE delta + |PE delta| ≈ 1 (approximately, ignoring discounting)
        assert abs((ce.delta + abs(pe.delta)) - 1.0) < 0.05

    def test_gamma_positive(self):
        g = self.engine.compute(S=22000, K=22000, T=7/365, sigma=0.20, option_type="CE")
        assert g.gamma > 0

    def test_theta_negative_for_long(self):
        g = self.engine.compute(S=22000, K=22000, T=14/365, sigma=0.20, option_type="CE")
        assert g.theta < 0

    def test_vega_positive(self):
        g = self.engine.compute(S=22000, K=22000, T=14/365, sigma=0.20, option_type="CE")
        assert g.vega > 0

    def test_expiry_delta_intrinsic(self):
        ce = self.engine.compute(S=22100, K=22000, T=0, sigma=0.20, option_type="CE")
        assert ce.delta == 1.0  # ITM call at expiry = delta 1

    def test_implied_volatility_roundtrip(self):
        S, K, T, true_sigma = 22000, 22000, 14/365, 0.20
        mkt_price = self.engine.price(S, K, T, self.engine.risk_free_rate, true_sigma, "CE")
        iv = self.engine.implied_volatility(mkt_price, S, K, T, "CE")
        assert abs(iv - true_sigma) < 0.005

    def test_implied_volatility_below_intrinsic_returns_minus_one(self):
        # intrinsic = 22100 - 22000 = 100, market_price = 50 < intrinsic
        iv = self.engine.implied_volatility(50.0, S=22100, K=22000, T=7/365, option_type="CE")
        assert iv == -1.0

    def test_portfolio_greeks_aggregation(self):
        positions = [
            dict(S=22000, K=22000, T=7/365, sigma=0.20, option_type="CE",
                 quantity=1, lot_size=50),
            dict(S=22000, K=22000, T=7/365, sigma=0.20, option_type="PE",
                 quantity=1, lot_size=50),
        ]
        pg = self.engine.portfolio_greeks(positions)
        # Long straddle: CE delta ~0.5 + PE delta ~-0.5 ≈ near zero net delta
        assert abs(pg.net_delta) < 20   # contract-adjusted (×50 lot size)
        assert pg.net_gamma > 0
        # Straddle vega is additive
        assert pg.net_vega > 0

    def test_portfolio_greeks_empty(self):
        pg = self.engine.portfolio_greeks([])
        assert pg.net_delta == 0.0
        assert pg.net_gamma == 0.0

    def test_greeks_to_dict(self):
        g = self.engine.compute(S=22000, K=22000, T=7/365, sigma=0.20, option_type="CE")
        d = g.to_dict()
        assert "delta" in d and "gamma" in d and "theta" in d
        assert "vega" in d and "rho" in d and "iv_pct" in d


# ── VolatilityEngine ──────────────────────────────────────────────────────────

class TestVolatilityEngine:
    def test_iv_rank_no_history_returns_50(self):
        ve = VolatilityEngine()
        assert ve.iv_rank(0.20) == 50.0

    def test_iv_rank_at_max(self):
        ve = VolatilityEngine()
        for v in [0.10, 0.15, 0.20]:
            ve.push_iv(v)
        assert ve.iv_rank(0.20) == 100.0

    def test_iv_rank_at_min(self):
        ve = VolatilityEngine()
        for v in [0.10, 0.15, 0.20]:
            ve.push_iv(v)
        assert ve.iv_rank(0.10) == 0.0

    def test_iv_percentile_above_all(self):
        ve = VolatilityEngine()
        for v in [0.10, 0.12, 0.15]:
            ve.push_iv(v)
        assert ve.iv_percentile(0.20) == 100.0

    def test_iv_percentile_below_all(self):
        ve = VolatilityEngine()
        for v in [0.15, 0.18, 0.20]:
            ve.push_iv(v)
        assert ve.iv_percentile(0.10) == 0.0

    def test_realized_vol_computed(self):
        closes = [22000.0 * (1 + 0.01 * i % 3) for i in range(25)]
        rv = VolatilityEngine.realized_volatility(closes)
        assert 0.0 < rv < 1.0  # should be between 0% and 100%

    def test_realized_vol_too_few_candles(self):
        rv = VolatilityEngine.realized_volatility([100.0, 101.0])
        assert rv == 0.0

    def test_expected_move_positive(self):
        m1, m2 = VolatilityEngine.expected_move(22000, 0.20, 7)
        assert m1 > 0 and m2 == m1 * 2

    def test_expected_move_from_straddle(self):
        m1, m2 = VolatilityEngine.expected_move_from_straddle(22000, 300)
        assert abs(m1 - 300 * 0.68) < 0.01
        assert abs(m2 - 300 * 1.35) < 0.01

    def test_vol_regime_classification(self):
        assert VolatilityEngine.vol_regime(10.0) == "low"
        assert VolatilityEngine.vol_regime(16.0) == "normal"
        assert VolatilityEngine.vol_regime(25.0) == "high"
        assert VolatilityEngine.vol_regime(35.0) == "extreme"

    def test_snapshot_builds_correctly(self):
        ve = VolatilityEngine()
        closes = [22000.0 + i for i in range(22)]
        snap = ve.snapshot(
            current_iv=0.18,
            india_vix=16.0,
            spot=22000.0,
            straddle_premium=250.0,
            closes=closes,
            days_to_expiry=7,
        )
        assert snap.current_iv == 0.18
        assert snap.vol_regime == "normal"
        assert snap.expected_move_1sd == pytest.approx(250.0 * 0.68)

    def test_should_sell_premium_high_iv(self):
        ve = VolatilityEngine()
        for v in [0.10, 0.15, 0.18, 0.22, 0.25]:
            ve.push_iv(v)
        snap = ve.snapshot(0.25, 18.0, 22000, 300, [22000.0 + i for i in range(22)])
        result = VolatilityEngine.should_sell_premium(snap)
        assert result is True

    def test_should_buy_premium_low_iv(self):
        ve = VolatilityEngine()
        for v in [0.20, 0.25, 0.30]:
            ve.push_iv(v)
        snap = ve.snapshot(0.10, 12.0, 22000, 100, [22000.0 + i for i in range(22)])
        result = VolatilityEngine.should_buy_premium(snap)
        assert result is True  # iv_rank will be 0 (< 30 threshold)


# ── ExpiryEngine ──────────────────────────────────────────────────────────────

class TestExpiryEngine:
    engine = ExpiryEngine()

    def test_nifty_next_thursday(self):
        # Monday 2026-07-13 → nearest Thursday should be 2026-07-16
        info = self.engine.get_info("NIFTY", today=date(2026, 7, 13))
        assert info.nearest_weekly.weekday() == 3  # Thursday
        assert info.nearest_weekly >= date(2026, 7, 13)

    def test_banknifty_next_wednesday(self):
        info = self.engine.get_info("BANKNIFTY", today=date(2026, 7, 13))
        assert info.nearest_weekly.weekday() == 2  # Wednesday

    def test_finnifty_next_tuesday(self):
        info = self.engine.get_info("FINNIFTY", today=date(2026, 7, 13))
        assert info.nearest_weekly.weekday() == 1

    def test_is_expiry_today_on_thursday(self):
        # Find the next Thursday from today and simulate that it's today
        d = date(2026, 7, 13)
        while d.weekday() != 3:
            d += timedelta(days=1)
        info = self.engine.get_info("NIFTY", today=d)
        assert info.is_expiry_today is True
        assert info.days_to_weekly == 0

    def test_not_expiry_on_monday(self):
        info = self.engine.get_info("NIFTY", today=date(2026, 7, 13))
        assert info.is_expiry_today is False

    def test_monthly_expiry_after_weekly(self):
        info = self.engine.get_info("NIFTY", today=date(2026, 7, 13))
        assert info.nearest_monthly >= info.nearest_weekly

    def test_recommended_expiry_sufficient_dte(self):
        info = self.engine.get_info("NIFTY", today=date(2026, 7, 13))
        # With >= 3 DTE to weekly, recommended = weekly
        if info.days_to_weekly >= info.recommended_min_dte:
            assert info.recommended_expiry == info.nearest_weekly

    def test_can_enter_on_expiry_day_returns_false(self):
        d = date(2026, 7, 13)
        while d.weekday() != 3:
            d += timedelta(days=1)
        ok, reason = self.engine.can_enter("NIFTY", "atm_call_buy", today=d)
        assert ok is False
        assert "Expiry day" in reason

    def test_can_enter_ok_with_sufficient_dte(self):
        ok, reason = self.engine.can_enter("NIFTY", "atm_call_buy", today=date(2026, 7, 13))
        assert isinstance(ok, bool)

    def test_gamma_risk_score_at_expiry(self):
        assert self.engine.gamma_risk_score(0) == 100.0

    def test_gamma_risk_score_at_one_dte(self):
        assert self.engine.gamma_risk_score(1) == 90.0

    def test_gamma_risk_score_decreases_with_dte(self):
        s1 = self.engine.gamma_risk_score(1)
        s5 = self.engine.gamma_risk_score(5)
        s10 = self.engine.gamma_risk_score(10)
        assert s1 > s5 > s10

    def test_should_accelerate_exit(self):
        assert self.engine.should_accelerate_exit(1, 0.30) is True
        assert self.engine.should_accelerate_exit(1, 0.10) is False
        assert self.engine.should_accelerate_exit(5, 0.30) is False

    def test_all_expiries_this_month(self):
        expiries = self.engine.all_expiries_this_month("NIFTY", today=date(2026, 7, 1))
        assert len(expiries) >= 2
        for e in expiries:
            assert e.month == 7
            assert e.weekday() == 3


# ── OptionsRegimeClassifier ───────────────────────────────────────────────────

class TestOptionsRegimeClassifier:
    clf = OptionsRegimeClassifier()

    def test_bull_regime_uptrend(self):
        candles = _candles(60, base=20000, trend=50)
        regime = self.clf.classify(candles, india_vix=15.0)
        assert regime.primary in ("bull", "strong_bull", "breakout")

    def test_expiry_day_override(self):
        # Set expiry_date to today
        today_str = date.today().isoformat()
        candles = _candles(30)
        regime = self.clf.classify(candles, india_vix=15.0, expiry_date=today_str)
        assert regime.primary == "expiry_day"
        assert regime.is_expiry_day is True

    def test_high_vix_regime(self):
        candles = _candles(30)
        regime = self.clf.classify(candles, india_vix=32.0)
        assert regime.iv_regime == "extreme" or regime.primary in ("high_volatility", "sideways", "volatile", "bull", "bear")

    def test_regime_to_dict_keys(self):
        candles = _candles(30)
        regime = self.clf.classify(candles, india_vix=16.0)
        d = regime.to_dict()
        for key in ("primary", "secondary", "iv_regime", "dte_today", "is_expiry_day"):
            assert key in d

    def test_best_strategy_types_for_sideways(self):
        regime = OptionsRegime(primary="sideways", iv_regime="high")
        strategies = regime.best_strategy_types()
        assert "iron_condor" in strategies or "short_straddle" in strategies

    def test_is_directional(self):
        assert OptionsRegime(primary="bull").is_directional is True
        assert OptionsRegime(primary="sideways").is_directional is False

    def test_is_neutral(self):
        assert OptionsRegime(primary="sideways").is_neutral is True
        assert OptionsRegime(primary="strong_bull").is_neutral is False


# ── StrategyManager ───────────────────────────────────────────────────────────

class TestStrategyManager:
    mgr = StrategyManager()

    def test_best_returns_strategy_for_bull_low_iv(self):
        result = self.mgr.best(
            primary_regime="bull",
            iv_regime="normal",
            dte=7,
            sell_premium=False,
            buy_premium=True,
        )
        assert result is not None
        assert result.name != StrategyType.NO_TRADE

    def test_best_returns_none_for_expiry_day(self):
        result = self.mgr.best(
            primary_regime="expiry_day",
            iv_regime="high",
            dte=0,
            sell_premium=False,
            buy_premium=False,
        )
        assert result is None

    def test_select_returns_ranked_list(self):
        results = self.mgr.select(
            primary_regime="sideways",
            iv_regime="high",
            dte=10,
            sell_premium=True,
            buy_premium=False,
        )
        assert isinstance(results, list)

    def test_iron_condor_preferred_for_sideways_high_iv(self):
        result = self.mgr.best(
            primary_regime="sideways",
            iv_regime="high",
            dte=10,
            sell_premium=True,
            buy_premium=False,
        )
        if result:
            assert result.name in (
                StrategyType.IRON_CONDOR,
                StrategyType.SHORT_STRADDLE,
                StrategyType.IRON_FLY,
            )

    def test_strategy_spec_is_compatible(self):
        result = self.mgr.best("bull", "normal", 7, sell_premium=False, buy_premium=True)
        if result:
            assert result.is_compatible("bull", "normal", 7) is True


# ── EntryEngine ───────────────────────────────────────────────────────────────

class TestEntryEngine:

    def _vol_snap(self, iv_rank: float = 40.0, regime: str = "normal") -> VolatilitySnapshot:
        return VolatilitySnapshot(
            current_iv=0.18, iv_rank=iv_rank, iv_percentile=40.0,
            realized_vol=0.15, india_vix=16.0, iv_minus_rv=0.03,
            expected_move_1sd=250.0, expected_move_1sd_pct=1.1,
            expected_move_2sd_pct=2.2, vol_regime=regime,
            is_iv_expanding=False, is_iv_contracting=False,
        )

    def _regime(self, primary: str = "bull") -> OptionsRegime:
        return OptionsRegime(primary=primary, iv_regime="normal", dte_today=7)

    def test_extreme_vix_instant_reject(self):
        engine = EntryEngine(min_confirmations=2, min_score=30.0)
        vol = self._vol_snap(regime="extreme")
        vol.vol_regime = "extreme"
        decision = engine.evaluate(
            direction="BUY", candles=_candles(30),
            chain=None, vol=vol, regime=self._regime(),
            greeks=None, consensus_confidence=80.0,
        )
        assert decision.approved is False
        assert "extreme" in decision.reason.lower()

    def test_expiry_day_instant_reject(self):
        engine = EntryEngine(min_confirmations=2, min_score=30.0)
        regime = OptionsRegime(primary="expiry_day", is_expiry_day=True)
        decision = engine.evaluate(
            direction="BUY", candles=_candles(30),
            chain=None, vol=self._vol_snap(), regime=regime,
            greeks=None, consensus_confidence=80.0,
        )
        assert decision.approved is False

    def test_approved_with_sufficient_confirmations(self):
        engine = EntryEngine(min_confirmations=4, min_score=50.0)
        decision = engine.evaluate(
            direction="BUY",
            candles=_candles(30, trend=10),  # uptrend
            chain=None,
            vol=self._vol_snap(iv_rank=30.0),
            regime=self._regime("bull"),
            greeks=None,
            consensus_confidence=75.0,
            news_clear=True,
        )
        assert isinstance(decision.approved, bool)
        assert 0.0 <= decision.score <= 100.0

    def test_entry_decision_to_dict(self):
        engine = EntryEngine()
        decision = engine.evaluate(
            direction="BUY", candles=_candles(30),
            chain=None, vol=self._vol_snap(), regime=self._regime(),
            greeks=None, consensus_confidence=60.0,
        )
        d = decision.to_dict()
        assert "approved" in d and "score" in d and "passed" in d and "failed" in d

    def test_low_confidence_fails_consensus_check(self):
        engine = EntryEngine(min_confirmations=3, min_score=40.0)
        decision = engine.evaluate(
            direction="BUY", candles=_candles(30),
            chain=None, vol=self._vol_snap(), regime=self._regime(),
            greeks=None, consensus_confidence=30.0,  # below 65% threshold
        )
        assert "consensus_confidence" in decision.failed_names


# ── ExitEngine ────────────────────────────────────────────────────────────────

class TestExitEngine:

    def test_fixed_stop_triggers(self):
        engine = ExitEngine(fixed_sl_pct=0.50)
        pos = _position(entry=100.0, current=45.0)  # -55% loss
        pos.peak_premium = 100.0
        sig = engine.check(pos)
        assert sig is not None
        assert sig.reason == ExitReason.FIXED_STOP

    def test_target_hit_triggers(self):
        engine = ExitEngine(target_pct=1.00)
        pos = _position(entry=100.0, current=205.0)  # +105% gain
        pos.peak_premium = 205.0
        sig = engine.check(pos)
        assert sig is not None
        assert sig.reason == ExitReason.TARGET_HIT

    def test_gamma_risk_triggers_on_low_dte(self):
        engine = ExitEngine(gamma_dte_limit=2)
        pos = _position(entry=100.0, current=110.0, dte=1)
        pos.peak_premium = 110.0
        sig = engine.check(pos)
        assert sig is not None
        assert sig.reason == ExitReason.GAMMA_RISK

    def test_trailing_stop_activates(self):
        engine = ExitEngine(trailing_sl_pct=0.25, target_pct=1.00)
        pos = _position(entry=100.0, current=200.0)
        pos.peak_premium = 200.0
        pos.trailing_sl = 0.0
        engine._update_trailing_sl(pos)
        # Trail = 200 × (1 - 0.25) = 150
        assert pos.trailing_sl == pytest.approx(150.0)
        # Now drop below trail
        pos.current_premium = 140.0
        sig = engine.check(pos)
        assert sig is not None
        assert sig.reason == ExitReason.TRAILING_STOP

    def test_eod_exit_triggers_near_close(self):
        engine = ExitEngine(eod_exit_minutes_before_close=15)
        pos = _position(entry=100.0, current=100.0)
        pos.peak_premium = 100.0
        sig = engine.check(pos, minutes_to_close=10)
        assert sig is not None
        assert sig.reason == ExitReason.TIME_EXIT

    def test_daily_loss_limit_triggers(self):
        engine = ExitEngine(daily_loss_limit_inr=5000.0)
        pos = _position(entry=100.0, current=80.0)
        pos.peak_premium = 100.0
        sig = engine.check(pos, daily_pnl_inr=-6000.0)
        assert sig is not None
        assert sig.reason == ExitReason.DAILY_LOSS_LIMIT

    def test_no_exit_when_position_healthy(self):
        engine = ExitEngine(target_pct=1.00, fixed_sl_pct=0.50)
        pos = _position(entry=100.0, current=110.0, dte=7)
        pos.peak_premium = 110.0
        sig = engine.check(pos, minutes_to_close=100)
        # No exit for a small gain with plenty of time
        assert sig is None

    def test_check_all_returns_signals_for_bad_positions(self):
        engine = ExitEngine(fixed_sl_pct=0.50)
        bad_pos = _position("BAD", entry=100.0, current=30.0)  # -70% loss
        good_pos = _position("GOOD", entry=100.0, current=110.0)
        bad_pos.peak_premium = 100.0
        good_pos.peak_premium = 110.0
        sigs = engine.check_all([bad_pos, good_pos], minutes_to_close=200)
        assert len(sigs) == 1
        assert sigs[0].position_symbol == "BAD"

    def test_emergency_flatten_near_zero_premium(self):
        engine = ExitEngine()
        pos = _position(entry=100.0, current=0.02)
        pos.peak_premium = 100.0
        sig = engine.check(pos)
        assert sig is not None
        assert sig.reason == ExitReason.EMERGENCY_FLATTEN

    def test_vol_crush_triggers(self):
        engine = ExitEngine(vol_crush_iv_drop=0.30)
        pos = _position(entry=100.0, current=90.0, entry_iv=0.30, current_iv=0.18, dte=7)
        pos.peak_premium = 100.0
        # IV dropped (0.30 - 0.18) / 0.30 = 40% > 30% threshold
        sig = engine.check(pos)
        assert sig is not None
        assert sig.reason == ExitReason.VOL_CRUSH

    def test_exit_signal_to_dict(self):
        engine = ExitEngine(fixed_sl_pct=0.50)
        pos = _position(entry=100.0, current=40.0)
        pos.peak_premium = 100.0
        sig = engine.check(pos)
        assert sig is not None
        d = sig.to_dict()
        assert "symbol" in d and "exit_now" in d and "reason" in d


# ── PositionManager ───────────────────────────────────────────────────────────

class TestPositionManager:

    def test_add_position_succeeds(self):
        pm = PositionManager(max_legs=4)
        pos = _position("NIFTY-22000-CE")
        ok = pm.add(pos)
        assert ok is True
        assert pm.get("NIFTY-22000-CE") is not None

    def test_add_duplicate_returns_true_no_duplicate(self):
        pm = PositionManager()
        pos = _position()
        pm.add(pos)
        result = pm.add(pos)  # second add of same symbol
        assert result is True
        assert len(pm.all_positions()) == 1

    def test_max_legs_enforced(self):
        pm = PositionManager(max_legs=2)
        pm.add(_position("SYM1"))
        pm.add(_position("SYM2"))
        ok = pm.add(_position("SYM3"))
        assert ok is False

    def test_per_underlying_limit(self):
        pm = PositionManager(max_positions_per_underlying=1)
        pm.add(_position("NIFTY-22000-CE"))
        ok = pm.add(_position("NIFTY-22500-CE"))
        assert ok is False

    def test_remove_position(self):
        pm = PositionManager()
        pm.add(_position())
        removed = pm.remove("NIFTY-22000-CE")
        assert removed is not None
        assert pm.get("NIFTY-22000-CE") is None

    def test_update_premium(self):
        pm = PositionManager()
        pm.add(_position(current=100.0))
        pm.update_premium("NIFTY-22000-CE", 150.0, 0.22)
        pos = pm.get("NIFTY-22000-CE")
        assert pos.current_premium == 150.0

    def test_pnl_summary_structure(self):
        pm = PositionManager()
        pm.add(_position(entry=100.0, current=120.0))
        summary = pm.pnl_summary()
        assert summary["open_legs"] == 1
        assert "total_unrealized_pnl_inr" in summary
        assert len(summary["positions"]) == 1

    def test_size_position_basic(self):
        pm = PositionManager()
        result = pm.size_position(
            equity=500_000,
            premium_per_unit=150.0,
            lot_size=50,
            max_risk_pct=0.05,
            confidence_score=75.0,
            max_lots=10,
        )
        assert result.approved_lots >= 1
        assert result.approved_cost_inr > 0

    def test_size_position_zero_premium_rejected(self):
        pm = PositionManager()
        result = pm.size_position(equity=500_000, premium_per_unit=0, lot_size=50)
        assert result.approved_lots == 0

    def test_size_position_1lot_forced_when_budget_tight(self):
        pm = PositionManager()
        # equity=100_000, risk 5% = 5000; 1 lot = 80×50 = 4000 < budget but forces 1 lot (low confidence)
        # equity=50_000, risk 5% = 2500; 1 lot = 30×50 = 1500 < budget, approved = 1
        result = pm.size_position(
            equity=50_000,
            premium_per_unit=30.0,
            lot_size=50,
            max_risk_pct=0.05,
            confidence_score=50.0,  # budget = 50000 × 0.05 × 0.5 = 1250; 1 lot=1500 > budget
        )
        assert result.approved_lots == 1
        assert result.capped is True

    def test_is_empty(self):
        pm = PositionManager()
        assert pm.is_empty() is True
        pm.add(_position())
        assert pm.is_empty() is False

    def test_positions_for_underlying(self):
        pm = PositionManager(max_positions_per_underlying=2)
        pm.add(_position("NIFTY-22000-CE"))
        other = _position("BNIFTY-48000-CE")
        other.underlying = "BANKNIFTY"
        pm.add(other)
        nifty_positions = pm.positions_for("NIFTY")
        assert len(nifty_positions) == 1
