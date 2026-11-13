"""Async client for Polymarket's public Data API (v2) and Gamma API. No API key needed."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Trade:
    """One fill from the public trade feed. ``price`` is the price of the outcome
    token that was traded, so it is also that outcome's implied probability."""

    wallet: str
    side: str            # BUY or SELL
    outcome: str         # e.g. "Yes", "No", or a candidate name
    size: float          # outcome tokens
    price: float         # USDC per token, 0 to 1
    timestamp: datetime  # UTC
    condition_id: str
    token_id: str
    title: str = ""
    slug: str = ""
    event_slug: str = ""
    trader_name: str = ""
    transaction_hash: str = ""

    @property
    def value_usd(self) -> float:
        return self.size * self.price

    @property
    def key(self) -> str:
        """Stable ID. A transaction can hold several fills, so the hash alone isn't unique."""
        return f"{self.transaction_hash}:{self.token_id}:{self.wallet}:{self.side}:{self.size}:{self.price}"

    @property
    def market_url(self) -> str:
        slug = self.event_slug or self.slug
        return f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"

    @property
    def wallet_url(self) -> str:
        return f"https://polymarket.com/profile/{self.wallet}"

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Trade:
        return cls(
            wallet=str(d["proxy_wallet"]).lower(),
            side=str(d.get("side", "")).upper(),
            outcome=str(d.get("outcome", "")),
            size=float(d["size"]),
            price=float(d["price"]),
            timestamp=datetime.fromtimestamp(int(d["timestamp"]), tz=timezone.utc),
            condition_id=str(d.get("condition_id", "")),
            token_id=str(d.get("token_id", "")),
            title=str(d.get("title") or ""),
            slug=str(d.get("slug") or ""),
            event_slug=str(d.get("event_slug") or ""),
            trader_name=str(d.get("pseudonym") or d.get("name") or ""),
            transaction_hash=str(d.get("transaction_hash") or ""),
        )


@dataclass(frozen=True)
class Market:
    condition_id: str
    question: str
    end_date: datetime | None
    closed: bool


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class PolymarketClient:
    def __init__(self, data_api: str, gamma_api: str, http: httpx.AsyncClient | None = None):
        self.data_api = data_api.rstrip("/")
        self.gamma_api = gamma_api.rstrip("/")
        self._http = http or httpx.AsyncClient(
            timeout=20.0, headers={"User-Agent": "polymarket-whale-watch"}
        )
        self._markets: dict[str, Market | None] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> PolymarketClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _get(self, url: str, params: dict[str, Any]) -> Any:
        delay = 1.0
        for attempt in range(4):
            try:
                r = await self._http.get(url, params=params)
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                retryable = not isinstance(e, httpx.HTTPStatusError) or (
                    e.response.status_code == 429 or e.response.status_code >= 500
                )
                if not retryable or attempt == 3:
                    raise
                log.debug("Retrying %s after %s", url, e)
                await asyncio.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    async def recent_trades(
        self, min_usd: float, stop_at: set[str] | None = None, max_pages: int = 5
    ) -> list[Trade]:
        """Newest trades worth at least ``min_usd``, newest first. Pages back until a
        trade in ``stop_at`` (already seen) appears, or ``max_pages`` is reached."""
        params: dict[str, Any] = {"limit": 100, "filter_type": "CASH", "filter_amount": min_usd}
        trades: list[Trade] = []
        for _ in range(max_pages):
            body = await self._get(f"{self.data_api}/trades", params)
            page_done = False
            for raw in body.get("data", []):
                try:
                    trade = Trade.from_api(raw)
                except (KeyError, TypeError, ValueError) as e:
                    log.debug("Skipping malformed trade: %s", e)
                    continue
                if stop_at and trade.key in stop_at:
                    page_done = True
                    break
                trades.append(trade)
            cursor = body.get("pagination", {}).get("next_cursor")
            if page_done or not cursor or not body.get("pagination", {}).get("has_more"):
                break
            params = {**params, "cursor": cursor}
        return trades

    async def first_activity(self, wallet: str) -> datetime | None:
        """When the wallet first did anything on Polymarket (trade, split, redeem...)."""
        body = await self._get(
            f"{self.data_api}/activity",
            {"user": wallet, "limit": 1, "sort_direction": "ASC"},
        )
        rows = body.get("data", [])
        if not rows:
            return None
        return datetime.fromtimestamp(int(rows[0]["timestamp"]), tz=timezone.utc)

    async def market(self, condition_id: str) -> Market | None:
        """Market details by condition ID (cached for the life of the client)."""
        if condition_id in self._markets:
            return self._markets[condition_id]
        market = None
        try:
            rows = await self._get(f"{self.gamma_api}/markets", {"condition_ids": condition_id})
            if rows:
                m = rows[0]
                market = Market(
                    condition_id=condition_id,
                    question=m.get("question") or "",
                    end_date=_parse_iso(m.get("endDate")),
                    closed=bool(m.get("closed")),
                )
        except httpx.HTTPError as e:
            log.warning("Market lookup failed for %s: %s", condition_id, e)
            return None  # don't cache failures
        self._markets[condition_id] = market
        return market
