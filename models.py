"""Database models for tracking wallets, trades, and alerts."""

from datetime import datetime
from typing import Optional
from sqlalchemy import Column, String, Float, DateTime, Boolean, Integer, Text, ForeignKey, JSON
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from config import settings

Base = declarative_base()


class TrackedWallet(Base):
    """Wallet being monitored for suspicious activity."""
    
    __tablename__ = "tracked_wallets"
    
    address = Column(String(42), primary_key=True)
    first_seen = Column(DateTime, default=datetime.utcnow)
    first_trade_timestamp = Column(DateTime, nullable=True)
    total_volume_usd = Column(Float, default=0.0)
    trade_count = Column(Integer, default=0)
    win_count = Column(Integer, default=0)
    loss_count = Column(Integer, default=0)
    is_flagged = Column(Boolean, default=False)
    flag_reason = Column(Text, nullable=True)
    risk_score = Column(Float, default=0.0)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    trades = relationship("Trade", back_populates="wallet")
    alerts = relationship("Alert", back_populates="wallet")
    
    @property
    def win_rate(self) -> float:
        total = self.win_count + self.loss_count
        return self.win_count / total if total > 0 else 0.0
    
    @property
    def wallet_age_hours(self) -> float:
        if not self.first_seen:
            return 0
        delta = datetime.utcnow() - self.first_seen
        return delta.total_seconds() / 3600


class Trade(Base):
    """Individual trade record."""
    
    __tablename__ = "trades"
    
    id = Column(String(64), primary_key=True)  # Transaction hash
    wallet_address = Column(String(42), ForeignKey("tracked_wallets.address"))
    market_id = Column(String(128))
    market_slug = Column(String(256))
    market_question = Column(Text)
    outcome = Column(String(64))  # YES/NO
    side = Column(String(4))  # BUY/SELL
    size = Column(Float)  # Token amount
    price = Column(Float)  # Price per token
    value_usd = Column(Float)
    timestamp = Column(DateTime)
    is_political = Column(Boolean, default=False)
    market_resolved = Column(Boolean, default=False)
    trade_won = Column(Boolean, nullable=True)
    
    wallet = relationship("TrackedWallet", back_populates="trades")
    
    @property
    def implied_probability(self) -> float:
        return self.price if self.outcome == "YES" else 1 - self.price


class Market(Base):
    """Cached market information."""
    
    __tablename__ = "markets"
    
    id = Column(String(128), primary_key=True)
    condition_id = Column(String(128))
    slug = Column(String(256))
    question = Column(Text)
    description = Column(Text)
    category = Column(String(64))
    is_political = Column(Boolean, default=False)
    created_at = Column(DateTime)
    end_date = Column(DateTime, nullable=True)
    resolved = Column(Boolean, default=False)
    resolution = Column(String(64), nullable=True)
    volume = Column(Float, default=0.0)
    liquidity = Column(Float, default=0.0)
    last_updated = Column(DateTime, default=datetime.utcnow)
    tokens = Column(JSON, nullable=True)  # Token IDs for YES/NO


class Alert(Base):
    """Generated alerts for suspicious activity."""
    
    __tablename__ = "alerts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address = Column(String(42), ForeignKey("tracked_wallets.address"))
    alert_type = Column(String(64))  # WHALE_TRADE, NEW_WALLET_LARGE_BET, SUSPICIOUS_PATTERN, etc.
    severity = Column(String(16))  # LOW, MEDIUM, HIGH, CRITICAL
    title = Column(String(256))
    description = Column(Text)
    trade_id = Column(String(64), nullable=True)
    market_id = Column(String(128), nullable=True)
    extra_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    acknowledged = Column(Boolean, default=False)
    
    wallet = relationship("TrackedWallet", back_populates="alerts")


# Async engine setup
engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    """Initialize the database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    """Get a database session."""
    async with AsyncSessionLocal() as session:
        yield session

