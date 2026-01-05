"""Polymarket API client for fetching market and trade data."""

import asyncio
import httpx
from datetime import datetime, timedelta
from typing import Optional, AsyncGenerator
from dataclasses import dataclass, field
from tenacity import retry, stop_after_attempt, wait_exponential
from config import settings
import logging

logger = logging.getLogger(__name__)


@dataclass
class MarketData:
    """Market information from Polymarket."""
    id: str
    condition_id: str
    slug: str
    question: str
    description: str
    category: str
    end_date: Optional[datetime]
    created_at: datetime
    volume: float
    liquidity: float
    active: bool
    closed: bool
    tokens: list[dict] = field(default_factory=list)
    
    @property
    def is_political(self) -> bool:
        """Check if market is related to global events (not sports)."""
        text = f"{self.question} {self.description} {self.category}".lower()
        
        # Exclude sports first
        if hasattr(settings, 'excluded_keywords'):
            if any(kw in text for kw in settings.excluded_keywords):
                return False
        
        # Include if matches global event keywords
        return any(kw in text for kw in settings.political_keywords)


@dataclass  
class TradeData:
    """Trade/order information from Polymarket."""
    id: str
    market_id: str
    asset_id: str  # Token ID
    maker_address: str
    taker_address: Optional[str]
    side: str  # BUY/SELL
    outcome: str  # YES/NO
    size: float
    price: float
    timestamp: datetime
    status: str
    transaction_hash: Optional[str] = None
    
    @property
    def value_usd(self) -> float:
        return self.size * self.price


