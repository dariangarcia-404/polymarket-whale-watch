"""SQLite storage for seen trades, wallet ages, and alerts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text, TypeDecorator, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .api import Trade


class UTCDateTime(TypeDecorator[datetime]):
    """SQLite drops time zones; store UTC and hand back aware datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value else None

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        return value.replace(tzinfo=timezone.utc) if value else None


class Base(DeclarativeBase):
    pass


class WalletRow(Base):
    __tablename__ = "wallets"
    address: Mapped[str] = mapped_column(String(42), primary_key=True)
    first_activity: Mapped[datetime | None] = mapped_column(UTCDateTime)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    alert_count: Mapped[int] = mapped_column(Integer, default=0)
    volume_usd: Mapped[float] = mapped_column(Float, default=0.0)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime)


class TradeRow(Base):
    __tablename__ = "trades"
    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    wallet: Mapped[str] = mapped_column(String(42), index=True)
    condition_id: Mapped[str] = mapped_column(String(80), index=True)
    token_id: Mapped[str] = mapped_column(String(100))
    side: Mapped[str] = mapped_column(String(4))
    outcome: Mapped[str] = mapped_column(String(128))
    size: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    slug: Mapped[str] = mapped_column(String(256), default="")
    event_slug: Mapped[str] = mapped_column(String(256), default="")
    transaction_hash: Mapped[str] = mapped_column(String(80), default="")

    def to_trade(self) -> Trade:
        return Trade(
            wallet=self.wallet, side=self.side, outcome=self.outcome, size=self.size,
            price=self.price, timestamp=self.timestamp, condition_id=self.condition_id,
            token_id=self.token_id, title=self.title, slug=self.slug,
            event_slug=self.event_slug, transaction_hash=self.transaction_hash,
        )


class AlertRow(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    trade_key: Mapped[str] = mapped_column(String(256))
    wallet: Mapped[str] = mapped_column(String(42), index=True)
    severity: Mapped[str] = mapped_column(String(10))
    risk_score: Mapped[int] = mapped_column(Integer)
    rules: Mapped[str] = mapped_column(String(256))
    title: Mapped[str] = mapped_column(Text)
    market: Mapped[str] = mapped_column(Text)
    market_url: Mapped[str] = mapped_column(Text)
    value_usd: Mapped[float] = mapped_column(Float)


class Store:
    def __init__(self, url: str):
        self.engine: AsyncEngine = create_async_engine(url)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    async def recent_keys(self, hours: float = 6) -> set[str]:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        async with self.session() as s:
            rows = await s.scalars(select(TradeRow.key).where(TradeRow.timestamp >= since))
            return set(rows)

    async def has_trade(self, key: str) -> bool:
        async with self.session() as s:
            return await s.get(TradeRow, key) is not None

    async def wallet(self, address: str) -> WalletRow | None:
        async with self.session() as s:
            return await s.get(WalletRow, address)

    async def wallet_trades(self, address: str, since: datetime) -> list[Trade]:
        async with self.session() as s:
            rows = await s.scalars(
                select(TradeRow).where(TradeRow.wallet == address, TradeRow.timestamp >= since)
            )
            return [r.to_trade() for r in rows]

    async def record(
        self,
        trade: Trade,
        first_activity: datetime | None,
        risk: int,
        alert: AlertRow | None,
    ) -> None:
        """Save the trade and wallet stats (and the alert, if any) in one transaction."""
        async with self.session.begin() as s:
            if await s.get(TradeRow, trade.key) is None:
                s.add(TradeRow(
                    key=trade.key, wallet=trade.wallet, condition_id=trade.condition_id,
                    token_id=trade.token_id, side=trade.side, outcome=trade.outcome,
                    size=trade.size, price=trade.price, timestamp=trade.timestamp,
                    title=trade.title, slug=trade.slug, event_slug=trade.event_slug,
                    transaction_hash=trade.transaction_hash,
                ))
            w = await s.get(WalletRow, trade.wallet)
            if w is None:
                w = WalletRow(address=trade.wallet, risk_score=0, alert_count=0, volume_usd=0.0)
                s.add(w)
            if first_activity is not None:
                w.first_activity = first_activity
            w.volume_usd = (w.volume_usd or 0) + trade.value_usd
            w.risk_score = max(w.risk_score or 0, risk)
            w.last_seen = trade.timestamp
            if alert is not None:
                w.alert_count = (w.alert_count or 0) + 1
                s.add(alert)

    async def alerts(self, limit: int = 20, severity: str | None = None) -> list[AlertRow]:
        async with self.session() as s:
            q = select(AlertRow).order_by(AlertRow.created_at.desc()).limit(limit)
            if severity:
                q = q.where(AlertRow.severity == severity.upper())
            return list(await s.scalars(q))

    async def top_wallets(self, limit: int = 20) -> list[WalletRow]:
        async with self.session() as s:
            q = select(WalletRow).order_by(WalletRow.risk_score.desc(), WalletRow.volume_usd.desc()).limit(limit)
            return list(await s.scalars(q))
