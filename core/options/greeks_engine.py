"""
Black-Scholes Greeks Engine for Indian Index Options.
Computes Delta, Gamma, Theta, Vega, Rho for CE/PE options.
Implied volatility via bisection on the BS pricing formula.
Portfolio Greeks aggregation for net exposure management.
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from typing import Sequence

log = logging.getLogger(__name__)

try:
    from scipy.stats import norm as _scipy_norm
    _USE_SCIPY = True
except ImportError:
    _USE_SCIPY = False
    log.warning("scipy not installed — falling back to pure-Python N(x) approximation")

# India 10-year G-sec / RBI repo rate proxy
_DEFAULT_RISK_FREE = 0.065


# ── Math helpers ──────────────────────────────────────────────────────────────

def _ncdf(x: float) -> float:
    """Standard normal CDF. Uses scipy when available."""
    if _USE_SCIPY:
        return float(_scipy_norm.cdf(x))
    # Abramowitz & Stegun 7.1.26 — max error 7.5e-8
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937
            + t * (-1.821255978 + t * 1.330274429))))
    p = 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x * x) * poly
    return p if x >= 0 else 1.0 - p


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class Greeks:
    """Greeks for a single option leg."""
    delta: float = 0.0     # sensitivity to spot price move
    gamma: float = 0.0     # rate of change of delta
    theta: float = 0.0     # time decay per calendar day (usually negative for longs)
    vega: float = 0.0      # sensitivity per 1% change in IV
    rho: float = 0.0       # sensitivity per 1% change in risk-free rate
    iv: float = 0.0        # implied volatility (decimal: 0.20 = 20%)

    def to_dict(self) -> dict:
        return {
            "delta": round(self.delta, 4),
            "gamma": round(self.gamma, 6),
            "theta": round(self.theta, 4),
            "vega": round(self.vega, 4),
            "rho": round(self.rho, 4),
            "iv_pct": round(self.iv * 100, 2),
        }


@dataclass
class PortfolioGreeks:
    """Aggregate Greeks across all open option positions."""
    net_delta: float = 0.0
    net_gamma: float = 0.0
    net_theta: float = 0.0         # per calendar day
    net_vega: float = 0.0
    net_rho: float = 0.0
    gamma_exposure_inr: float = 0.0   # P&L impact of 1% spot move, in ₹
    theta_decay_inr: float = 0.0      # daily theta P&L in ₹
    is_delta_hedged: bool = False

    def to_dict(self) -> dict:
        return {
            "net_delta": round(self.net_delta, 4),
            "net_gamma": round(self.net_gamma, 6),
            "net_theta": round(self.net_theta, 4),
            "net_vega": round(self.net_vega, 4),
            "net_rho": round(self.net_rho, 4),
            "gamma_exposure_inr": round(self.gamma_exposure_inr, 2),
            "theta_decay_inr": round(self.theta_decay_inr, 2),
            "is_delta_hedged": self.is_delta_hedged,
        }


# ── Greeks Engine ─────────────────────────────────────────────────────────────

class GreeksEngine:
    """
    Black-Scholes Greeks engine.

    Convention:
    - S: spot price (₹)
    - K: strike price (₹)
    - T: time to expiry in years (e.g. 7/365 for 7 calendar days)
    - r: risk-free rate as decimal (0.065 = 6.5%)
    - sigma: volatility as decimal (0.20 = 20%)
    - option_type: "CE" (call) or "PE" (put)
    """

    risk_free_rate: float = _DEFAULT_RISK_FREE

    # ── Core pricing ──────────────────────────────────────────────────────────

    def _d1_d2(self, S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
        if T <= 1e-9 or sigma <= 1e-9 or S <= 0 or K <= 0:
            return 0.0, 0.0
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return d1, d2

    def price(self, S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> float:
        """Theoretical option price via Black-Scholes."""
        if T <= 0:
            if option_type == "CE":
                return max(0.0, S - K)
            return max(0.0, K - S)
        d1, d2 = self._d1_d2(S, K, T, r, sigma)
        disc = math.exp(-r * T)
        if option_type == "CE":
            return max(0.0, S * _ncdf(d1) - K * disc * _ncdf(d2))
        return max(0.0, K * disc * _ncdf(-d2) - S * _ncdf(-d1))

    # ── Individual Greeks ─────────────────────────────────────────────────────

    def delta(self, S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> float:
        if T <= 0:
            return float(S > K) if option_type == "CE" else -float(S < K)
        d1, _ = self._d1_d2(S, K, T, r, sigma)
        return _ncdf(d1) if option_type == "CE" else _ncdf(d1) - 1.0

    def gamma(self, S: float, K: float, T: float, r: float, sigma: float) -> float:
        if T <= 0 or S <= 0 or sigma <= 0:
            return 0.0
        d1, _ = self._d1_d2(S, K, T, r, sigma)
        return _npdf(d1) / (S * sigma * math.sqrt(T))

    def theta(self, S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> float:
        """Theta per calendar day. Negative for long options (time decay)."""
        if T <= 0:
            return 0.0
        d1, d2 = self._d1_d2(S, K, T, r, sigma)
        term1 = -S * _npdf(d1) * sigma / (2.0 * math.sqrt(T))
        disc = math.exp(-r * T)
        if option_type == "CE":
            return (term1 - r * K * disc * _ncdf(d2)) / 365.0
        return (term1 + r * K * disc * _ncdf(-d2)) / 365.0

    def vega(self, S: float, K: float, T: float, r: float, sigma: float) -> float:
        """Vega per 1% change in implied volatility."""
        if T <= 0:
            return 0.0
        d1, _ = self._d1_d2(S, K, T, r, sigma)
        return S * _npdf(d1) * math.sqrt(T) / 100.0

    def rho(self, S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> float:
        """Rho per 1% change in risk-free rate."""
        if T <= 0:
            return 0.0
        _, d2 = self._d1_d2(S, K, T, r, sigma)
        disc = math.exp(-r * T)
        if option_type == "CE":
            return K * T * disc * _ncdf(d2) / 100.0
        return -K * T * disc * _ncdf(-d2) / 100.0

    # ── All Greeks in one call ────────────────────────────────────────────────

    def compute(
        self,
        S: float,
        K: float,
        T: float,
        sigma: float,
        option_type: str,
        r: float | None = None,
    ) -> Greeks:
        r = r if r is not None else self.risk_free_rate
        return Greeks(
            delta=self.delta(S, K, T, r, sigma, option_type),
            gamma=self.gamma(S, K, T, r, sigma),
            theta=self.theta(S, K, T, r, sigma, option_type),
            vega=self.vega(S, K, T, r, sigma),
            rho=self.rho(S, K, T, r, sigma, option_type),
            iv=sigma,
        )

    # ── Implied Volatility ────────────────────────────────────────────────────

    def implied_volatility(
        self,
        market_price: float,
        S: float,
        K: float,
        T: float,
        option_type: str,
        r: float | None = None,
        max_iter: int = 200,
        tol: float = 0.001,
    ) -> float:
        """
        Implied volatility via bisection on Black-Scholes price.
        Returns -1.0 if no solution found (e.g. below intrinsic value).
        """
        r = r if r is not None else self.risk_free_rate
        if T <= 0 or market_price <= 0:
            return -1.0
        intrinsic = max(0.0, S - K) if option_type == "CE" else max(0.0, K - S)
        if market_price < intrinsic * 0.999:
            return -1.0

        lo, hi = 0.001, 20.0  # 0.1% to 2000% vol
        for _ in range(max_iter):
            mid = (lo + hi) / 2.0
            p = self.price(S, K, T, r, mid, option_type)
            diff = p - market_price
            if abs(diff) < tol:
                return mid
            if diff < 0:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0

    # ── Portfolio Greeks ──────────────────────────────────────────────────────

    def portfolio_greeks(self, positions: Sequence[dict]) -> PortfolioGreeks:
        """
        Aggregate Greeks across a list of option positions.

        Each position dict must contain:
            S           float  spot price
            K           float  strike
            T           float  time to expiry in years
            sigma       float  IV as decimal
            option_type str    "CE" or "PE"
            quantity    int    signed (positive = long, negative = short)
            lot_size    int    contract multiplier (default 1)
            r           float  optional risk-free rate override

        Returns PortfolioGreeks with all aggregates in contract-adjusted units.
        """
        pg = PortfolioGreeks()
        for pos in positions:
            S = pos["S"]; K = pos["K"]; T = pos["T"]
            sigma = pos["sigma"]; otype = pos["option_type"]
            qty = int(pos.get("quantity", 0))
            lot = int(pos.get("lot_size", 1))
            r = float(pos.get("r", self.risk_free_rate))
            multiplier = qty * lot

            g = self.compute(S, K, T, sigma, otype, r)
            pg.net_delta += g.delta * multiplier
            pg.net_gamma += g.gamma * multiplier
            pg.net_theta += g.theta * multiplier
            pg.net_vega += g.vega * multiplier
            pg.net_rho += g.rho * multiplier
            # Gamma exposure: P&L from a 1% spot move = 0.5 * Γ * (0.01S)^2 * multiplier
            pg.gamma_exposure_inr += 0.5 * g.gamma * (0.01 * S) ** 2 * multiplier
            # Theta decay in ₹/day: theta per unit × lot × qty × spot (proxy)
            pg.theta_decay_inr += g.theta * multiplier

        pg.is_delta_hedged = abs(pg.net_delta) < 0.10
        return pg