class PolymarketClient:
    """Async client for Polymarket APIs."""
    
    def __init__(self):
        self.gamma_base = settings.polymarket_gamma_api
        self.clob_base = settings.polymarket_clob_api
        self.data_base = settings.polymarket_data_api
        self._client: Optional[httpx.AsyncClient] = None
        
    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_connections=settings.max_concurrent_requests)
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()
    
    @property
    def client(self) -> httpx.AsyncClient:
        if not self._client:
            raise RuntimeError("Client not initialized. Use async context manager.")
        return self._client
    
    def _parse_timestamp(self, ts: Optional[str]) -> Optional[datetime]:
        """Parse ISO timestamp, handling various formats."""
        if not ts:
            return None
        try:
            import re
            # Handle Z suffix
            ts = ts.replace("Z", "+00:00")
            # Normalize microseconds to exactly 6 digits
            # This handles cases like .8422 -> .842200, .15243 -> .152430, .1234567 -> .123456
            def normalize_microseconds(match):
                decimals = match.group(1)
                if len(decimals) > 6:
                    return '.' + decimals[:6]
                else:
                    return '.' + decimals.ljust(6, '0')
            ts = re.sub(r'\.(\d+)', normalize_microseconds, ts)
            return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            # Fallback: try parsing just the date part
            try:
                return datetime.fromisoformat(ts[:19].replace("T", " "))
            except:
                return None
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def _get(self, url: str, params: Optional[dict] = None) -> dict:
        """Make a GET request with retry logic."""
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()
    
    # ============ GAMMA API - Market Discovery ============
    
    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
        category: Optional[str] = None,
    ) -> list[MarketData]:
        """Fetch markets from Gamma API."""
        params = {
            "limit": limit,
            "offset": offset,
            "active": active,
            "closed": closed,
        }
        if category:
            params["tag_slug"] = category
            
        try:
            data = await self._get(f"{self.gamma_base}/markets", params)
            markets = []
            for m in data:
                try:
                    market = MarketData(
                        id=m.get("id", ""),
                        condition_id=m.get("conditionId", ""),
                        slug=m.get("slug", ""),
                        question=m.get("question", ""),
                        description=m.get("description", ""),
                        category=m.get("groupItemTitle", "") or m.get("category", ""),
                        end_date=self._parse_timestamp(m.get("endDate")),
                        created_at=self._parse_timestamp(m.get("createdAt")) or datetime.utcnow(),
                        volume=float(m.get("volume", 0) or 0),
                        liquidity=float(m.get("liquidity", 0) or 0),
                        active=m.get("active", False),
                        closed=m.get("closed", False),
                        tokens=m.get("tokens", []),
                    )
                    markets.append(market)
                except Exception as e:
                    logger.warning(f"Failed to parse market: {e}")
                    continue
            return markets
        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []
    
    async def get_political_markets(self) -> list[MarketData]:
        """Fetch all active political markets."""
        all_markets = []
        offset = 0
        limit = 100
        
        while True:
            markets = await self.get_markets(limit=limit, offset=offset)
            if not markets:
                break
            all_markets.extend(markets)
            if len(markets) < limit:
                break
            offset += limit
            await asyncio.sleep(0.1)  # Rate limiting
        
        # Filter for political markets
        political = [m for m in all_markets if m.is_political]
        logger.info(f"Found {len(political)} political markets out of {len(all_markets)} total")
        return political
    
    async def get_market_by_id(self, market_id: str) -> Optional[MarketData]:
        """Fetch a specific market by ID."""
        try:
            data = await self._get(f"{self.gamma_base}/markets/{market_id}")
            return MarketData(
                id=data.get("id", ""),
                condition_id=data.get("conditionId", ""),
                slug=data.get("slug", ""),
                question=data.get("question", ""),
                description=data.get("description", ""),
                category=data.get("groupItemTitle", "") or data.get("category", ""),
                end_date=self._parse_timestamp(data.get("endDate")),
                created_at=self._parse_timestamp(data.get("createdAt")) or datetime.utcnow(),
                volume=float(data.get("volume", 0) or 0),
                liquidity=float(data.get("liquidity", 0) or 0),
                active=data.get("active", False),
                closed=data.get("closed", False),
                tokens=data.get("tokens", []),
            )
        except Exception as e:
            logger.error(f"Failed to fetch market {market_id}: {e}")
            return None
    
    # ============ CLOB API - Order Book & Trades ============
    
    async def get_order_book(self, token_id: str) -> dict:
        """Get order book for a specific token."""
        try:
            return await self._get(f"{self.clob_base}/book", params={"token_id": token_id})
        except Exception as e:
            logger.error(f"Failed to fetch order book for {token_id}: {e}")
            return {"bids": [], "asks": []}
    
    async def get_trades(
        self,
        market_id: Optional[str] = None,
        maker: Optional[str] = None,
        before: Optional[str] = None,
        after: Optional[str] = None,
        limit: int = 100,
    ) -> list[TradeData]:
        """Fetch recent trades from Gamma API (public, no auth required)."""
        # Use Gamma API activity endpoint which is public
        params = {"limit": limit}
        
        try:
            # Try Gamma API for market activity (public endpoint)
            if market_id:
                data = await self._get(f"{self.gamma_base}/markets/{market_id}")
                # Parse recent activity from market data
                trades = []
                activity = data.get("activity", []) or data.get("recentActivity", [])
                for t in activity[:limit]:
                    try:
                        trade = TradeData(
                            id=t.get("id", str(hash(str(t)))),
                            market_id=market_id,
                            asset_id=t.get("asset_id", "") or t.get("tokenId", ""),
                            maker_address=t.get("user", "") or t.get("maker", "") or t.get("address", ""),
                            taker_address=None,
                            side=t.get("side", "BUY") or ("BUY" if t.get("type") == "buy" else "SELL"),
                            outcome=t.get("outcome", "YES"),
                            size=float(t.get("size", 0) or t.get("amount", 0) or t.get("shares", 0)),
                            price=float(t.get("price", 0) or t.get("avgPrice", 0)),
                            timestamp=self._parse_timestamp(t.get("timestamp") or t.get("createdAt")) or datetime.utcnow(),
                            status="MATCHED",
                            transaction_hash=t.get("transactionHash") or t.get("txHash"),
                        )
                        if trade.maker_address and trade.size > 0:
                            trades.append(trade)
                    except Exception as e:
                        continue
                return trades
            else:
                # Get global activity
                data = await self._get(f"{self.gamma_base}/activity", params)
                trades = []
                items = data if isinstance(data, list) else data.get("data", []) or data.get("activity", [])
                for t in items[:limit]:
                    try:
                        trade = TradeData(
                            id=t.get("id", str(hash(str(t)))),
                            market_id=t.get("market", "") or t.get("marketId", "") or t.get("conditionId", ""),
                            asset_id=t.get("asset_id", "") or t.get("tokenId", ""),
                            maker_address=t.get("user", "") or t.get("maker", "") or t.get("address", ""),
                            taker_address=None,
                            side=t.get("side", "BUY") or ("BUY" if t.get("type") == "buy" else "SELL"),
                            outcome=t.get("outcome", "YES"),
                            size=float(t.get("size", 0) or t.get("amount", 0) or t.get("shares", 0)),
                            price=float(t.get("price", 0) or t.get("avgPrice", 0)),
                            timestamp=self._parse_timestamp(t.get("timestamp") or t.get("createdAt")) or datetime.utcnow(),
                            status="MATCHED",
                            transaction_hash=t.get("transactionHash") or t.get("txHash"),
                        )
                        if trade.maker_address and trade.size > 0:
                            trades.append(trade)
                    except Exception as e:
                        continue
                return trades
        except Exception as e:
            logger.debug(f"Gamma API activity failed: {e}, trying alternative...")
            
        # Fallback: try to get trades from strapi/data endpoints
        try:
            params = {"_limit": limit, "_sort": "createdAt:desc"}
            if maker:
                params["user"] = maker
            data = await self._get(f"{self.gamma_base}/trades", params)
            trades = []
            for t in data if isinstance(data, list) else []:
                try:
                    trade = TradeData(
                        id=t.get("id", ""),
                        market_id=t.get("market", {}).get("id", "") if isinstance(t.get("market"), dict) else t.get("market", ""),
                        asset_id=t.get("tokenId", ""),
                        maker_address=t.get("user", ""),
                        taker_address=None,
                        side=t.get("side", "BUY"),
                        outcome=t.get("outcome", "YES"),
                        size=float(t.get("size", 0) or t.get("amount", 0)),
                        price=float(t.get("price", 0)),
                        timestamp=self._parse_timestamp(t.get("createdAt")) or datetime.utcnow(),
                        status="MATCHED",
                        transaction_hash=t.get("transactionHash"),
                    )
                    if trade.maker_address:
                        trades.append(trade)
                except Exception:
                    continue
            return trades
        except Exception as e:
            logger.error(f"Failed to fetch trades: {e}")
            return []
    
    async def get_large_trades(
        self,
        min_value_usd: float = None,
        markets: Optional[list[str]] = None,
    ) -> list[TradeData]:
        """Fetch trades above a certain USD value."""
        min_value = min_value_usd or settings.whale_trade_threshold_usd
        all_trades = []
        
        if markets:
            for market_id in markets:
                trades = await self.get_trades(market_id=market_id, limit=500)
                all_trades.extend(trades)
                await asyncio.sleep(0.05)
        else:
            all_trades = await self.get_trades(limit=500)
        
        return [t for t in all_trades if t.value_usd >= min_value]
    
    # ============ Data API - User Positions ============
    
    async def get_user_positions(self, address: str) -> list[dict]:
        """Get user's current positions."""
        try:
            data = await self._get(f"{self.data_base}/positions", params={"user": address})
            return data if isinstance(data, list) else data.get("positions", [])
        except Exception as e:
            logger.error(f"Failed to fetch positions for {address}: {e}")
            return []
    
    async def get_user_trade_history(
        self,
        address: str,
        limit: int = 100,
    ) -> list[TradeData]:
        """Get a user's trade history."""
        return await self.get_trades(maker=address, limit=limit)
    
    async def estimate_wallet_age(self, address: str) -> Optional[datetime]:
        """Estimate wallet age based on first trade."""
        trades = await self.get_user_trade_history(address, limit=1000)
        if trades:
            oldest = min(trades, key=lambda t: t.timestamp)
            return oldest.timestamp
        return None
    
    # ============ Streaming / Real-time ============
    
    async def stream_trades(
        self,
        markets: Optional[list[str]] = None,
        min_value_usd: float = 0,
    ) -> AsyncGenerator[TradeData, None]:
        """Stream new trades from the Polymarket data API."""
        seen_trades: set[str] = set()
        political_slugs = set()
        
        # Build set of political market slugs for filtering
        if markets:
            for m in self.political_markets_cache if hasattr(self, 'political_markets_cache') else []:
                if hasattr(m, 'slug') and m.slug:
                    political_slugs.add(m.slug.lower())
        
        logger.info(f"Starting trade stream, min value: ${min_value_usd}")
        
        while True:
            try:
                # Fetch recent trades from the public data API
                trades = await self._fetch_recent_trades(limit=100)
                
                for trade in trades:
                    # Skip if already seen
                    if trade.id in seen_trades:
                        continue
                    
                    # Check if trade meets minimum value
                    if trade.value_usd < min_value_usd:
                        continue
                    
                    # Check if it's a global event market (not sports)
                    if hasattr(trade, 'market_slug') or hasattr(trade, 'market_title'):
                        text = f"{getattr(trade, 'market_slug', '')} {getattr(trade, 'market_title', '')}".lower()
                        
                        # Exclude sports
                        if hasattr(settings, 'excluded_keywords'):
                            if any(kw in text for kw in settings.excluded_keywords):
                                continue
                        
                        # Must match at least one global event keyword
                        is_global_event = any(kw in text for kw in settings.political_keywords)
                        if not is_global_event:
                            continue
                    
                    seen_trades.add(trade.id)
                    logger.info(f"New trade: ${trade.value_usd:.2f} on {trade.market_slug[:30] if trade.market_slug else 'unknown'}...")
                    yield trade
                
                # Clean old entries
                if len(seen_trades) > 50000:
                    seen_trades = set(list(seen_trades)[-25000:])
                
                await asyncio.sleep(settings.poll_interval_seconds)
                
            except Exception as e:
                logger.error(f"Error in trade stream: {e}")
                await asyncio.sleep(5)
    
    async def _fetch_recent_trades(self, limit: int = 100) -> list[TradeData]:
        """Fetch recent trades from the Polymarket data API."""
        try:
            data = await self._get(f"{self.data_base}/trades", params={"limit": limit})
            trades = []
            
            for t in data if isinstance(data, list) else []:
                try:
                    size = float(t.get("size", 0))
                    price = float(t.get("price", 0))
                    
                    trade = TradeData(
                        id=t.get("transactionHash", "") or str(t.get("timestamp", "")),
                        market_id=t.get("conditionId", ""),
                        asset_id=t.get("asset", ""),
                        maker_address=t.get("proxyWallet", ""),
                        taker_address=None,
                        side=t.get("side", "BUY"),
                        outcome=t.get("outcome", "YES"),
                        size=size,
                        price=price,
                        timestamp=datetime.fromtimestamp(t.get("timestamp", 0)) if t.get("timestamp") else datetime.utcnow(),
                        status="MATCHED",
                        transaction_hash=t.get("transactionHash"),
                    )
                    # Store extra fields for filtering
                    trade.market_slug = t.get("slug", "")
                    trade.market_title = t.get("title", "")
                    
                    if trade.maker_address and size > 0:
                        trades.append(trade)
                except Exception as e:
                    logger.debug(f"Failed to parse trade: {e}")
                    continue
            
            return trades
        except Exception as e:
            logger.error(f"Failed to fetch trades from data API: {e}")
            return []

