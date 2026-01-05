#!/usr/bin/env python3
"""
Polymarket Whale & Insider Detection Bot

Monitors Polymarket for suspicious trading activity including:
- New wallets making large bets (potential insider trading)
- Whale trades on political markets
- Pre-event position accumulation
- High-conviction contrarian bets
"""

import asyncio
import signal
import sys
from datetime import datetime, timedelta
from typing import Optional
import logging

import typer
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn

from config import settings
from models import init_db, AsyncSessionLocal, TrackedWallet, Alert, Market
from polymarket_client import PolymarketClient, MarketData
from detector import WhaleDetector, create_alert, DetectionResult
from notifier import get_notifier, ConsoleNotificationUI, get_notification_history

# Setup logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# CLI app
app = typer.Typer(
    name="polymarket-whale-detector",
    help="🐋 Monitor Polymarket for whale trades and potential insider activity",
    add_completion=False,
)

console = Console()


class MonitoringStats:
    """Track monitoring statistics."""
    
    def __init__(self):
        self.start_time = datetime.utcnow()
        self.markets_tracked = 0
        self.trades_analyzed = 0
        self.alerts_generated = 0
        self.wallets_flagged = 0
        self.last_trade_time: Optional[datetime] = None
    
    def to_dict(self) -> dict:
        return {
            "start_time": self.start_time.isoformat(),
            "uptime": str(datetime.utcnow() - self.start_time),
            "markets_tracked": self.markets_tracked,
            "trades_analyzed": self.trades_analyzed,
            "alerts_generated": self.alerts_generated,
            "wallets_flagged": self.wallets_flagged,
            "last_trade": self.last_trade_time.isoformat() if self.last_trade_time else "N/A",
        }


class WhaleMonitor:
    """Main monitoring orchestrator."""
    
    def __init__(self):
        self.client: Optional[PolymarketClient] = None
        self.detector: Optional[WhaleDetector] = None
        self.notifier = get_notifier()
        self.console_ui = ConsoleNotificationUI()
        self.stats = MonitoringStats()
        self.running = False
        self.political_markets: list[MarketData] = []
        self.market_cache: dict[str, MarketData] = {}
    
    async def start(self):
        """Initialize and start monitoring."""
        console.print(Panel.fit(
            "[bold cyan]🐋 Polymarket Whale & Insider Detector[/bold cyan]\n\n"
            "Monitoring for:\n"
            "• New wallets making large bets\n"
            "• Whale trades on political markets\n"
            "• Pre-event position accumulation\n"
            "• High-conviction contrarian bets",
            border_style="cyan",
        ))
        
        # Initialize database
        console.print("📦 Initializing database...")
        await init_db()
        
        # Start client
        self.client = PolymarketClient()
        await self.client.__aenter__()
        self.detector = WhaleDetector(self.client)
        
        # Load political markets
        console.print("🏛️  Loading political markets...")
        await self._refresh_markets()
        
        self.running = True
        console.print(f"[green]✓ Monitoring {len(self.political_markets)} political markets[/green]")
        console.print(f"[dim]Whale threshold: ${settings.whale_trade_threshold_usd:,.0f}[/dim]")
        console.print(f"[dim]Poll interval: {settings.poll_interval_seconds}s[/dim]\n")
    
    async def stop(self):
        """Stop monitoring and cleanup."""
        self.running = False
        if self.client:
            await self.client.__aexit__(None, None, None)
        console.print("\n[yellow]Monitoring stopped.[/yellow]")
    
    async def _refresh_markets(self):
        """Refresh the list of political markets."""
        self.political_markets = await self.client.get_political_markets()
        self.stats.markets_tracked = len(self.political_markets)
        
        for market in self.political_markets:
            self.market_cache[market.id] = market
    
    async def monitor_loop(self):
        """Main monitoring loop."""
        market_ids = [m.id for m in self.political_markets]
        refresh_counter = 0
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Monitoring for whale activity...", total=None)
            
            async for trade in self.client.stream_trades(
                markets=market_ids,
                min_value_usd=settings.whale_trade_threshold_usd * 0.3,  # Lower threshold to catch more
            ):
                if not self.running:
                    break
                
                self.stats.trades_analyzed += 1
                self.stats.last_trade_time = trade.timestamp
                
                progress.update(
                    task,
                    description=f"Analyzing trade #{self.stats.trades_analyzed} | "
                               f"${trade.value_usd:,.0f} | Alerts: {self.stats.alerts_generated}"
                )
                
                # Get market info
                market = self.market_cache.get(trade.market_id)
                if not market:
                    market = await self.client.get_market_by_id(trade.market_id)
                    if market:
                        self.market_cache[trade.market_id] = market
                
                # Analyze trade
                async with AsyncSessionLocal() as session:
                    results = await self.detector.analyze_trade(trade, market, session)
                    
                    for result in results:
                        if result.is_suspicious:
                            self.stats.alerts_generated += 1
                            
                            # Create alert in database
                            await create_alert(
                                result=result,
                                wallet_address=trade.maker_address,
                                trade_id=trade.id or trade.transaction_hash or "",
                                market_id=trade.market_id,
                                session=session,
                            )
                            
                            # Send notifications
                            await self.notifier.send_notification(
                                result=result,
                                wallet_address=trade.maker_address,
                                trade_id=trade.id or "",
                            )
                            
                            # Display in console
                            progress.stop()
                            self.console_ui.display_alert(result, trade.maker_address)
                            progress.start()
                
                # Refresh markets periodically
                refresh_counter += 1
                if refresh_counter >= 100:
                    await self._refresh_markets()
                    refresh_counter = 0


