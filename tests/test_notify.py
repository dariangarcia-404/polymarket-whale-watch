from conftest import make_trade

from whalewatch.detect import Signal
from whalewatch.notify import build_alert, email_html


def test_untrusted_text_is_escaped_in_email():
    trade = make_trade(title='<script>alert(1)</script> "rates"')
    alert = build_alert(trade, [Signal("whale_trade", "HIGH", "Whale <b>", ["x"])], 70, 5.0)
    out = email_html(alert)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "Whale &lt;b&gt;" in out


def test_alert_takes_highest_severity():
    signals = [Signal("a", "MEDIUM", "m"), Signal("b", "CRITICAL", "c"), Signal("d", "LOW", "l")]
    alert = build_alert(make_trade(), signals, 90, None)
    assert alert.severity == "CRITICAL"
    assert alert.title == "c"
    assert "wallet age unknown" in alert.text
