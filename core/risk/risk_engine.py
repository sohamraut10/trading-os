from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import math

from config.settings import settings
from core.agents.meta_agent import TradeSignal
from core.agents.base_agent import Signal


class RiskStatus(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SCALED_DOWN = "SCALED_DOWN"    # trade allowed but size reduced


@dataclass
class PortfolioState:
    equity: float                      # total account equity
    cash: float                        # available cash
    open_trades: int                   # number of open positions
    daily_pnl_pct: float               # today's realized + unrealized PnL %
    max_daily_drawdown_pct: float      # worst point today
    positions: dict[str, float]        # symbol → notional value
    consecutive_losses: int = 0
    sector_exposure: dict[str, float] = None   # sector → notional

    def __post_init__(self):
        if self.sector_exposure is None:
            self.sector_exposure = {}

    @property
    def total_exposure_pct(self) -> float:
        total_positions = sum(abs(v) for v in self.positions.values())
        return total_positions / self.equity if self.equity > 0 else 0.0


@dataclass
class RiskCheckResult:
    status: RiskStatus
    approved_position_size_pct: float
    approved_position_size_usd: float
    stop_loss_price: float
    take_profit_price: float
    rejection_reasons: list[str]
    warnings: list[str]
    sanitization_diff: list[str] = field(default_factory=list)

    def is_tradeable(self) -> bool:
        return self.status in (RiskStatus.APPROVED, RiskStatus.SCALED_DOWN)


class RiskEngine:
    """
    All risk checks are independent gates. A single gate failure rejects the trade.
    Order of evaluation: circuit breakers → portfolio limits → position sizing → SR levels.
    """

    def __init__(self):
        self.cfg = settings.risk

    def check(
        self,
        signal: TradeSignal,
        portfolio: PortfolioState,
        current_price: float,
    ) -> RiskCheckResult:
        rejections = []
        warnings = []
        sanitization_diff = []

        # ── Gate 1: Circuit Breakers ────────────────────────────────────────
        if portfolio.daily_pnl_pct <= -self.cfg.max_daily_drawdown:
            rejections.append(
                f"Daily circuit breaker: drawdown {portfolio.daily_pnl_pct:.2%} "
                f"exceeds limit {self.cfg.max_daily_drawdown:.2%}"
            )

        if portfolio.consecutive_losses >= 3:
            warnings.append(f"Loss streak: {portfolio.consecutive_losses} consecutive losses")
            if portfolio.consecutive_losses >= 5:
                rejections.append(f"Loss streak circuit breaker: {portfolio.consecutive_losses} losses")

        # ── Gate 2: Portfolio Exposure ──────────────────────────────────────
        if portfolio.total_exposure_pct >= self.cfg.max_portfolio_exposure:
            rejections.append(
                f"Portfolio fully deployed: {portfolio.total_exposure_pct:.2%} "
                f">= {self.cfg.max_portfolio_exposure:.2%} limit"
            )

        if portfolio.open_trades >= self.cfg.max_open_trades:
            rejections.append(f"Max open trades reached: {portfolio.open_trades}/{self.cfg.max_open_trades}")

        if rejections:
            return RiskCheckResult(
                status=RiskStatus.REJECTED,
                approved_position_size_pct=0.0,
                approved_position_size_usd=0.0,
                stop_loss_price=0.0,
                take_profit_price=0.0,
                rejection_reasons=rejections,
                warnings=warnings,
                sanitization_diff=["Rejected by risk engine gates"],
            )

        # ── Gate 3: Position Sizing ─────────────────────────────────────────
        desired_pct = signal.suggested_position_size_pct
        available_pct = self.cfg.max_portfolio_exposure - portfolio.total_exposure_pct
        # Options: premium = max loss (defined-risk), so a higher per-trade cap is safe.
        size_cap = (
            settings.options_max_position_pct
            if settings.trade_mode == "options"
            else self.cfg.max_position_pct
        )
        approved_pct = min(desired_pct, available_pct, size_cap)
        scaled_down = approved_pct < desired_pct * 0.99

        if approved_pct < 0.002:  # too small to be worth trading
            return RiskCheckResult(
                status=RiskStatus.REJECTED,
                approved_position_size_pct=0.0,
                approved_position_size_usd=0.0,
                stop_loss_price=0.0,
                take_profit_price=0.0,
                rejection_reasons=["Position size too small after constraints"],
                warnings=warnings,
                sanitization_diff=["Size below minimum trading floor"],
            )

        position_usd = portfolio.equity * approved_pct

        # ── Gate 3b: Minimum Lot Check ──────────────────────────────────────
        # For whole-share markets (NSE/BSE), the minimum order is 1 share.
        # If the approved position budget can't buy even 1 share, scale up to
        # 1 share — but only if 1 share ≤ 40% of available cash.
        # Skip this check for:
        #   - Crypto/forex pairs (fractional by nature)
        #   - Index instruments (NIFTY/BANKNIFTY etc.) — never bought as shares,
        #     always traded via options or futures lots priced off premium, not spot
        #   - Options trade mode — actual cost is premium × lots, not the underlying price
        _INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "NIFTYNXT50", "MIDCPNIFTY", "SENSEX"}
        is_fractional_asset = (
            "/" in signal.asset or "USD" in signal.asset.upper()
            or signal.asset.upper() in _INDEX_UNDERLYINGS
            or settings.trade_mode == "options"
        )
        if not is_fractional_asset and current_price > 0 and position_usd < current_price:
            one_share_pct = current_price / portfolio.equity if portfolio.equity > 0 else 1.0
            if one_share_pct > 0.40:
                return RiskCheckResult(
                    status=RiskStatus.REJECTED,
                    approved_position_size_pct=0.0,
                    approved_position_size_usd=0.0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    rejection_reasons=[
                        f"Insufficient capital: 1 share costs {current_price:.0f} "
                        f"({one_share_pct:.0%} of equity), exceeds 40% limit"
                    ],
                    warnings=warnings,
                    sanitization_diff=["Rejected: cannot afford minimum lot"],
                )
            # Scale up to exactly 1 share
            approved_pct = one_share_pct
            position_usd = current_price
            scaled_down = True
            sanitization_diff.append(
                f"Size raised to 1-share minimum: {current_price:.0f} "
                f"({approved_pct:.2%} of equity)"
            )

        if scaled_down and not any("minimum" in s for s in sanitization_diff):
            warnings.append(f"Position scaled from {desired_pct:.2%} → {approved_pct:.2%}")
            sanitization_diff.append(
                f"size cut {desired_pct * 100:.2f}% -> {approved_pct * 100:.2f}% by exposure limits"
            )

        # ── Gate 4: Stop Loss / Take Profit Prices ──────────────────────────
        sl_pct = max(signal.suggested_stop_loss_pct, self.cfg.max_trade_drawdown)
        if signal.suggested_stop_loss_pct < self.cfg.max_trade_drawdown:
            sanitization_diff.append(
                f"SL floor applied: {signal.suggested_stop_loss_pct * 100:.2f}% -> {self.cfg.max_trade_drawdown * 100:.2f}%"
            )

        raw_tp = signal.suggested_take_profit_pct
        min_tp = sl_pct * self.cfg.default_rr_ratio
        tp_pct = raw_tp if raw_tp >= min_tp else min_tp
        if raw_tp < min_tp:
            sanitization_diff.append(
                f"TP floor applied: {raw_tp * 100:.2f}% -> {min_tp * 100:.2f}% (R:R {self.cfg.default_rr_ratio}x)"
            )

        # In options mode, current_price is the underlying spot (e.g. ₹24500 for NIFTY).
        # Computing sl/tp as spot × fraction produces meaningless ₹23,000-level prices
        # for what is actually a ₹150 premium. Skip spot-based pricing — the options
        # router enforces SL/TP on the premium directly.
        if settings.trade_mode == "options":
            sl_price = 0.0
            tp_price = 0.0
            dynamic_sl_pct = settings.options_sl_pct
            sanitization_diff.append(
                f"Options mode: SL={dynamic_sl_pct*100:.0f}% of premium (dynamic by DTE), "
                f"TP=2× risk ({dynamic_sl_pct*200:.0f}% gain) — 1:2 R:R on premium"
            )
        else:
            if signal.action == Signal.BUY:
                sl_price = current_price * (1 - sl_pct)
                tp_price = current_price * (1 + tp_pct)
            else:
                sl_price = current_price * (1 + sl_pct)
                tp_price = current_price * (1 - tp_pct)

        # ── Gate 5: Minimum Risk/Reward ─────────────────────────────────────
        # Enforced for equity only — options router handles its own 1:2 R:R via
        # the premium TP order placed after entry.
        if settings.trade_mode != "options":
            rr = tp_pct / sl_pct if sl_pct > 0 else 0
            if rr < settings.min_risk_reward:
                return RiskCheckResult(
                    status=RiskStatus.REJECTED,
                    approved_position_size_pct=0.0,
                    approved_position_size_usd=0.0,
                    stop_loss_price=0.0,
                    take_profit_price=0.0,
                    rejection_reasons=[
                        f"R:R {rr:.2f} below minimum {settings.min_risk_reward:.1f} — "
                        f"entry price would not support a {settings.min_risk_reward:.0f}:1 reward"
                    ],
                    warnings=warnings,
                    sanitization_diff=sanitization_diff,
                )

        status = RiskStatus.SCALED_DOWN if scaled_down else RiskStatus.APPROVED

        return RiskCheckResult(
            status=status,
            approved_position_size_pct=approved_pct,
            approved_position_size_usd=round(position_usd, 2),
            stop_loss_price=round(sl_price, 6),
            take_profit_price=round(tp_price, 6),
            rejection_reasons=[],
            warnings=warnings,
            sanitization_diff=sanitization_diff,
        )

    def check_manual_order(
        self,
        side: str,
        quantity: float,
        current_price: float,
        portfolio: PortfolioState,
    ) -> RiskCheckResult:
        """
        Same gates as check() (circuit breakers, exposure, open-trade caps) but for a
        manually-submitted order with an explicit quantity instead of an agent signal.
        Sizing can only be scaled down to fit within limits, never scaled up.
        """
        rejections = []
        warnings = []
        sanitization_diff = []

        if portfolio.daily_pnl_pct <= -self.cfg.max_daily_drawdown:
            rejections.append(
                f"Daily circuit breaker: drawdown {portfolio.daily_pnl_pct:.2%} "
                f"exceeds limit {self.cfg.max_daily_drawdown:.2%}"
            )

        if portfolio.consecutive_losses >= 5:
            rejections.append(f"Loss streak circuit breaker: {portfolio.consecutive_losses} losses")

        if portfolio.total_exposure_pct >= self.cfg.max_portfolio_exposure:
            rejections.append(
                f"Portfolio fully deployed: {portfolio.total_exposure_pct:.2%} "
                f">= {self.cfg.max_portfolio_exposure:.2%} limit"
            )

        if portfolio.open_trades >= self.cfg.max_open_trades:
            rejections.append(f"Max open trades reached: {portfolio.open_trades}/{self.cfg.max_open_trades}")

        if current_price <= 0 or quantity <= 0:
            rejections.append("Invalid price or quantity")

        if rejections:
            return RiskCheckResult(
                status=RiskStatus.REJECTED,
                approved_position_size_pct=0.0,
                approved_position_size_usd=0.0,
                stop_loss_price=0.0,
                take_profit_price=0.0,
                rejection_reasons=rejections,
                warnings=warnings,
                sanitization_diff=["Rejected by risk engine gates"],
            )

        desired_usd = quantity * current_price
        desired_pct = desired_usd / portfolio.equity if portfolio.equity > 0 else 0.0
        available_pct = self.cfg.max_portfolio_exposure - portfolio.total_exposure_pct
        approved_pct = min(desired_pct, available_pct, self.cfg.max_position_pct)
        scaled_down = approved_pct < desired_pct * 0.99

        if approved_pct < 0.002:
            return RiskCheckResult(
                status=RiskStatus.REJECTED,
                approved_position_size_pct=0.0,
                approved_position_size_usd=0.0,
                stop_loss_price=0.0,
                take_profit_price=0.0,
                rejection_reasons=["Order size too small or exceeds available exposure"],
                warnings=warnings,
                sanitization_diff=["Size below minimum trading floor"],
            )

        if scaled_down:
            approved_usd = portfolio.equity * approved_pct
            warnings.append(f"Order scaled from {desired_pct:.2%} to {approved_pct:.2%} of equity")
            sanitization_diff.append(
                f"quantity cut to fit exposure limits: ${desired_usd:,.2f} -> ${approved_usd:,.2f}"
            )
        else:
            approved_usd = desired_usd

        sl_pct = self.cfg.max_trade_drawdown
        tp_pct = sl_pct * self.cfg.default_rr_ratio
        if side.lower() == "buy":
            sl_price = current_price * (1 - sl_pct)
            tp_price = current_price * (1 + tp_pct)
        else:
            sl_price = current_price * (1 + sl_pct)
            tp_price = current_price * (1 - tp_pct)

        status = RiskStatus.SCALED_DOWN if scaled_down else RiskStatus.APPROVED

        return RiskCheckResult(
            status=status,
            approved_position_size_pct=approved_pct,
            approved_position_size_usd=round(approved_usd, 2),
            stop_loss_price=round(sl_price, 6),
            take_profit_price=round(tp_price, 6),
            rejection_reasons=[],
            warnings=warnings,
            sanitization_diff=sanitization_diff,
        )

    def compute_portfolio_var(self, portfolio: PortfolioState, confidence: float = 0.95) -> float:
        total_exposure = portfolio.equity * portfolio.total_exposure_pct
        daily_vol = 0.02
        z = 1.645 if confidence == 0.95 else 2.326  # 99%
        return total_exposure * daily_vol * z


class PositionSizer:
    """
    Kelly Criterion–inspired position sizing with hard guardrails.
    f* = (p * b - q) / b  where p=win_rate, b=avg_win/avg_loss, q=1-p
    We use fractional Kelly (0.25x) for conservative sizing.
    """

    @staticmethod
    def kelly_size(
        win_rate: float,
        avg_win_pct: float,
        avg_loss_pct: float,
        portfolio_equity: float,
        kelly_fraction: float = 0.25,
        max_pct: float = 0.05,
    ) -> float:
        if avg_loss_pct <= 0 or win_rate <= 0:
            return 0.0
        b = avg_win_pct / avg_loss_pct
        q = 1 - win_rate
        f_star = (win_rate * b - q) / b
        f_star = max(0.0, f_star)  # no negative sizing
        fractional = f_star * kelly_fraction
        capped = min(fractional, max_pct)
        return portfolio_equity * capped
