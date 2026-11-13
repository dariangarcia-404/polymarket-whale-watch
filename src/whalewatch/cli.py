"""Command-line interface: ``whalewatch monitor | alerts | wallets | test-alert | config``."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timezone

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .api import PolymarketClient
from .config import Settings
from .monitor import Monitor
from .notify import Alert, Notifier
from .store import Store

app = typer.Typer(
    help="Watch Polymarket for whale trades and unusual bets from fresh wallets.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()
STYLES = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "red", "CRITICAL": "bold white on red"}


def show_alert(alert: Alert) -> None:
    style = STYLES.get(alert.severity, "white")
    body = "\n".join(alert.lines) + f"\n\n{alert.market_url}\n{alert.wallet_url}"
    console.print(Panel(body, title=f"[{style}]{alert.severity}[/] {alert.title}", border_style=style.split()[-1]))


@app.command()
def monitor(
    threshold: float = typer.Option(None, "--threshold", "-t", help="Whale threshold in USD."),
    interval: float = typer.Option(None, "--interval", "-i", help="Seconds between polls."),
    all_markets: bool = typer.Option(False, "--all-markets", help="Include sports and crypto price markets."),
    no_desktop: bool = typer.Option(False, "--no-desktop", help="Disable desktop notifications."),
) -> None:
    """Poll Polymarket and alert on suspicious trades until Ctrl-C."""
    overrides: dict[str, object] = {}
    if threshold:
        overrides["whale_trade_threshold_usd"] = threshold
    if interval:
        overrides["poll_interval_seconds"] = interval
    if all_markets:
        overrides["market_filter"] = "all"
    if no_desktop:
        overrides["enable_desktop_notifications"] = False
    settings = Settings(**overrides)
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    async def run() -> None:
        store = Store(settings.database_url)
        await store.init()
        notifier = Notifier(settings)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows
                pass
        console.print(Panel.fit(
            f"Polymarket Whale Watch {__version__}\n"
            f"Threshold ${settings.whale_trade_threshold_usd:,.0f} · poll every {settings.poll_interval_seconds:g}s · "
            f"markets: {settings.market_filter}\nAlerts via: {', '.join(notifier.channels())}",
            border_style="cyan",
        ))
        async with PolymarketClient(settings.data_api, settings.gamma_api) as client:
            mon = Monitor(settings, client, store, notifier, on_alert=show_alert)
            try:
                await mon.run(stop)
            finally:
                await store.close()
                s = mon.stats
                console.print(f"\nStopped. {s.polls} polls, {s.trades} trades analyzed, {s.alerts} alerts.")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


@app.command()
def alerts(
    limit: int = typer.Option(20, "--limit", "-n"),
    severity: str = typer.Option(None, "--severity", "-s", help="LOW, MEDIUM, HIGH or CRITICAL."),
) -> None:
    """List recent alerts."""
    settings = Settings()

    async def run() -> None:
        store = Store(settings.database_url)
        await store.init()
        rows = await store.alerts(limit, severity)
        await store.close()
        if not rows:
            console.print("No alerts yet.")
            return
        t = Table(title="Recent alerts")
        for col in ("When (UTC)", "Severity", "Risk", "Size", "Alert", "Market"):
            t.add_column(col)
        for a in rows:
            t.add_row(
                f"{a.created_at:%m-%d %H:%M}", f"[{STYLES.get(a.severity, '')}]{a.severity}[/]",
                str(a.risk_score), f"${a.value_usd:,.0f}", a.title, a.market[:50],
            )
        console.print(t)

    asyncio.run(run())


@app.command()
def wallets(limit: int = typer.Option(20, "--limit", "-n")) -> None:
    """List the highest-risk wallets seen so far."""
    settings = Settings()

    async def run() -> None:
        store = Store(settings.database_url)
        await store.init()
        rows = await store.top_wallets(limit)
        await store.close()
        if not rows:
            console.print("No wallets yet.")
            return
        now = datetime.now(timezone.utc)
        t = Table(title="Wallets by risk")
        for col in ("Wallet", "Risk", "Volume", "Alerts", "Age"):
            t.add_column(col)
        for w in rows:
            age = f"{(now - w.first_activity).total_seconds() / 86400:.1f} d" if w.first_activity else "?"
            t.add_row(w.address, str(w.risk_score), f"${w.volume_usd:,.0f}", str(w.alert_count), age)
        console.print(t)

    asyncio.run(run())


@app.command("test-alert")
def test_alert() -> None:
    """Send a sample alert through every configured channel."""
    settings = Settings()
    notifier = Notifier(settings)
    alert = Alert(
        severity="HIGH", risk_score=80, title="Test alert from Polymarket Whale Watch",
        market="Example market", market_url="https://polymarket.com",
        wallet="0x0000000000000000000000000000000000000000",
        wallet_url="https://polymarket.com", value_usd=10_000,
        lines=["This is a test. If you can read it, alerts are working."],
    )
    show_alert(alert)
    asyncio.run(notifier.send(alert, force_remote=True))
    console.print(f"Sent via: {', '.join(notifier.channels())}")


@app.command()
def config() -> None:
    """Show the active settings (secrets hidden)."""
    s = Settings()
    t = Table(title="Settings", show_header=False)
    for name, value in s.model_dump().items():
        if any(k in name for k in ("token", "password", "sid", "user_key")) and value:
            value = "•••"
        t.add_row(name, str(value))
    console.print(t)


if __name__ == "__main__":
    app()
