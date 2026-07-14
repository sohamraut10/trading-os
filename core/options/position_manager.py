"""
Options Position Manager.
Tracks all open option positions, aggregates portfolio Greeks,
manages position limits, and provides risk-adjusted sizing.

Integrates with the existing RiskEngine for portfolio-level limits.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Sequence

from core.options.greeks_engine import GreeksEngine, PortfolioGreeks
from core.options.exit_engine import OptionPosition

log = logging.getLogger(__name__)


@dataclass
class PositionSizeResult:
    """Output of position sizing calculation."""
    approved_lots: int
    approved_cost_inr: float
    reason: str
    capped: bool = False
    warnings: list[str] = field(default_factory=list)


class PositionManager:
    """
    Manages open option positions with portfolio-level risk controls.

    Limits enforced:
    - Max concurrent option legs
    - Max net delta exposure (avoids unintended directional bias)
    - Max portfolio vega (prevents excessive IV sensitivity)
    - Max daily theta drag
    - Per-underlying position limit
    """

    def __init__(
        self,
        max_legs: int = 6,
        max_net_delta: float = 5.0,       # max absolute net delta in portfolio
        max_net_vega: float = 50.0,       # max absolute net vega (per 1% IV move)
        max_theta_drag_inr: float = 3000, # max daily theta loss in ₹
        max_positions_per_underlying: int = 2,
        greeks_engine: GreeksEngine | None = None,
    ):
        self.max_legs = max_legs
        self.max_net_delta = max_net_delta
        self.max_net_vega = max_net_vega
        self.max_theta_drag = max_theta_drag_inr
        self.max_per_underlying = max_positions_per_underlying
        self._greeks = greeks_engine or GreeksEngine()
        self._positions: dict[str, OptionPosition] = {}

    # ── Position tracking ─────────────────────────────────────────────────────

    def add(self, pos: OptionPosition) -> bool:
        """Register a new position. Returns False if limits are breached."""
        if pos.symbol in self._positions:
            log.warning("Position %s already tracked — skipping duplicate add", pos.symbol)
            return True

        # Check leg limit
        if len(self._positions) >= self.max_legs:
            log.warning("Max legs (%d) reached — cannot add %s", self.max_legs, pos.symbol)
            return False

        # Check per-underlying limit
        existing_for_ul = sum(1 for p in self._positions.values()
                              if p.underlying == pos.underlying)
        if existing_for_ul >= self.max_per_underlying:
            log.warning("Max positions (%d) for %s reached", self.max_per_underlying, pos.underlying)
            return False

        self._positions[pos.symbol] = pos
        log.info("Position added: %s | legs=%d", pos.symbol, len(self._positions))
        return True

    def remove(self, symbol: str) -> OptionPosition | None:
        return self._positions.pop(symbol, None)

    def update_premium(self, symbol: str, new_premium: float, new_iv: float = 0.0) -> None:
        if symbol in self._positions:
            self._positions[symbol].update(new_premium, new_iv)

    def get(self, symbol: str) -> OptionPosition | None:
        return self._positions.get(symbol)

    def all_positions(self) -> list[OptionPosition]:
        return list(self._positions.values())

    def positions_for(self, underlying: str) -> list[OptionPosition]:
        return [p for p in self._positions.values() if p.underlying == underlying]

    def is_empty(self) -> bool:
        return len(self._positions) == 0

    # ── Portfolio Greeks ──────────────────────────────────────────────────────

    def portfolio_greeks(self) -> PortfolioGreeks:
        """Compute aggregate portfolio Greeks across all open positions."""
        if not self._positions:
            return PortfolioGreeks()
        legs = [
            {
                "S": p.current_premium * 10,   # proxy spot = premium × 10 (CE/PE delta approx)
                "K": p.strike,
                "T": max(1, p.days_to_expiry) / 365.0,
                "sigma": p.current_iv if p.current_iv > 0 else 0.20,
                "option_type": p.option_type,
                "quantity": p.quantity,
                "lot_size": p.lot_size,
            }
            for p in self._positions.values()
        ]
        return self._greeks.portfolio_greeks(legs)

    def is_within_greek_limits(self) -> tuple[bool, list[str]]:
        """
        Check if current portfolio Greeks are within configured limits.
        Returns (within_limits, list_of_violations).
        """
        pg = self.portfolio_greeks()
        violations = []
        if abs(pg.net_delta) > self.max_net_delta:
            violations.append(f"Net delta {pg.net_delta:.2f} > max {self.max_net_delta}")
        if abs(pg.net_vega) > self.max_net_vega:
            violations.append(f"Net vega {pg.net_vega:.2f} > max {self.max_net_vega}")
        if pg.theta_decay_inr < -self.max_theta_drag:
            violations.append(f"Daily theta drag ₹{pg.theta_decay_inr:.0f} > max -₹{self.max_theta_drag:.0f}")
        return len(violations) == 0, violations

    # ── Position sizing ───────────────────────────────────────────────────────

    def size_position(
        self,
        equity: float,
        premium_per_unit: float,
        lot_size: int,
        max_risk_pct: float = 0.05,
        confidence_score: float = 75.0,
        max_lots: int = 10,
    ) -> PositionSizeResult:
        """
        Risk-adjusted position sizing for an option buy.
        Max risk = premium paid (defined risk for long options).

        Sizing formula:
          budget = equity × max_risk_pct × (confidence / 100)
          lots = floor(budget / (premium × lot_size))
          capped at max_lots and portfolio Greek limits
        """
        warnings = []

        if premium_per_unit <= 0 or lot_size <= 0:
            return PositionSizeResult(0, 0.0, "Zero premium or lot size", capped=True)

        # Kelly-inspired budget: scale by confidence
        confidence_factor = max(0.5, min(1.0, confidence_score / 100.0))
        budget = equity * max_risk_pct * confidence_factor
        cost_per_lot = premium_per_unit * lot_size

        if cost_per_lot > budget:
            # Can't afford 1 lot within budget — check if 1 lot is still safe
            one_lot_pct = cost_per_lot / equity if equity > 0 else 1.0
            if one_lot_pct > 0.40:
                return PositionSizeResult(
                    0, 0.0,
                    f"1 lot = ₹{cost_per_lot:.0f} ({one_lot_pct:.0%} of equity) > 40% cap",
                    capped=True,
                )
            warnings.append(f"Budget below 1-lot cost — forced to 1 lot ({one_lot_pct:.1%} of equity)")
            approved_lots = 1
        else:
            approved_lots = min(max_lots, int(budget // cost_per_lot))

        approved_lots = max(1, approved_lots)
        approved_cost = approved_lots * cost_per_lot

        # Check if adding this would breach Greek limits
        within, viols = self.is_within_greek_limits()
        if not within:
            warnings.extend(viols)
            # Scale down by 50% as a precaution
            approved_lots = max(1, approved_lots // 2)
            approved_cost = approved_lots * cost_per_lot
            warnings.append(f"Lots halved to {approved_lots} due to Greek limit breach")

        return PositionSizeResult(
            approved_lots=approved_lots,
            approved_cost_inr=approved_cost,
            reason=f"Budget ₹{budget:.0f}, {approved_lots} lots × ₹{cost_per_lot:.0f}",
            capped=bool(warnings),
            warnings=warnings,
        )

    # ── P&L summary ───────────────────────────────────────────────────────────

    def pnl_summary(self) -> dict:
        total_pnl = sum(p.pnl_inr for p in self._positions.values())
        total_cost = sum(p.entry_premium * abs(p.quantity) * p.lot_size
                         for p in self._positions.values())
        return {
            "open_legs": len(self._positions),
            "total_unrealized_pnl_inr": round(total_pnl, 2),
            "total_cost_inr": round(total_cost, 2),
            "pnl_pct": round(total_pnl / total_cost * 100 if total_cost > 0 else 0, 2),
            "positions": [
                {
                    "symbol": p.symbol,
                    "pnl_pct": round(p.pnl_pct * 100, 2),
                    "pnl_inr": round(p.pnl_inr, 2),
                    "current_premium": p.current_premium,
                    "dte": p.days_to_expiry,
                }
                for p in self._positions.values()
            ],
        }
