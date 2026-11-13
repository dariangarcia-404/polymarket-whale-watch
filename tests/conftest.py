from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from whalewatch.api import Trade
from whalewatch.config import Settings

NOW = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, enable_desktop_notifications=False, database_url="sqlite+aiosqlite:///:memory:")


def make_trade(
    value: float = 10_000,
    price: float = 0.5,
    side: str = "BUY",
    outcome: str = "Yes",
    wallet: str = "0xabc0000000000000000000000000000000000001",
    when: datetime = NOW - timedelta(minutes=1),
    title: str = "Will the Fed cut interest rates in December?",
    condition_id: str = "0xcond",
    tx: str = "0xtx",
) -> Trade:
    return Trade(
        wallet=wallet, side=side, outcome=outcome, size=value / price, price=price,
        timestamp=when, condition_id=condition_id, token_id="123", title=title,
        slug="fed-december", event_slug="fed-december", transaction_hash=tx,
    )


def api_trade(trade: Trade) -> dict:
    """The JSON shape of one row from GET /v2/trades."""
    return {
        "proxy_wallet": trade.wallet, "side": trade.side, "token_id": trade.token_id,
        "condition_id": trade.condition_id, "size": trade.size, "price": trade.price,
        "timestamp": int(trade.timestamp.timestamp()), "title": trade.title, "slug": trade.slug,
        "event_slug": trade.event_slug, "outcome": trade.outcome, "name": "", "pseudonym": "Brave-Otter",
        "transaction_hash": trade.transaction_hash,
    }