async def run_monitor():
    """Run the whale monitor."""
    monitor = WhaleMonitor()
    
    # Handle graceful shutdown
    def signal_handler(sig, frame):
        console.print("\n[yellow]Shutting down...[/yellow]")
        asyncio.create_task(monitor.stop())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        await monitor.start()
        await monitor.monitor_loop()
    except KeyboardInterrupt:
        pass
    finally:
        await monitor.stop()


@app.command()
def monitor(
    threshold: float = typer.Option(
        None,
        "--threshold", "-t",
        help="Minimum trade size in USD to track",
    ),
    interval: int = typer.Option(
        None,
        "--interval", "-i",
        help="Poll interval in seconds",
    ),
    no_notifications: bool = typer.Option(
        False,
        "--no-notifications",
        help="Disable desktop notifications",
    ),
):
    """
    🐋 Start monitoring Polymarket for whale trades and insider activity.
    
    Tracks political markets for suspicious patterns including:
    - New wallets making large bets
    - Whale trades before events
    - High-conviction contrarian positions
    """
    if threshold:
        settings.whale_trade_threshold_usd = threshold
    if interval:
        settings.poll_interval_seconds = interval
    if no_notifications:
        settings.enable_desktop_notifications = False
    
    asyncio.run(run_monitor())


@app.command()
def alerts(
    limit: int = typer.Option(20, "--limit", "-l", help="Number of alerts to show"),
    severity: str = typer.Option(None, "--severity", "-s", help="Filter by severity"),
):
    """📋 View recent alerts from the database."""
    
    async def show_alerts():
        await init_db()
        
        from sqlalchemy import select, desc
        from models import Alert
        
        async with AsyncSessionLocal() as session:
            query = select(Alert).order_by(desc(Alert.created_at)).limit(limit)
            if severity:
                query = query.where(Alert.severity == severity.upper())
            
            result = await session.execute(query)
            alerts = result.scalars().all()
            
            if not alerts:
                console.print("[yellow]No alerts found.[/yellow]")
                return
            
            table = Table(title=f"📋 Recent Alerts (Last {len(alerts)})")
            table.add_column("Time", style="dim")
            table.add_column("Severity", justify="center")
            table.add_column("Type")
            table.add_column("Title")
            table.add_column("Wallet")
            
            severity_styles = {
                "LOW": "green",
                "MEDIUM": "yellow",
                "HIGH": "red",
                "CRITICAL": "bold red",
            }
            
            for alert in alerts:
                style = severity_styles.get(alert.severity, "white")
                table.add_row(
                    alert.created_at.strftime("%m/%d %H:%M"),
                    f"[{style}]{alert.severity}[/{style}]",
                    alert.alert_type,
                    alert.title[:40] + "..." if len(alert.title) > 40 else alert.title,
                    alert.wallet_address[:10] + "...",
                )
            
            console.print(table)
    
    asyncio.run(show_alerts())


