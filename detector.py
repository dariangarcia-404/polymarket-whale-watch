"""Whale and insider trading detection algorithms."""

import asyncio
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
from enum import Enum
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from config import settings
from models import TrackedWallet, Trade, Market, Alert, AsyncSessionLocal
from polymarket_client import PolymarketClient, TradeData, MarketData

logger = logging.getLogger(__name__)


class AlertType(str, Enum):
    """Types of suspicious activity alerts."""
    WHALE_TRADE = "WHALE_TRADE"
    NEW_WALLET_LARGE_BET = "NEW_WALLET_LARGE_BET"
    SUSPICIOUS_WIN_STREAK = "SUSPICIOUS_WIN_STREAK"
    PRE_EVENT_ACCUMULATION = "PRE_EVENT_ACCUMULATION"
    COORDINATED_TRADING = "COORDINATED_TRADING"
    RAPID_POSITION_BUILD = "RAPID_POSITION_BUILD"
    HIGH_CONVICTION_NEW_WALLET = "HIGH_CONVICTION_NEW_WALLET"


class Severity(str, Enum):
    """Alert severity levels."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class DetectionResult:
    """Result of running detection on a trade."""
    is_suspicious: bool
    alert_type: Optional[AlertType] = None
    severity: Severity = Severity.LOW
    title: str = ""
    description: str = ""
    risk_score: float = 0.0
    metadata: dict = field(default_factory=dict)


class WhaleDetector:
    """Detects suspicious whale and insider trading activity."""
    
    def __init__(self, client: PolymarketClient):
        self.client = client
        self.recent_trades_by_market: dict[str, list[TradeData]] = {}
        self.wallet_activity_cache: dict[str, list[TradeData]] = {}
    
    async def analyze_trade(
        self,
        trade: TradeData,
        market: Optional[MarketData],
        session: AsyncSession,
    ) -> list[DetectionResult]:
        """Analyze a single trade for suspicious patterns."""
        results = []
        
        # Get or create wallet record
        wallet = await self._get_or_create_wallet(trade.maker_address, session)
        
        # Run all detection algorithms
        checks = [
            self._check_whale_trade(trade, market, wallet),
            self._check_new_wallet_large_bet(trade, market, wallet),
            self._check_high_conviction_new_wallet(trade, market, wallet),
            self._check_pre_event_accumulation(trade, market, wallet, session),
            self._check_suspicious_timing(trade, market, wallet),
        ]
        
        for check in checks:
            result = await check
            if result and result.is_suspicious:
                results.append(result)
        
        # Update wallet record
        await self._update_wallet_stats(wallet, trade, session)
        
        return results
    
    async def _get_or_create_wallet(
        self,
        address: str,
        session: AsyncSession,
    ) -> TrackedWallet:
        """Get existing wallet or create new one."""
        result = await session.execute(
            select(TrackedWallet).where(TrackedWallet.address == address)
        )
        wallet = result.scalar_one_or_none()
        
        if not wallet:
            # Estimate wallet age from first trade
            first_trade_time = await self.client.estimate_wallet_age(address)
            
            wallet = TrackedWallet(
                address=address,
                first_seen=datetime.utcnow(),
                first_trade_timestamp=first_trade_time,
            )
            session.add(wallet)
            await session.flush()
            logger.info(f"New wallet tracked: {address[:10]}...")
        
        return wallet
    
    async def _update_wallet_stats(
        self,
        wallet: TrackedWallet,
        trade: TradeData,
        session: AsyncSession,
    ):
        """Update wallet statistics after a trade."""
        wallet.total_volume_usd += trade.value_usd
        wallet.trade_count += 1
        wallet.last_updated = datetime.utcnow()
        
        # Store trade
        trade_record = Trade(
            id=trade.id or trade.transaction_hash or f"{trade.maker_address}_{trade.timestamp.isoformat()}",
            wallet_address=wallet.address,
            market_id=trade.market_id,
            market_slug="",  # Will be updated when market info available
            market_question="",
            outcome=trade.outcome,
            side=trade.side,
            size=trade.size,
            price=trade.price,
            value_usd=trade.value_usd,
            timestamp=trade.timestamp,
            is_political=True,  # We're only tracking political markets
        )
        session.add(trade_record)
        
        # Calculate risk score
        wallet.risk_score = await self._calculate_risk_score(wallet, session)
        
        await session.commit()
    
    async def _calculate_risk_score(
        self,
        wallet: TrackedWallet,
        session: AsyncSession,
    ) -> float:
        """Calculate a risk score for a wallet (0-100)."""
        score = 0.0
        
        # Factor 1: New wallet (up to 25 points)
        if wallet.wallet_age_hours < settings.new_wallet_age_hours:
            age_factor = 1 - (wallet.wallet_age_hours / settings.new_wallet_age_hours)
            score += 25 * age_factor
        
        # Factor 2: High win rate (up to 25 points)
        if wallet.trade_count >= settings.min_trades_for_analysis:
            if wallet.win_rate > settings.suspicious_win_rate:
                score += 25 * (wallet.win_rate - settings.suspicious_win_rate) / (1 - settings.suspicious_win_rate)
        
        # Factor 3: Large volume (up to 25 points)
        if wallet.total_volume_usd > settings.whale_trade_threshold_usd:
            volume_factor = min(wallet.total_volume_usd / (settings.whale_trade_threshold_usd * 10), 1.0)
            score += 25 * volume_factor
        
        # Factor 4: Trade concentration (up to 25 points)
        # Get trades in last 24 hours
        recent_trades = await session.execute(
            select(func.count(Trade.id))
            .where(Trade.wallet_address == wallet.address)
            .where(Trade.timestamp > datetime.utcnow() - timedelta(hours=24))
        )
        recent_count = recent_trades.scalar() or 0
        
        if recent_count > 10:  # Many trades in short period
            score += min(25, (recent_count - 10) * 2.5)
        
        return min(100, score)
    
    # ============ Detection Algorithms ============
    
    async def _check_whale_trade(
        self,
        trade: TradeData,
        market: Optional[MarketData],
        wallet: TrackedWallet,
    ) -> Optional[DetectionResult]:
        """Detect large whale trades."""
        if trade.value_usd < settings.whale_trade_threshold_usd:
            return None
        
        severity = Severity.MEDIUM
        if trade.value_usd > settings.whale_trade_threshold_usd * 5:
            severity = Severity.HIGH
        if trade.value_usd > settings.whale_trade_threshold_usd * 20:
            severity = Severity.CRITICAL
        
        market_title = getattr(trade, 'market_title', '') or (market.question if market else trade.market_id[:20])
        market_slug = getattr(trade, 'market_slug', '') or (market.slug if market else '')
        trader_name = getattr(trade, 'trader_name', '') or 'Unknown'
        
        # Build useful links
        market_url = f"https://polymarket.com/event/{market_slug}" if market_slug else ""
        wallet_url = f"https://polygonscan.com/address/{wallet.address}"
        tx_url = f"https://polygonscan.com/tx/{trade.transaction_hash}" if trade.transaction_hash else ""
        
        return DetectionResult(
            is_suspicious=True,
            alert_type=AlertType.WHALE_TRADE,
            severity=severity,
            title=f"🐋 Whale Trade: ${trade.value_usd:,.0f}",
            description=(
                f"Large {trade.side} order detected:\n\n"
                f"📊 MARKET: {market_title}\n"
                f"🔗 {market_url}\n\n"
                f"💰 Trade: {trade.side} {trade.outcome} @ {trade.price:.1%}\n"
                f"💵 Size: ${trade.value_usd:,.2f}\n\n"
                f"👤 Trader: {trader_name}\n"
                f"🔍 Wallet: {wallet_url}\n"
                + (f"📝 TX: {tx_url}" if tx_url else "")
            ),
            risk_score=min(50 + (trade.value_usd / settings.whale_trade_threshold_usd) * 5, 100),
            metadata={
                "trade_value": trade.value_usd,
                "market_id": trade.market_id,
                "market_title": market_title,
                "market_url": market_url,
                "wallet_url": wallet_url,
                "tx_url": tx_url,
                "outcome": trade.outcome,
                "price": trade.price,
            }
        )
    
    async def _check_new_wallet_large_bet(
        self,
        trade: TradeData,
        market: Optional[MarketData],
        wallet: TrackedWallet,
    ) -> Optional[DetectionResult]:
        """Detect new wallets making large bets."""
        is_new = wallet.wallet_age_hours < settings.new_wallet_age_hours
        is_large = trade.value_usd >= settings.whale_trade_threshold_usd * 0.5
        is_first_trades = wallet.trade_count <= 3
        
        if not (is_new and is_large and is_first_trades):
            return None
        
        severity = Severity.HIGH
        if trade.value_usd > settings.whale_trade_threshold_usd * 2:
            severity = Severity.CRITICAL
        
        market_title = getattr(trade, 'market_title', '') or (market.question if market else trade.market_id[:20])
        market_slug = getattr(trade, 'market_slug', '') or (market.slug if market else '')
        trader_name = getattr(trade, 'trader_name', '') or 'Unknown'
        
        # Build useful links
        market_url = f"https://polymarket.com/event/{market_slug}" if market_slug else ""
        wallet_url = f"https://polygonscan.com/address/{wallet.address}"
        tx_url = f"https://polygonscan.com/tx/{trade.transaction_hash}" if trade.transaction_hash else ""
        
        return DetectionResult(
            is_suspicious=True,
            alert_type=AlertType.NEW_WALLET_LARGE_BET,
            severity=severity,
            title=f"⚠️ New Wallet Large Bet: ${trade.value_usd:,.0f}",
            description=(
                f"🚨 POTENTIAL INSIDER ACTIVITY\n\n"
                f"📊 MARKET: {market_title}\n"
                f"🔗 {market_url}\n\n"
                f"💰 Bet: {trade.side} {trade.outcome} @ {trade.price:.1%}\n"
                f"💵 Size: ${trade.value_usd:,.2f}\n\n"
                f"🆕 Wallet Age: {wallet.wallet_age_hours:.1f} hours\n"
                f"📈 Prior Trades: {wallet.trade_count}\n\n"
                f"👤 Trader: {trader_name}\n"
                f"🔍 Wallet: {wallet_url}\n"
                + (f"📝 TX: {tx_url}" if tx_url else "")
            ),
            risk_score=80 + min(20, trade.value_usd / settings.whale_trade_threshold_usd),
            metadata={
                "wallet_age_hours": wallet.wallet_age_hours,
                "trade_count": wallet.trade_count,
                "trade_value": trade.value_usd,
                "market_title": market_title,
                "market_url": market_url,
                "wallet_url": wallet_url,
                "tx_url": tx_url,
            }
        )
    
    async def _check_high_conviction_new_wallet(
        self,
        trade: TradeData,
        market: Optional[MarketData],
        wallet: TrackedWallet,
    ) -> Optional[DetectionResult]:
        """Detect new wallets betting against market consensus."""
        is_new = wallet.wallet_age_hours < settings.new_wallet_age_hours
        # Require LARGE bet - at least whale threshold to avoid noise
        is_large = trade.value_usd >= settings.whale_trade_threshold_usd
        
        if not (is_new and is_large):
            return None
        
        # High conviction = buying VERY low probability outcome or selling VERY high probability
        # Only flag extreme contrarian bets (< 10% or > 90%) to reduce noise
        implied_prob = trade.price if trade.outcome == "YES" else (1 - trade.price)
        is_contrarian = (
            (trade.side == "BUY" and implied_prob < 0.10) or  # Buying < 10% outcome
            (trade.side == "SELL" and implied_prob > 0.90)    # Selling > 90% outcome
        )
        
        if not is_contrarian:
            return None
        
        market_title = getattr(trade, 'market_title', '') or (market.question if market else trade.market_id[:20])
        market_slug = getattr(trade, 'market_slug', '') or (market.slug if market else '')
        trader_name = getattr(trade, 'trader_name', '') or 'Unknown'
        
        # Build useful links
        market_url = f"https://polymarket.com/event/{market_slug}" if market_slug else ""
        wallet_url = f"https://polygonscan.com/address/{wallet.address}"
        tx_url = f"https://polygonscan.com/tx/{trade.transaction_hash}" if trade.transaction_hash else ""
        
        return DetectionResult(
            is_suspicious=True,
            alert_type=AlertType.HIGH_CONVICTION_NEW_WALLET,
            severity=Severity.CRITICAL,
            title=f"🔥 High Conviction Contrarian: ${trade.value_usd:,.0f}",
            description=(
                f"🚨🚨 EXTREME INSIDER SIGNAL 🚨🚨\n\n"
                f"New wallet betting AGAINST market consensus!\n\n"
                f"📊 MARKET: {market_title}\n"
                f"🔗 {market_url}\n\n"
                f"📈 Market Price: {implied_prob:.1%} probability\n"
                f"🎯 Their Bet: {trade.side} {trade.outcome} (contrarian!)\n"
                f"💵 Size: ${trade.value_usd:,.2f}\n\n"
                f"🆕 Wallet Age: {wallet.wallet_age_hours:.1f} hours\n\n"
                f"👤 Trader: {trader_name}\n"
                f"🔍 Wallet: {wallet_url}\n"
                + (f"📝 TX: {tx_url}" if tx_url else "")
            ),
            risk_score=95,
            metadata={
                "market_probability": implied_prob,
                "is_contrarian": True,
                "wallet_age_hours": wallet.wallet_age_hours,
                "market_title": market_title,
                "market_url": market_url,
                "wallet_url": wallet_url,
                "tx_url": tx_url,
            }
        )
    
    async def _check_pre_event_accumulation(
        self,
        trade: TradeData,
        market: Optional[MarketData],
        wallet: TrackedWallet,
        session: AsyncSession,
    ) -> Optional[DetectionResult]:
        """Detect position accumulation before market events."""
        if not market or not market.end_date:
            return None
        
        hours_to_event = (market.end_date - datetime.utcnow()).total_seconds() / 3600
        
        # Only check if event is within 48 hours
        if hours_to_event < 0 or hours_to_event > 48:
            return None
        
        # Check if this wallet has been accumulating
        result = await session.execute(
            select(Trade)
            .where(Trade.wallet_address == wallet.address)
            .where(Trade.market_id == trade.market_id)
            .where(Trade.timestamp > datetime.utcnow() - timedelta(hours=24))
        )
        recent_market_trades = result.scalars().all()
        
        if len(recent_market_trades) < 2:
            return None
        
        # Calculate total position
        total_volume = sum(t.value_usd for t in recent_market_trades) + trade.value_usd
        same_direction = all(t.outcome == trade.outcome for t in recent_market_trades)
        
        if not same_direction or total_volume < settings.whale_trade_threshold_usd:
            return None
        
        return DetectionResult(
            is_suspicious=True,
            alert_type=AlertType.PRE_EVENT_ACCUMULATION,
            severity=Severity.HIGH,
            title=f"📈 Pre-Event Accumulation: ${total_volume:,.0f}",
            description=(
                f"Position building before event resolution:\n"
                f"• Market: {market.question[:50]}...\n"
                f"• Hours to Resolution: {hours_to_event:.1f}\n"
                f"• Total Position: ${total_volume:,.2f}\n"
                f"• Direction: {trade.outcome}\n"
                f"• Trades in 24h: {len(recent_market_trades) + 1}\n"
                f"• Wallet: {wallet.address[:10]}...\n\n"
                f"📊 Rapid accumulation before event deadline!"
            ),
            risk_score=75,
            metadata={
                "hours_to_event": hours_to_event,
                "total_volume": total_volume,
                "trade_count": len(recent_market_trades) + 1,
            }
        )
    
    async def _check_suspicious_timing(
        self,
        trade: TradeData,
        market: Optional[MarketData],
        wallet: TrackedWallet,
    ) -> Optional[DetectionResult]:
        """Detect trades at suspicious times (overnight, etc)."""
        # Trading at unusual hours could indicate automated insider bot
        hour = trade.timestamp.hour
        is_off_hours = hour < 6 or hour > 23  # UTC off-hours
        is_large = trade.value_usd >= settings.whale_trade_threshold_usd
        is_new = wallet.wallet_age_hours < settings.new_wallet_age_hours * 2
        
        if not (is_off_hours and is_large and is_new):
            return None
        
        market_name = market.question[:50] if market else trade.market_id[:20]
        
        return DetectionResult(
            is_suspicious=True,
            alert_type=AlertType.COORDINATED_TRADING,
            severity=Severity.MEDIUM,
            title=f"🌙 Off-Hours Large Trade: ${trade.value_usd:,.0f}",
            description=(
                f"Large trade at unusual time:\n"
                f"• Time: {trade.timestamp.strftime('%H:%M UTC')}\n"
                f"• Market: {market_name}...\n"
                f"• Size: ${trade.value_usd:,.2f}\n"
                f"• Wallet Age: {wallet.wallet_age_hours:.1f} hours\n\n"
                f"Could indicate automated insider trading"
            ),
            risk_score=60,
            metadata={
                "hour": hour,
                "is_off_hours": True,
            }
        )


async def create_alert(
    result: DetectionResult,
    wallet_address: str,
    trade_id: str,
    market_id: str,
    session: AsyncSession,
) -> Alert:
    """Create and save an alert to the database."""
    alert = Alert(
        wallet_address=wallet_address,
        alert_type=result.alert_type.value,
        severity=result.severity.value,
        title=result.title,
        description=result.description,
        trade_id=trade_id,
        market_id=market_id,
        extra_data=result.metadata,
    )
    session.add(alert)
    await session.commit()
    
    logger.info(f"Alert created: {result.title}")
    return alert

