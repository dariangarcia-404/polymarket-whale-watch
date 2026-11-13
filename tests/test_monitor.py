"""End to end: fake Polymarket HTTP API -> Monitor -> SQLite -> alerts."""

from datetime import timedelta

import httpx
import pytest
from conftest import NOW, api_trade, make_trade

from whalewatch.api import PolymarketClient
from whalewatch.monitor import Monitor
from whalewatch.notify import Notifier
from whalewatch.store import Store


class FakePolymarket:
    def __init__(self):
        self.trades = []          # newest first, like the real API
        self.first_activity = {}  # wallet -> datetime
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        q = request.url.params
        if request.url.path == "/v2/trades":
            min_usd = float(q["filter_amount"])
            rows = [api_trade(t) for t in self.trades if t.value_usd >= min_usd]
            return httpx.Response(200, json={"data": rows, "pagination": {"has_more": False}})
        if request.url.path == "/v2/activity":
            first = self.first_activity.get(q["user"])
            rows = [{"timestamp": int(first.timestamp())}] if first else []
            return httpx.Response(200, json={"data": rows})
        if request.url.path == "/markets":
            return httpx.Response(200, json=[{"question": "Fed?", "endDate": "2027-01-01T00:00:00Z", "closed": False}])
        return httpx.Response(404)


@pytest.fixture
async def env(settings, tmp_path):
    settings.database_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    fake = FakePolymarket()
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    client = PolymarketClient("https://data.test/v2", "https://gamma.test", http=http)
    store = Store(settings.database_url)
    await store.init()
    seen = []
    mon = Monitor(settings, client, store, Notifier(settings), on_alert=seen.append, clock=lambda: NOW)
    yield fake, mon, store, seen
    await client.aclose()
    await store.close()


async def test_fresh_wallet_whale_alerts_once(env):
    fake, mon, store, seen = env
    fresh = "0xf00000000000000000000000000000000000000d"
    fake.first_activity[fresh] = NOW - timedelta(hours=2)
    fake.trades = [make_trade(value=12_000, price=0.07, wallet=fresh, tx="0x1")]

    alerts = await mon.poll_once()
    assert len(alerts) == 1
    assert alerts[0].severity == "CRITICAL"
    assert {"whale_trade", "fresh_wallet_large_bet", "fresh_wallet_longshot"} <= set(alerts[0].rules)

    # Same trade on the next poll and after a restart: no duplicate alert.
    assert await mon.poll_once() == []
    mon2 = Monitor(mon.s, mon.client, store, mon.notifier, clock=lambda: NOW)
    await mon2.prime()
    assert await mon2.poll_once() == []

    [row] = await store.alerts()
    assert row.wallet == fresh and row.risk_score >= 70
    [wallet] = await store.top_wallets()
    assert wallet.first_activity == NOW - timedelta(hours=2)


async def test_old_wallet_and_sports_market_are_filtered(env):
    fake, mon, store, seen = env
    old = "0x0100000000000000000000000000000000000000"
    fake.first_activity[old] = NOW - timedelta(days=900)
    fake.trades = [
        make_trade(value=3_000, wallet=old, tx="0xa"),                          # small, old: nothing
        make_trade(value=50_000, wallet=old, tx="0xb", title="Lakers vs. Celtics"),  # sports: skipped
    ]
    assert await mon.poll_once() == []
    assert mon.stats.skipped_market == 1
    assert mon.stats.trades == 1
    # Wallet age is looked up once and then cached in the database.
    fake.trades.insert(0, make_trade(value=3_100, wallet=old, tx="0xc"))
    await mon.poll_once()
    assert fake.calls.count("/v2/activity") == 1


async def test_trades_filtered_server_side_by_size(env, settings):
    fake, mon, store, seen = env
    fake.trades = [make_trade(value=100, tx="0xtiny")]
    await mon.poll_once()
    assert mon.stats.trades == 0


async def test_startup_ignores_old_backlog(env):
    fake, mon, store, seen = env
    fake.trades = [make_trade(value=50_000, when=NOW - timedelta(hours=3), tx="0xold")]
    assert await mon.poll_once() == []
    assert mon.stats.trades == 0
