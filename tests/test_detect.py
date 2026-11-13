from datetime import timedelta

from conftest import NOW, make_trade

from whalewatch.api import Market
from whalewatch.detect import (
    WalletProfile, bets_on_longshot, evaluate, overall_severity, risk_score,
)

OLD = WalletProfile(first_activity=NOW - timedelta(days=400))
FRESH = WalletProfile(first_activity=NOW - timedelta(hours=3))


def rules(signals):
    return {s.rule for s in signals}


def test_small_trade_from_old_wallet_is_quiet(settings):
    assert evaluate(make_trade(value=1_000), OLD, None, NOW, settings) == []


def test_whale_severity_scales_with_size(settings):
    sev = lambda v: evaluate(make_trade(value=v), OLD, None, NOW, settings)[0].severity
    assert sev(5_000) == "MEDIUM"
    assert sev(25_000) == "HIGH"
    assert sev(100_000) == "CRITICAL"


def test_wallet_age_comes_from_first_activity_not_first_sighting(settings):
    # An old wallet we have never seen before must not count as new.
    assert "fresh_wallet_large_bet" not in rules(evaluate(make_trade(value=4_000), OLD, None, NOW, settings))
    assert "fresh_wallet_large_bet" in rules(evaluate(make_trade(value=4_000), FRESH, None, NOW, settings))


def test_unknown_wallet_age_is_not_treated_as_fresh(settings):
    unknown = WalletProfile(first_activity=None)
    assert "fresh_wallet_large_bet" not in rules(evaluate(make_trade(value=8_000), unknown, None, NOW, settings))


def test_fresh_wallet_longshot_is_critical(settings):
    signals = evaluate(make_trade(value=6_000, price=0.06, outcome="No"), FRESH, None, NOW, settings)
    assert "fresh_wallet_longshot" in rules(signals)
    assert overall_severity(signals) == "CRITICAL"


def test_price_is_the_traded_outcome_probability(settings):
    # Buying "No" at 0.94 is backing the favourite, whatever the outcome is called.
    assert not bets_on_longshot(make_trade(price=0.94, outcome="No"), settings)
    assert bets_on_longshot(make_trade(price=0.04, outcome="No"), settings)
    assert bets_on_longshot(make_trade(price=0.95, side="SELL"), settings)


def test_pre_resolution_accumulation(settings):
    market = Market("0xcond", "Fed?", end_date=NOW + timedelta(hours=20), closed=False)
    earlier = tuple(make_trade(value=2_000, when=NOW - timedelta(hours=h), tx=f"0x{h}") for h in (5, 2))
    p = WalletProfile(first_activity=NOW - timedelta(days=30), recent_trades=earlier)
    assert "pre_resolution_accumulation" in rules(evaluate(make_trade(value=2_000), p, market, NOW, settings))

    far = Market("0xcond", "Fed?", end_date=NOW + timedelta(days=30), closed=False)
    assert evaluate(make_trade(value=2_000), p, far, NOW, settings) == []

    opposite = tuple(make_trade(value=2_000, outcome="No", tx=f"0x{h}") for h in (5, 2))
    p2 = WalletProfile(first_activity=p.first_activity, recent_trades=opposite)
    assert evaluate(make_trade(value=2_000), p2, market, NOW, settings) == []


def test_quiet_hours_rule_fires_inside_window_only(settings):
    young = WalletProfile(first_activity=NOW - timedelta(days=5))
    at = lambda h: make_trade(value=6_000, when=NOW.replace(hour=h))
    assert "quiet_hours_trade" in rules(evaluate(at(7), young, None, NOW, settings))
    assert "quiet_hours_trade" not in rules(evaluate(at(15), young, None, NOW, settings))


def test_risk_score_bounds_and_ordering(settings):
    quiet = risk_score(make_trade(value=500), OLD, NOW, settings)
    loud = risk_score(make_trade(value=60_000, price=0.03), FRESH, NOW, settings)
    assert 0 <= quiet < 10
    assert 80 <= loud <= 100


def test_fresh_wallet_severity_depends_on_age_and_size(settings):
    sev = lambda hours, value: next(
        s.severity for s in evaluate(make_trade(value=value), WalletProfile(NOW - timedelta(hours=hours)), None, NOW, settings)
        if s.rule == "fresh_wallet_large_bet"
    )
    assert sev(60, 3_000) == "MEDIUM"
    assert sev(60, 12_000) == "HIGH"
    assert sev(5, 3_000) == "HIGH"
    assert sev(5, 12_000) == "CRITICAL"
