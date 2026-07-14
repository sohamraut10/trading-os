"""
Options-specific Market Regime Classifier.
Extends the existing detect_regime() with richer classifications needed
for strategy selection in the options module.

Regimes:
  strong_bull, strong_bear   — strong directional trend
  bull, bear                 — moderate directional trend
  sideways                   — range-bound
  breakout                   — volatility expansion from consolidation
  mean_reversion             — price reverting to mean after overextension
  high_volatility            — VIX elevated, wide ranges
  low_volatility             — VIX suppressed, tight ranges
  expiry_day                 — NSE weekly/monthly expiry (gamma risk)
  event_day                  — major scheduled macro event (RBI, Budget, FOMC)
  gap_up                     — gap open >1% above prior close
  gap_down                   — gap open >1% below prior close
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from typing import Sequence

from core.agents.base_agent import OHLCV
from core.monitoring.regime_detector import detect_regime

log = logging.getLogger(__name__)

# IST = UTC + 5:30
_IST = timezone(timedelta(hours=5, minutes=30))

# NSE weekly expiry day = Thursday (weekday 3)
_NSE_EXPIRY_WEEKDAY = 3

# Gap threshold
_GAP_PCT_THRESH = 0.01   # 1% open vs prior close = gap

# Known annual event dates (add RBI policy dates, Budget, etc.)
# Format: "MM-DD"
_KNOWN_EVENT_MONTH_DAYS: set[str] = {
    "02-01",  # Union Budget (approx)
    "03-31",  # Financial year end
}


@dataclass
class OptionsRegime:
    """Rich regime output for options strategy selection."""
    primary: str            # main regime label
    secondary: list[str] = field(default_factory=list)  # overlay conditions
    iv_regime: str = "normal"   # "low" | "normal" | "high" | "extreme"
    expiry_type: str = "none"   # "weekly" | "monthly" | "none"
    dte_today: int = 0          # days to nearest expiry
    is_expiry_day: bool = False
    is_event_day: bool = False
    gap_pct: float = 0.0        # opening gap %

    @property
    def is_high_gamma(self) -> bool:
        return self.is_expiry_day or self.dte_today <= 1

    @property
    def is_directional(self) -> bool:
        return self.primary in ("strong_bull", "bull", "strong_bear", "bear",
                                "breakout", "gap_up", "gap_down")

    @property
    def is_neutral(self) -> bool:
        return self.primary in ("sideways", "mean_reversion", "low_volatility")

    def best_strategy_types(self) -> list[str]:
        """Advisory strategy types for this regime (used by StrategyManager)."""
        if self.primary in ("strong_bull", "bull"):
            return ["atm_call_buy", "call_spread", "ema_trend"]
        if self.primary in ("strong_bear", "bear"):
            return ["atm_put_buy", "put_spread", "ema_trend"]
        if self.primary == "sideways":
            if self.iv_regime in ("high", "extreme"):
                return ["iron_condor", "short_straddle", "iron_fly"]
            return ["long_straddle", "long_strangle"]
        if self.primary == "breakout":
            return ["atm_call_buy", "atm_put_buy", "momentum_breakout"]
        if self.primary == "mean_reversion":
            return ["iron_condor", "short_straddle"]
        if self.primary == "high_volatility":
            return ["long_straddle", "long_strangle", "atm_put_buy"]
        if self.primary == "low_volatility":
            return ["iron_condor", "calendar_spread", "diagonal_spread"]
        if self.primary == "expiry_day":
            return []   # avoid new entries on expiry
        if self.primary == "event_day":
            return ["long_straddle", "long_strangle"]
        if self.primary == "gap_up":
            return ["atm_call_buy", "call_spread"]
        if self.primary == "gap_down":
            return ["atm_put_buy", "put_spread"]
        return []

    def to_dict(self) -> dict:
        return {
            "primary": self.primary,
            "secondary": self.secondary,
            "iv_regime": self.iv_regime,
            "expiry_type": self.expiry_type,
            "dte_today": self.dte_today,
            "is_expiry_day": self.is_expiry_day,
            "is_event_day": self.is_event_day,
            "gap_pct": round(self.gap_pct, 4),
            "is_high_gamma": self.is_high_gamma,
            "best_strategies": self.best_strategy_types(),
        }


class OptionsRegimeClassifier:
    """
    Classifies market into options-relevant regimes by combining:
    - Price action analysis (candles)
    - India VIX level
    - Calendar context (expiry, events, time of day)
    - Opening gap detection
    """

    # Breakout detection: if HV has expanded >50% vs 20-day HV, call it breakout
    BREAKOUT_VOL_MULTIPLIER: float = 1.5
    MEAN_REVERSION_Z_THRESHOLD: float = 2.0   # price >2σ from 20-day mean

    def classify(
        self,
        candles: Sequence[OHLCV],
        india_vix: float = 20.0,
        asset: str = "NIFTY",
        expiry_date: str | None = None,
        iv_regime: str = "normal",
    ) -> OptionsRegime:
        """
        Produce a full OptionsRegime from candle data and context.
        expiry_date: ISO date string "YYYY-MM-DD" of nearest expiry.
        """
        now_ist = datetime.now(_IST)
        today = now_ist.date()

        # ── Calendar context ──────────────────────────────────────────────────
        is_expiry, dte, expiry_type = self._expiry_context(today, expiry_date)
        is_event = self._is_event_day(today)

        # ── Fast-path: expiry / event overrides ───────────────────────────────
        if is_expiry:
            regime = OptionsRegime(
                primary="expiry_day",
                secondary=[],
                iv_regime=iv_regime,
                expiry_type=expiry_type,
                dte_today=0,
                is_expiry_day=True,
                is_event_day=is_event,
            )
            return regime

        if is_event:
            regime = OptionsRegime(
                primary="event_day",
                secondary=[],
                iv_regime=iv_regime,
                expiry_type=expiry_type,
                dte_today=dte,
                is_expiry_day=False,
                is_event_day=True,
            )
            return regime

        # ── Gap detection ─────────────────────────────────────────────────────
        gap_pct = self._opening_gap(candles, now_ist)
        if abs(gap_pct) >= _GAP_PCT_THRESH:
            primary = "gap_up" if gap_pct > 0 else "gap_down"
            secondary = self._secondary_regimes(candles, india_vix, asset)
            return OptionsRegime(
                primary=primary,
                secondary=secondary,
                iv_regime=iv_regime,
                expiry_type=expiry_type,
                dte_today=dte,
                is_expiry_day=False,
                is_event_day=is_event,
                gap_pct=gap_pct,
            )

        # ── Price action regime ───────────────────────────────────────────────
        primary = self._price_regime(candles, india_vix, asset)
        secondary = self._secondary_regimes(candles, india_vix, asset)

        return OptionsRegime(
            primary=primary,
            secondary=secondary,
            iv_regime=iv_regime,
            expiry_type=expiry_type,
            dte_today=dte,
            is_expiry_day=False,
            is_event_day=is_event,
            gap_pct=gap_pct,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _price_regime(self, candles: Sequence[OHLCV], vix: float, asset: str) -> str:
        base = detect_regime(list(candles), vix=vix, asset=asset)

        if len(candles) < 20:
            return base

        closes = [c.close for c in candles]

        # Strengthen bull/bear into strong if trend is powerful
        period_return = (closes[-1] - closes[-50]) / closes[-50] if len(closes) >= 50 else 0.0
        if base == "bull" and period_return > 0.10:
            return "strong_bull"
        if base == "bear" and period_return < -0.10:
            return "strong_bear"

        # Detect breakout: short-term HV >> long-term HV
        if len(closes) >= 40:
            recent_rv = self._hv(closes[-10:])
            baseline_rv = self._hv(closes[-40:-10])
            if baseline_rv > 0 and recent_rv / baseline_rv >= self.BREAKOUT_VOL_MULTIPLIER:
                return "breakout"

        # Detect mean reversion: price far from 20-day mean
        if len(closes) >= 20:
            mean20 = sum(closes[-20:]) / 20
            std20 = self._hv(closes[-20:]) * mean20 / 100 / math.sqrt(252)
            if std20 > 0 and abs(closes[-1] - mean20) / std20 >= self.MEAN_REVERSION_Z_THRESHOLD:
                return "mean_reversion"

        # Low/high volatility overlay as primary
        if base == "volatile" and vix >= 30:
            return "high_volatility"
        if base == "sideways" and vix <= 13:
            return "low_volatility"

        return base

    def _secondary_regimes(self, candles: Sequence[OHLCV], vix: float, asset: str) -> list[str]:
        secondary = []
        if vix >= 20:
            secondary.append("high_volatility")
        elif vix <= 13:
            secondary.append("low_volatility")
        if len(candles) >= 20:
            closes = [c.close for c in candles]
            period_return = (closes[-1] - closes[-20]) / closes[-20]
            if period_return > 0.03:
                secondary.append("bull")
            elif period_return < -0.03:
                secondary.append("bear")
        return secondary

    @staticmethod
    def _hv(closes: Sequence[float]) -> float:
        """Annualized historical volatility (%) from a price series."""
        if len(closes) < 2:
            return 0.0
        log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        n = len(log_returns)
        mean = sum(log_returns) / n
        var = sum((r - mean) ** 2 for r in log_returns) / max(1, n - 1)
        return math.sqrt(var * 252) * 100

    @staticmethod
    def _opening_gap(candles: Sequence[OHLCV], now_ist: datetime) -> float:
        """
        Gap = (today's open - yesterday's close) / yesterday's close.
        Only meaningful within first 15 minutes of market open (09:15–09:30 IST).
        """
        if len(candles) < 2:
            return 0.0
        market_open_start = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        market_open_end = now_ist.replace(hour=9, minute=30, second=0, microsecond=0)
        if not (market_open_start <= now_ist <= market_open_end):
            return 0.0
        yesterday_close = candles[-2].close
        today_open = candles[-1].open
        if yesterday_close <= 0:
            return 0.0
        return (today_open - yesterday_close) / yesterday_close

    @staticmethod
    def _expiry_context(today: date, expiry_date_str: str | None) -> tuple[bool, int, str]:
        """Returns (is_today_expiry, days_to_expiry, expiry_type)."""
        if not expiry_date_str:
            # Infer from weekday: NSE weekly expiry = Thursday
            days_to_thursday = (_NSE_EXPIRY_WEEKDAY - today.weekday()) % 7
            if days_to_thursday == 0:
                return True, 0, "weekly"
            return False, days_to_thursday, "weekly"

        try:
            exp = date.fromisoformat(expiry_date_str[:10])
        except ValueError:
            return False, 7, "weekly"

        dte = (exp - today).days
        expiry_type = "monthly" if exp.day >= 25 else "weekly"
        return dte == 0, max(0, dte), expiry_type

    @staticmethod
    def _is_event_day(today: date) -> bool:
        md = today.strftime("%m-%d")
        return md in _KNOWN_EVENT_MONTH_DAYS
