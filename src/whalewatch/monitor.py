"""The polling loop: fetch new large trades, profile the wallet, run the rules, alert."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .api import PolymarketClient, Trade
from .classify import is_event_market
from .config import Settings
from .detect import WalletProfile, evaluate, risk_score
from .notify import Alert, Notifier, build_alert
from .store import AlertRow, Store

log = logging.getLogger(__name__)


class SeenSet:
    """Bounded insertion-ordered set of trade keys."""

    def __init__(self, maxlen: int = 20_000):
        self._d: OrderedDict[str, None] = OrderedDict()
        self.maxlen = maxlen

    def add(self, key: str) -> None:
        self._d[key] = None
        self._d.move_to_end(key)
        while len(self._d) > self.maxlen:
            self._d.popitem(last=False)

    def __contains__(self, key: object) -> bool:
        return key in self._d

    def keys(self) -> set[str]:
        return set(self._d)


@dataclass
class Stats:
    polls: int = 0
    trades: int = 0
    skipped_market: int = 0
    alerts: int = 0


class Monitor:
    def __init__(
        self,
        settings: Settings,
        client: PolymarketClient,
        store: Store,
        notifier: Notifier,
        on_alert: Callable[[Alert], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.s = settings
        self.client = client
        self.store = store
        self.notifier = notifier
        self.on_alert = on_alert
        self.clock = clock
        self.seen = SeenSet()
        self.stats = Stats()
        self.oldest_allowed = clock() - timedelta(minutes=settings.backfill_minutes)

    async def prime(self) -> None:
        """Load recently stored trades so a restart doesn't re-alert on them."""
        for key in await self.store.recent_keys():
            self.seen.add(key)

    async def poll_once(self) -> list[Alert]:
        """One polling pass. Returns the alerts it raised."""
        self.stats.polls += 1
        min_usd = 0.5 * self.s.whale_trade_threshold_usd  # lowest size any rule cares about
        trades = await self.client.recent_trades(
            min_usd, stop_at=self.seen.keys(), max_pages=self.s.max_pages_per_poll
        )
        alerts = []
        for trade in reversed(trades):  # oldest first
            if trade.key in self.seen:
                continue
            self.seen.add(trade.key)
            if trade.timestamp < self.oldest_allowed:
                continue
            alert = await self.process(trade)
            if alert:
                alerts.append(alert)
        return alerts

    async def process(self, trade: Trade) -> Alert | None:
        if self.s.market_filter == "events" and not is_event_market(trade.title, trade.slug, trade.event_slug):
            self.stats.skipped_market += 1
            return None
        if await self.store.has_trade(trade.key):
            return None
        self.stats.trades += 1
        now = self.clock()

        stored = await self.store.wallet(trade.wallet)
        first = stored.first_activity if stored else None
        if first is None:
            try:
                first = await self.client.first_activity(trade.wallet)
            except Exception as e:  # noqa: BLE001 - one bad lookup shouldn't stop the loop
                log.warning("Wallet age lookup failed for %s: %s", trade.wallet, e)
        history = await self.store.wallet_trades(trade.wallet, since=now - timedelta(hours=48))
        profile = WalletProfile(first_activity=first, recent_trades=tuple(history))

        market = await self.client.market(trade.condition_id) if trade.condition_id else None
        signals = evaluate(trade, profile, market, now, self.s)
        risk = risk_score(trade, profile, now, self.s)

        alert = build_alert(trade, signals, risk, profile.age_hours(now)) if signals else None
        row = None
        if alert:
            row = AlertRow(
                created_at=now, trade_key=trade.key, wallet=trade.wallet, severity=alert.severity,
                risk_score=risk, rules=",".join(alert.rules), title=alert.title,
                market=alert.market, market_url=alert.market_url, value_usd=trade.value_usd,
            )
        await self.store.record(trade, first, risk, row)

        if alert:
            self.stats.alerts += 1
            if self.on_alert:
                self.on_alert(alert)
            await self.notifier.send(alert)
        return alert

    async def run(self, stop: asyncio.Event) -> None:
        await self.prime()
        while not stop.is_set():
            try:
                await self.poll_once()
            except Exception as e:  # noqa: BLE001 - keep running through API hiccups
                log.error("Poll failed: %s", e)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.s.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass
