"""
Alert System — Telegram + Console
Fires real-time alerts on: signal generated, trade executed, circuit breaker triggered,
daily P&L milestone.
"""
import asyncio
from dataclasses import dataclass
from typing import Literal

from core.agents.meta_agent import TradeSignal


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

    def add_telegram(self, token: str, chat_id: str) -> None:
        if token and chat_id:
            self._alerters.append(TelegramAlerter(token, chat_id))

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
        else:
            alert = Alert(
                level="info",
                title=f"FALSE SIGNAL — {signal.asset} (rejected)",
                body=f"Reason: {signal.reason[:200]}",
                asset=signal.asset,
            )
        await self._broadcast(alert)

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