@app.command()
def wallets(
    flagged: bool = typer.Option(False, "--flagged", "-f", help="Show only flagged wallets"),
    limit: int = typer.Option(20, "--limit", "-l", help="Number of wallets to show"),
):
    """👛 View tracked wallets and their risk scores."""
    
    async def show_wallets():
        await init_db()
        
        from sqlalchemy import select, desc
        
        async with AsyncSessionLocal() as session:
            query = select(TrackedWallet).order_by(desc(TrackedWallet.risk_score)).limit(limit)
            if flagged:
                query = query.where(TrackedWallet.is_flagged == True)
            
            result = await session.execute(query)
            wallets = result.scalars().all()
            
            if not wallets:
                console.print("[yellow]No wallets found.[/yellow]")
                return
            
            table = Table(title=f"👛 Tracked Wallets (Top {len(wallets)} by Risk)")
            table.add_column("Address", style="cyan")
            table.add_column("Risk Score", justify="right")
            table.add_column("Volume", justify="right")
            table.add_column("Trades", justify="right")
            table.add_column("Age (hrs)", justify="right")
            table.add_column("Win Rate", justify="right")
            
            for wallet in wallets:
                risk_style = (
                    "green" if wallet.risk_score < 40 else
                    "yellow" if wallet.risk_score < 70 else
                    "red"
                )
                table.add_row(
                    wallet.address[:12] + "...",
                    f"[{risk_style}]{wallet.risk_score:.0f}[/{risk_style}]",
                    f"${wallet.total_volume_usd:,.0f}",
                    str(wallet.trade_count),
                    f"{wallet.wallet_age_hours:.1f}",
                    f"{wallet.win_rate:.0%}",
                )
            
            console.print(table)
    
    asyncio.run(show_wallets())


@app.command()
def markets(
    limit: int = typer.Option(20, "--limit", "-l", help="Number of markets to show"),
):
    """🏛️ View currently tracked political markets."""
    
    async def show_markets():
        async with PolymarketClient() as client:
            political = await client.get_political_markets()
            
            # Sort by volume
            political.sort(key=lambda m: m.volume, reverse=True)
            political = political[:limit]
            
            table = Table(title=f"🏛️ Political Markets (Top {len(political)} by Volume)")
            table.add_column("Question", max_width=50)
            table.add_column("Volume", justify="right")
            table.add_column("Liquidity", justify="right")
            table.add_column("End Date")
            
            for market in political:
                end_date = market.end_date.strftime("%m/%d/%y") if market.end_date else "N/A"
                table.add_row(
                    market.question[:50] + "..." if len(market.question) > 50 else market.question,
                    f"${market.volume:,.0f}",
                    f"${market.liquidity:,.0f}",
                    end_date,
                )
            
            console.print(table)
    
    asyncio.run(show_markets())


@app.command()
def test_notification(
    sms: bool = typer.Option(False, "--sms", help="Test SMS notification"),
    email: bool = typer.Option(False, "--email", help="Test email notification"),
    pushover: bool = typer.Option(False, "--pushover", help="Test Pushover notification"),
    all_channels: bool = typer.Option(False, "--all", help="Test all notification channels"),
):
    """🔔 Send a test notification to verify setup."""
    
    async def send_test():
        from detector import DetectionResult, AlertType, Severity
        
        # Temporarily enable requested channels for testing
        if sms or all_channels:
            settings.enable_sms = True
        if email or all_channels:
            settings.enable_email = True
        if pushover or all_channels:
            settings.enable_pushover = True
        
        result = DetectionResult(
            is_suspicious=True,
            alert_type=AlertType.WHALE_TRADE,
            severity=Severity.HIGH,  # HIGH triggers phone notifications
            title="🐋 TEST: Whale Trade Detected",
            description=(
                "This is a test notification!\n\n"
                "• Market: Will the test succeed?\n"
                "• Position: YES @ 95%\n"
                "• Size: $10,000.00\n"
                "• Wallet: 0xTEST..."
            ),
            risk_score=75,
        )
        
        notifier = get_notifier()
        await notifier.send_notification(
            result=result,
            wallet_address="0xTEST1234567890",
            trade_id="test_123",
        )
        
        console.print("[green]✓ Test notification sent![/green]")
        
        if sms or all_channels:
            console.print("[dim]  📱 SMS: Check your phone[/dim]")
        if email or all_channels:
            console.print("[dim]  📧 Email: Check your inbox[/dim]")
        if pushover or all_channels:
            console.print("[dim]  📲 Pushover: Check Pushover app[/dim]")
    
    asyncio.run(send_test())


@app.command()
def config():
    """⚙️ Display current configuration."""
    table = Table(title="⚙️ Current Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value")
    
    table.add_row("Whale Threshold", f"${settings.whale_trade_threshold_usd:,.0f}")
    table.add_row("New Wallet Age", f"{settings.new_wallet_age_hours} hours")
    table.add_row("Suspicious Win Rate", f"{settings.suspicious_win_rate:.0%}")
    table.add_row("Poll Interval", f"{settings.poll_interval_seconds} seconds")
    table.add_row("Desktop Notifications", str(settings.enable_desktop_notifications))
    table.add_row("Sound Alerts", str(settings.enable_sound_alerts))
    table.add_row("Database", settings.database_url)
    
    console.print(table)
    
    console.print("\n[dim]Political keywords tracked:[/dim]")
    console.print(f"[dim]{', '.join(settings.political_keywords[:10])}...[/dim]")


if __name__ == "__main__":
    app()

