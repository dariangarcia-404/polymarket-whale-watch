import pytest

from whalewatch.classify import is_event_market


@pytest.mark.parametrize("title", [
    "Will the Fed cut interest rates in December?",
    "Xi Jinping out before 2027?",
    "Will Trump sign the tariff executive order by Friday?",
    "Russia x Ukraine ceasefire in 2026?",
    "Will OpenAI announce GPT-6 this year?",
    "U.S. v. Google remedies decision by March?",
    "Will the EU fine Apple?",
])
def test_event_markets_are_kept(title):
    assert is_event_market(title)


@pytest.mark.parametrize("title,slug", [
    ("Lakers vs. Celtics", "nba-lal-bos"),
    ("Bitcoin Up or Down - November 12, 11:40PM-11:45PM ET", "btc-updown-5m-1794544800"),
    ("Will IF Elfsborg win on 2026-08-29?", "swe-elf-deg-2026-08-29-elf"),
    ("Chiefs O/U 47.5", ""),
    ("Who will win the Super Bowl?", ""),
])
def test_sports_and_price_markets_are_skipped(title, slug):
    assert not is_event_market(title, slug)


@pytest.mark.parametrize("title", [
    "Will it snow in a neutral zone?",   # 'eu' inside 'neutral'
    "What will he have said?",           # 'ai' inside 'said'
    "Highest temperature in Denver today?",
])
def test_substrings_do_not_match(title):
    assert not is_event_market(title)
