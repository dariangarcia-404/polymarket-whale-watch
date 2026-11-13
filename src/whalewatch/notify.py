"""Alert delivery: console, desktop, SMS (Twilio), email, and Pushover.

Market titles and trader names come from other people, so they are treated as
untrusted: never interpolated into a shell or AppleScript program, and HTML-escaped
in email.
"""

from __future__ import annotations

import asyncio
import html
import logging
import platform
import shutil
from dataclasses import dataclass, field
from email.message import EmailMessage

import httpx

from .api import Trade
from .config import Settings
from .detect import Signal, format_age, overall_severity

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Alert:
    severity: str
    risk_score: int
    title: str
    market: str
    market_url: str
    wallet: str
    wallet_url: str
    value_usd: float
    lines: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join([self.title, *self.lines, self.market_url, self.wallet_url])


def build_alert(trade: Trade, signals: list[Signal], risk: int, wallet_age_hours: float | None) -> Alert:
    top = max(signals, key=lambda s: ("LOW", "MEDIUM", "HIGH", "CRITICAL").index(s.severity))
    price_note = f"{trade.side} {trade.outcome or '?'} at {trade.price:.0%}"
    who = trade.trader_name or f"{trade.wallet[:6]}…{trade.wallet[-4:]}"
    age = format_age(wallet_age_hours)
    lines = [
        f"Market: {trade.title or trade.condition_id}",
        f"Trade: {price_note}, ${trade.value_usd:,.0f}",
        f"Trader: {who} (wallet age {age})",
        *[r for sig in signals for r in sig.reasons],
        f"Risk score: {risk}/100",
    ]
    return Alert(
        severity=overall_severity(signals),
        risk_score=risk,
        title=top.title,
        market=trade.title or trade.condition_id,
        market_url=trade.market_url,
        wallet=trade.wallet,
        wallet_url=trade.wallet_url,
        value_usd=trade.value_usd,
        lines=lines,
        rules=[s.rule for s in signals],
    )


def email_html(alert: Alert) -> str:
    e = html.escape
    colors = {"LOW": "#2f855a", "MEDIUM": "#b7791f", "HIGH": "#c53030", "CRITICAL": "#742a2a"}
    items = "".join(f"<li>{e(line)}</li>" for line in alert.lines)
    return (
        f'<div style="font-family:sans-serif;max-width:600px">'
        f'<h2 style="color:{colors.get(alert.severity, "#333")}">[{e(alert.severity)}] {e(alert.title)}</h2>'
        f"<ul>{items}</ul>"
        f'<p><a href="{e(alert.market_url, quote=True)}">Market</a> · '
        f'<a href="{e(alert.wallet_url, quote=True)}">Wallet</a></p>'
        f'<p style="color:#666;font-size:12px">Flags unusual activity only. Not evidence of insider trading.</p>'
        f"</div>"
    )


_APPLESCRIPT = """on run argv
  display notification (item 2 of argv) with title (item 1 of argv) sound name "default"
end run"""


class Notifier:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self.s = settings
        self._http = http
        self._twilio = None
        if settings.enable_sms:
            if not all([settings.twilio_account_sid, settings.twilio_auth_token,
                        settings.twilio_from_number, settings.twilio_to_number]):
                log.warning("SMS enabled but Twilio settings are incomplete; SMS disabled")
            else:
                try:
                    from twilio.rest import Client
                except ImportError:
                    log.warning("SMS enabled but twilio isn't installed: pip install 'polymarket-whale-watch[sms]'")
                else:
                    self._twilio = Client(settings.twilio_account_sid, settings.twilio_auth_token)

    def channels(self) -> list[str]:
        out = ["console"]
        if self.s.enable_desktop_notifications:
            out.append("desktop")
        if self._twilio:
            out.append("sms")
        if self.s.enable_email and self.s.smtp_username:
            out.append("email")
        if self.s.enable_pushover and self.s.pushover_user_key and self.s.pushover_api_token:
            out.append("pushover")
        return out

    async def send(self, alert: Alert, force_remote: bool = False) -> None:
        tasks = []
        if self.s.enable_desktop_notifications:
            tasks.append(self._desktop(alert))
        if force_remote or alert.severity in self.s.notify_severities:
            chans = self.channels()
            if "sms" in chans:
                tasks.append(self._sms(alert))
            if "email" in chans:
                tasks.append(self._email(alert))
            if "pushover" in chans:
                tasks.append(self._pushover(alert))
        for result in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(result, Exception):
                log.error("Notification failed: %s", result)

    async def _desktop(self, alert: Alert) -> None:
        title = f"[{alert.severity}] {alert.title}"
        body = f"{alert.market} · ${alert.value_usd:,.0f}"[:200]
        system = platform.system()
        if system == "Darwin":
            argv = ["osascript", "-e", _APPLESCRIPT, title, body]
        elif system == "Linux" and shutil.which("notify-send"):
            urgency = "critical" if alert.severity == "CRITICAL" else "normal"
            argv = ["notify-send", "-u", urgency, "--", title, body]
        else:
            return
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()

    async def _sms(self, alert: Alert) -> None:
        body = f"[{alert.severity}] {alert.title}\n{alert.market[:80]}\nRisk {alert.risk_score}/100\n{alert.market_url}"
        await asyncio.to_thread(
            self._twilio.messages.create,  # type: ignore[union-attr]
            body=body[:1600], from_=self.s.twilio_from_number, to=self.s.twilio_to_number,
        )

    async def _email(self, alert: Alert) -> None:
        try:
            import aiosmtplib
        except ImportError:
            log.warning("Email enabled but aiosmtplib isn't installed: pip install 'polymarket-whale-watch[email]'")
            return
        msg = EmailMessage()
        msg["Subject"] = f"[{alert.severity}] {alert.title}"
        msg["From"] = self.s.smtp_username
        msg["To"] = self.s.email_to or self.s.smtp_username
        msg.set_content(alert.text + "\n\nFlags unusual activity only. Not evidence of insider trading.")
        msg.add_alternative(email_html(alert), subtype="html")
        await aiosmtplib.send(
            msg, hostname=self.s.smtp_host, port=self.s.smtp_port,
            username=self.s.smtp_username, password=self.s.smtp_password, start_tls=True,
        )

    async def _pushover(self, alert: Alert) -> None:
        priority = {"LOW": -1, "MEDIUM": 0, "HIGH": 1, "CRITICAL": 1}[alert.severity]
        data = {
            "token": self.s.pushover_api_token,
            "user": self.s.pushover_user_key,
            "title": f"[{alert.severity}] {alert.title}"[:250],
            "message": "\n".join(alert.lines)[:1024],
            "url": alert.market_url,
            "url_title": "Open market",
            "priority": priority,
        }
        if self._http:
            r = await self._http.post("https://api.pushover.net/1/messages.json", data=data)
            r.raise_for_status()
            return
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post("https://api.pushover.net/1/messages.json", data=data)
            r.raise_for_status()
