"""Detection rules. Pure functions with no I/O, so they are easy to test and tune.

Each rule looks at one trade plus what we know about the wallet and market, and may
return a Signal. None of these prove insider trading; they flag trades worth a look.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .api import Market, Trade
from .config import Settings

SEVERITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


@dataclass(frozen=True)
class WalletProfile:
    first_activity: datetime | None  # first on-chain Polymarket activity
    recent_trades: tuple[Trade, ...] = ()  # this wallet's earlier trades we have stored

    def age_hours(self, now: datetime) -> float | None:
        if self.first_activity is None:
            return None
        return max(0.0, (now - self.first_activity).total_seconds() / 3600)


@dataclass(frozen=True)
class Signal:
    rule: str
    severity: str
    title: str
    reasons: list[str] = field(default_factory=list)


def _usd(x: float) -> str:
    return f"${x:,.0f}"


def format_age(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    return f"{hours:.1f} h" if hours < 48 else f"{hours / 24:.0f} days"


def is_fresh(profile: WalletProfile, now: datetime, s: Settings) -> bool:
    age = profile.age_hours(now)
    return age is not None and age < s.new_wallet_age_hours


def bets_on_longshot(trade: Trade, s: Settings) -> bool:
    """Buying an outcome priced under the longshot threshold, or selling one priced
    above 1 - threshold (which is the same bet from the other side)."""
    if trade.side == "BUY":
        return trade.price < s.longshot_price
    return trade.side == "SELL" and trade.price > 1 - s.longshot_price


def whale_trade(trade: Trade, s: Settings) -> Signal | None:
    t = s.whale_trade_threshold_usd
    v = trade.value_usd
    if v < t:
        return None
    severity = "CRITICAL" if v >= 20 * t else "HIGH" if v >= 5 * t else "MEDIUM"
    return Signal("whale_trade", severity, f"Whale trade: {_usd(v)}", [f"{_usd(v)} is over the {_usd(t)} threshold"])


def fresh_wallet_large_bet(trade: Trade, p: WalletProfile, now: datetime, s: Settings) -> Signal | None:
    t = s.whale_trade_threshold_usd
    if not is_fresh(p, now, s) or trade.value_usd < 0.5 * t:
        return None
    age = p.age_hours(now) or 0.0
    very_new = age < s.new_wallet_age_hours / 3
    if very_new and trade.value_usd >= 2 * t:
        severity = "CRITICAL"
    elif very_new or trade.value_usd >= 2 * t:
        severity = "HIGH"
    else:
        severity = "MEDIUM"
    return Signal(
        "fresh_wallet_large_bet",
        severity,
        f"New wallet, large bet: {_usd(trade.value_usd)}",
        [f"Wallet's first Polymarket activity was {format_age(age)} ago"],
    )


def fresh_wallet_longshot(trade: Trade, p: WalletProfile, now: datetime, s: Settings) -> Signal | None:
    if not is_fresh(p, now, s) or trade.value_usd < s.whale_trade_threshold_usd:
        return None
    if not bets_on_longshot(trade, s):
        return None
    implied = trade.price if trade.side == "BUY" else 1 - trade.price
    return Signal(
        "fresh_wallet_longshot",
        "CRITICAL",
        f"New wallet backs a long shot: {_usd(trade.value_usd)}",
        [f"Market gives this outcome {implied:.0%}"],
    )


def pre_resolution_accumulation(
    trade: Trade, p: WalletProfile, market: Market | None, now: datetime, s: Settings
) -> Signal | None:
    if market is None or market.end_date is None or market.closed:
        return None
    hours_left = (market.end_date - now).total_seconds() / 3600
    if not 0 <= hours_left <= s.pre_resolution_hours:
        return None
    window = now - timedelta(hours=24)
    same = [
        t for t in p.recent_trades
        if t.condition_id == trade.condition_id and t.timestamp >= window
        and t.side == trade.side and t.outcome == trade.outcome
    ]
    if len(same) < 2:
        return None
    total = trade.value_usd + sum(t.value_usd for t in same)
    if total < s.whale_trade_threshold_usd:
        return None
    return Signal(
        "pre_resolution_accumulation",
        "HIGH",
        f"Building a position before resolution: {_usd(total)}",
        [f"{len(same) + 1} {trade.side} {trade.outcome} trades in 24 h", f"Market ends in {hours_left:.1f} h"],
    )


def quiet_hours_trade(trade: Trade, p: WalletProfile, now: datetime, s: Settings) -> Signal | None:
    start, end = s.quiet_hours_utc
    hour = trade.timestamp.hour
    in_window = start <= hour <= end if start <= end else (hour >= start or hour <= end)
    if not in_window or trade.value_usd < s.whale_trade_threshold_usd:
        return None
    age = p.age_hours(now)
    if age is None or age >= 2 * s.new_wallet_age_hours:
        return None
    return Signal(
        "quiet_hours_trade",
        "LOW",
        f"Large trade during quiet hours: {_usd(trade.value_usd)}",
        [f"Placed at {trade.timestamp:%H:%M} UTC by a wallet {format_age(age)} old"],
    )


def evaluate(trade: Trade, p: WalletProfile, market: Market | None, now: datetime, s: Settings) -> list[Signal]:
    # Most specific first: on a severity tie, the first signal supplies the alert title.
    candidates = [
        fresh_wallet_longshot(trade, p, now, s),
        pre_resolution_accumulation(trade, p, market, now, s),
        fresh_wallet_large_bet(trade, p, now, s),
        whale_trade(trade, s),
        quiet_hours_trade(trade, p, now, s),
    ]
    return [c for c in candidates if c is not None]


def risk_score(trade: Trade, p: WalletProfile, now: datetime, s: Settings) -> int:
    """0 to 100. Weighs wallet freshness, size, long-shot pricing, and recent activity."""
    score = 0.0
    age = p.age_hours(now)
    if age is not None and age < s.new_wallet_age_hours:
        score += 40 * (1 - age / s.new_wallet_age_hours)
    score += 25 * min(trade.value_usd / (4 * s.whale_trade_threshold_usd), 1.0)
    if bets_on_longshot(trade, s):
        score += 20
    day_ago = now - timedelta(hours=24)
    recent = sum(1 for t in p.recent_trades if t.timestamp >= day_ago)
    score += min(15, 3 * recent)
    return round(min(score, 100))


def overall_severity(signals: list[Signal]) -> str:
    return max((sig.severity for sig in signals), key=SEVERITY_ORDER.__getitem__, default="LOW")
