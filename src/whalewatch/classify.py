"""Decide whether a market is a real-world event (politics, policy, business, etc.)
rather than sports or short-term crypto price action.

Insider information mostly matters for events where someone could know the outcome
early. A 5-minute "Bitcoin up or down" market or a football match is mostly noise
for this tool. Matching is by whole word, so "eu" doesn't match "neutral".
"""

from __future__ import annotations

import re

EVENT_KEYWORDS = [
    # Politics
    "president", "presidential", "election", "elected", "trump", "vance", "democrat",
    "democrats", "republican", "republicans", "congress", "senate", "house", "governor",
    "primary", "nominee", "nomination", "inauguration", "cabinet", "supreme court",
    "legislation", "impeach", "impeachment", "veto", "executive order", "shutdown",
    "minister", "parliament", "prime minister", "chancellor", "referendum", "eu", "nato", "united nations",
    # Geopolitics
    "war", "russia", "ukraine", "putin", "zelensky", "china", "xi", "taiwan", "israel",
    "gaza", "iran", "north korea", "ceasefire", "invasion", "military", "sanctions",
    "treaty", "nuclear", "missile", "strike", "coup",
    # Economy and policy
    "fed", "federal reserve", "interest rate", "rate cut", "rate hike", "inflation", "cpi",
    "recession", "gdp", "tariff", "tariffs", "unemployment", "jobs report",
    # Companies and regulators
    "sec", "doj", "ftc", "antitrust", "lawsuit", "indicted", "indictment", "verdict",
    "ceo", "merger", "acquisition", "acquire", "ipo", "bankruptcy", "earnings",
    "openai", "anthropic", "google", "apple", "microsoft", "amazon", "meta", "nvidia",
    "tesla", "spacex", "musk", "tiktok", "etf",
    # Awards and announcements
    "oscar", "oscars", "emmy", "grammy", "nobel", "time person of the year", "pope",
    "ai", "launch", "release", "announce", "announced",
]

SPORTS_KEYWORDS = [
    "nba", "nfl", "mlb", "nhl", "mls", "wnba", "fifa", "uefa", "premier league", "la liga",
    "serie a", "bundesliga", "ligue 1", "champions league", "atp", "wta", "pga", "ncaa",
    "super bowl", "world series", "stanley cup", "playoffs", "grand slam", "wimbledon",
    "us open", "french open", "australian open", "march madness", "olympics", "world cup",
    "touchdown", "touchdowns", "home run", "home runs", "moneyline", "spread", "halftime",
    "tennis", "golf", "boxing", "ufc", "mma", "f1", "formula 1", "nascar", "cricket",
    "rugby", "hockey", "basketball", "football", "baseball", "soccer", "esports",
]

_SPORTS_PATTERNS = [
    re.compile(r"\bvs\.?\s"),                  # "Lakers vs. Celtics" (but not "U.S. v. Google")
    re.compile(r"\bo/u\b"),                    # over/under lines
    re.compile(r"\bwin on \d{4}-\d{2}-\d{2}\b"),  # "Will X win on 2026-08-29?"
    re.compile(r"\bup or down\b"),             # short-window crypto price markets
    re.compile(r"\b(?:btc|eth|sol|xrp)-updown\b"),
]


def _word_pattern(words: list[str]) -> re.Pattern[str]:
    alternatives = sorted((re.escape(w) for w in words), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(alternatives) + r")\b")


_EVENT = _word_pattern(EVENT_KEYWORDS)
_SPORTS = _word_pattern(SPORTS_KEYWORDS)


def is_event_market(title: str | None, slug: str | None = None, event_slug: str | None = None) -> bool:
    """True for real-world event markets, false for sports and crypto price markets."""
    raw = " ".join(p for p in (title, slug, event_slug) if p).lower()
    words = re.sub(r"[-_]", " ", raw)
    if any(p.search(raw) for p in _SPORTS_PATTERNS) or _SPORTS.search(words):
        return False
    return bool(_EVENT.search(words))
