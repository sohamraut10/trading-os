"""
Expiry Engine for NSE/BSE F&O.
Manages the expiry calendar, gamma risk assessment near expiry,
and strategy constraints based on days-to-expiry.

NSE expiry schedule:
  - NIFTY: Thursday weekly (moved from Thursday to any day as NSE updates)
  - BANKNIFTY: Wednesday weekly
  - FINNIFTY: Tuesday weekly
  - MIDCPNIFTY: Monday weekly
  - Monthly: last Thursday of month
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

log = logging.getLogger(__name__)

# Weekly expiry weekdays per index (Python weekday: Mon=0, Thu=3)
_WEEKLY_EXPIRY_WEEKDAY: dict[str, int] = {
    "NIFTY": 3,        # Thursday
    "BANKNIFTY": 2,    # Wednesday
    "FINNIFTY": 1,     # Tuesday
    "MIDCPNIFTY": 0,   # Monday
    "SENSEX": 4,       # Friday (BSE)
}

# High gamma risk zones (DTE where gamma accelerates dangerously)
_HIGH_GAMMA_DTE = 2   # last 2 days before expiry


@dataclass
class ExpiryInfo:
    """Information about the nearest expiry for an underlying."""
    underlying: str
    nearest_weekly: date
    nearest_monthly: date
    days_to_weekly: int
    days_to_monthly: int
    is_expiry_today: bool
    is_high_gamma_zone: bool  # DTE ≤ _HIGH_GAMMA_DTE
    expiry_type_nearest: str  # "weekly" | "monthly"
    recommended_min_dte: int  # recommended min DTE for new entries

    @property
    def recommended_expiry(self) -> date:
        """Pick the safer expiry for new entries."""
        if self.days_to_weekly >= self.recommended_min_dte:
            return self.nearest_weekly
        return self.nearest_monthly

    def to_dict(self) -> dict:
        return {
            "underlying": self.underlying,
            "nearest_weekly": self.nearest_weekly.isoformat(),
            "nearest_monthly": self.nearest_monthly.isoformat(),
            "days_to_weekly": self.days_to_weekly,
            "days_to_monthly": self.days_to_monthly,
            "is_expiry_today": self.is_expiry_today,
            "is_high_gamma": self.is_high_gamma_zone,
            "recommended_expiry": self.recommended_expiry.isoformat(),
            "recommended_min_dte": self.recommended_min_dte,
        }


class ExpiryEngine:
    """
    NSE/BSE expiry calendar manager.
    Computes expiry dates, gamma risk zones, and strategy DTE constraints.
    """

    # Minimum DTE for new entries (avoid gamma trap)
    MIN_DTE_DIRECTIONAL = 2
    MIN_DTE_SPREAD = 5
    MIN_DTE_IRON_CONDOR = 7

    def get_info(self, underlying: str, today: date | None = None) -> ExpiryInfo:
        """Compute expiry information for the given underlying and date."""
        today = today or date.today()
        underlying_upper = underlying.upper()
        weekly_wd = _WEEKLY_EXPIRY_WEEKDAY.get(underlying_upper, 3)  # default Thursday

        nearest_weekly = self._next_weekday(today, weekly_wd)
        nearest_monthly = self._next_monthly_expiry(today, weekly_wd)

        dte_weekly = (nearest_weekly - today).days
        dte_monthly = (nearest_monthly - today).days
        is_expiry_today = dte_weekly == 0
        is_high_gamma = dte_weekly <= _HIGH_GAMMA_DTE

        # Recommended min DTE: higher on expiry week
        if is_high_gamma:
            rec_min_dte = self.MIN_DTE_DIRECTIONAL + _HIGH_GAMMA_DTE
        else:
            rec_min_dte = self.MIN_DTE_DIRECTIONAL

        return ExpiryInfo(
            underlying=underlying,
            nearest_weekly=nearest_weekly,
            nearest_monthly=nearest_monthly,
            days_to_weekly=dte_weekly,
            days_to_monthly=dte_monthly,
            is_expiry_today=is_expiry_today,
            is_high_gamma_zone=is_high_gamma,
            expiry_type_nearest="weekly" if dte_weekly <= dte_monthly else "monthly",
            recommended_min_dte=rec_min_dte,
        )

    def can_enter(self, underlying: str, strategy_type: str, today: date | None = None) -> tuple[bool, str]:
        """
        Return (can_enter, reason) for a new trade given strategy type.
        """
        info = self.get_info(underlying, today)

        if info.is_expiry_today:
            return False, f"Expiry day for {underlying} — no new entries"

        if info.is_high_gamma_zone:
            if strategy_type in ("iron_condor", "short_straddle", "iron_fly"):
                return False, (f"DTE={info.days_to_weekly}: high gamma risk for "
                               f"short-premium strategies")
            if strategy_type in ("atm_call_buy", "atm_put_buy"):
                return True, f"DTE={info.days_to_weekly}: directional buy OK near expiry"

        min_dte_req = {
            "iron_condor": self.MIN_DTE_IRON_CONDOR,
            "short_straddle": self.MIN_DTE_IRON_CONDOR,
            "iron_fly": self.MIN_DTE_SPREAD,
            "call_spread": self.MIN_DTE_SPREAD,
            "put_spread": self.MIN_DTE_SPREAD,
        }.get(strategy_type, self.MIN_DTE_DIRECTIONAL)

        if info.days_to_weekly < min_dte_req:
            # Maybe use monthly instead
            if info.days_to_monthly >= min_dte_req:
                return True, (f"Weekly DTE={info.days_to_weekly} < {min_dte_req}, "
                              f"use monthly expiry DTE={info.days_to_monthly}")
            return False, (f"Both expiries have insufficient DTE "
                           f"(weekly={info.days_to_weekly}, monthly={info.days_to_monthly})")

        return True, f"DTE={info.days_to_weekly} — OK for {strategy_type}"

    def gamma_risk_score(self, dte: int) -> float:
        """
        Gamma risk score 0–100. Higher = more dangerous.
        Non-linear: spikes sharply in last 2 days.
        """
        if dte <= 0:
            return 100.0
        if dte == 1:
            return 90.0
        if dte == 2:
            return 75.0
        if dte <= 5:
            return 50.0 - (dte - 2) * 5
        return max(0.0, 30.0 - dte * 2)

    def should_accelerate_exit(self, dte: int, pnl_pct: float) -> bool:
        """
        During expiry afternoon, accelerate exit when position is profitable.
        Gamma acceleration means winning positions can reverse rapidly.
        """
        return dte <= 1 and pnl_pct > 0.20   # take 20%+ profit on expiry day

    # ── Calendar helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _next_weekday(from_date: date, weekday: int) -> date:
        """Next occurrence of `weekday` (0=Mon) on or after `from_date`."""
        days_ahead = weekday - from_date.weekday()
        if days_ahead < 0:
            days_ahead += 7
        return from_date + timedelta(days=days_ahead)

    @staticmethod
    def _next_monthly_expiry(from_date: date, expiry_weekday: int) -> date:
        """
        Last Thursday (or expiry_weekday) of current or next month.
        NSE monthly F&O expires on last Thursday of expiry month.
        """
        def last_weekday_of_month(year: int, month: int, wd: int) -> date:
            # Start from last day of month and walk back
            if month == 12:
                last = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                last = date(year, month + 1, 1) - timedelta(days=1)
            offset = (last.weekday() - wd) % 7
            return last - timedelta(days=offset)

        y, m = from_date.year, from_date.month
        monthly = last_weekday_of_month(y, m, expiry_weekday)
        if monthly <= from_date:
            # Move to next month
            if m == 12:
                y, m = y + 1, 1
            else:
                m += 1
            monthly = last_weekday_of_month(y, m, expiry_weekday)
        return monthly

    def all_expiries_this_month(self, underlying: str, today: date | None = None) -> list[date]:
        """All weekly expiries remaining in the current month."""
        today = today or date.today()
        underlying_upper = underlying.upper()
        weekly_wd = _WEEKLY_EXPIRY_WEEKDAY.get(underlying_upper, 3)
        expiries = []
        d = self._next_weekday(today, weekly_wd)
        while d.month == today.month:
            expiries.append(d)
            d += timedelta(weeks=1)
        return expiries
