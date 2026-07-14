"""
Alert System — Telegram + Console
Fires real-time alerts on: signal generated, trade executed, circuit breaker triggered,
daily P&L milestone.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Literal

from core.agents.meta_agent import TradeSignal

_MCX_PREFIXES = (
    "GOLD", "SILVER", "CRUDEOIL", "NATURALGAS", "NATGAS", "COPPER",
    "ZINC", "LEAD", "NICKEL", "ALUMINIUM", "MENTHAOIL", "KAPAS",
    "COTTON", "CARDAMOM", "STEELREBAR",
)

def _ist_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))

def _is_market_live(asset: str) -> bool:
    """Return True only if the exchange for this asset is currently open (IST)."""
    now = _ist_now()
    if now.weekday() >= 5:   # Saturday / Sunday
        return False
    t = now.time()
    up = asset.upper()
    is_mcx = any(up.startswith(p) for p in _MCX_PREFIXES)
    from datetime import time as _t
    if is_mcx:
        return _t(9, 0) <= t <= _t(23, 30)
    return _t(9, 15) <= t <= _t(15, 30)


AlertLevel = Literal["info", "warning", "critical"]


@dataclass
class Alert:
    level: AlertLevel
    title: str
    body: str
    asset: str = ""
    trade_id: str = ""


class TelegramAlerter:
    def __init__(self, bot_token: str, chat_id: str):
        self._token = bot_token
        self._chat_id = chat_id
        self._base = f"https://api.telegram.org/bot{bot_token}"

    async def send(self, alert: Alert) -> None:
        emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(alert.level, "📌")
        text = f"{emoji} *{alert.title}*\n\n{alert.body}"
        if alert.asset:
            text += f"\n\n`Asset: {alert.asset}`"

        import aiohttp
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self._base}/sendMessage", json=payload) as resp:
                    if resp.status != 200:
                        print(f"[Alert] Telegram error: {await resp.text()}")
        except Exception as e:
            print(f"[Alert] Failed to send Telegram alert: {e}")


class ConsoleAlerter:
    async def send(self, alert: Alert) -> None:
        prefix = {"info": "[INFO]", "warning": "[WARN]", "critical": "[CRIT]"}.get(alert.level, "[LOG]")
        print(f"{prefix} {alert.title} | {alert.body}")


class AlertRouter:
    """Routes alerts to all registered channels."""

    def __init__(self, alerters=None):
        self._alerters = alerters or [ConsoleAlerter()]
        self._telegram_alerters: list[TelegramAlerter] = []

    def add_telegram(self, token: str, chat_id: str) -> None:
        if token and chat_id:
            t = TelegramAlerter(token, chat_id)
            self._alerters.append(t)
            self._telegram_alerters.append(t)

    async def signal_generated(self, signal: TradeSignal) -> None:
        if signal.final_decision:
            alert = Alert(
                level="info",
                title=f"TRUE SIGNAL — {signal.asset} {signal.action.value if signal.action else ''}",
                body=(
                    f"Confidence: {signal.confidence:.0f}%\n"
                    f"Regime: {signal.regime}\n"
                    f"Reason: {signal.reason[:200]}"
                ),
                asset=signal.asset,
                trade_id=signal.request_id,
            )
            if _is_market_live(signal.asset):
                # Market open — fire all channels including Telegram
                await self._broadcast(alert)
            else:
                # Market closed — console only, suppress Telegram
                await asyncio.gather(
                    *[a.send(alert) for a in self._alerters if isinstance(a, ConsoleAlerter)],
                    return_exceptions=True,
                )
        else:
            # FALSE signals only go to console — Telegram stays quiet
            await asyncio.gather(
                *[a.send(Alert(
                    level="info",
                    title=f"FALSE SIGNAL — {signal.asset} (rejected)",
                    body=f"Reason: {signal.reason[:200]}",
                    asset=signal.asset,
                )) for a in self._alerters if isinstance(a, ConsoleAlerter)],
                return_exceptions=True,
            )

    async def circuit_breaker(self, reason: str) -> None:
        await self._broadcast(Alert(
            level="critical",
            title="CIRCUIT BREAKER TRIGGERED",
            body=reason,
        ))

    async def pnl_milestone(self, daily_pnl_pct: float) -> None:
        level: AlertLevel = "critical" if daily_pnl_pct < -2 else "info"
        await self._broadcast(Alert(
            level=level,
            title=f"Daily P&L: {daily_pnl_pct:+.2f}%",
            body="Daily drawdown limit approaching." if daily_pnl_pct < -2 else "P&L update.",
        ))

    async def _broadcast(self, alert: Alert) -> None:
        await asyncio.gather(*[a.send(alert) for a in self._alerters], return_exceptions=True)
