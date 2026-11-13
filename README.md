# Polymarket Whale Watch

[![tests](https://github.com/dariangarcia-404/polymarket-whale-watch/actions/workflows/tests.yml/badge.svg)](https://github.com/dariangarcia-404/polymarket-whale-watch/actions/workflows/tests.yml)

Watches Polymarket's public trade feed for the kinds of bets that tend to come before
surprising news: large positions from wallets that didn't exist a few days ago, big bets
on long shots, and positions built up just before a market resolves. It alerts you on
your desktop, by SMS, email, or Pushover.

It only reads public data. It doesn't need a Polymarket account or API key, and it never trades.

```
╭─ CRITICAL  New wallet backs a long shot: $12,000 ─────────────────────╮
│ Market: Will the Fed cut interest rates in December?                  │
│ Trade: BUY Yes at 7%, $12,000                                         │
│ Trader: Brave-Otter (wallet age 2.0 h)                                │
│ Market gives this outcome 7%                                          │
│ Wallet's first Polymarket activity was 2.0 h ago                      │
│ $12,000 is over the $5,000 threshold                                  │
│ Risk score: 74/100                                                    │
╰───────────────────────────────────────────────────────────────────────╯
```

## How it works

Every 30 seconds it asks the Polymarket [Data API](https://docs.polymarket.com) for new
trades above half the whale threshold. The API filters by size on the server, so
small trades never cross the wire. For each new trade it:

1. **Filters by market.** It keeps real-world event markets (politics, geopolitics,
   central banks, companies, regulators, awards). It skips sports and short-window
   crypto price markets, where an insider edge is unlikely and noise is high. Keywords
   match whole words only, so "EU" doesn't match "neutral". Use `--all-markets` to turn this off.
2. **Profiles the wallet.** It finds the wallet's *first ever* Polymarket activity, so
   "new wallet" means new to Polymarket, not just new to this tool. Each wallet is looked
   up once and cached in SQLite.
3. **Runs the rules** below and computes a 0–100 risk score.
4. **Alerts** once per trade, at the highest severity any rule assigns, and stores it.

| Rule | Fires when | Severity |
| --- | --- | --- |
| `whale_trade` | Trade ≥ threshold ($5k default) | MEDIUM; HIGH at 5×; CRITICAL at 20× |
| `fresh_wallet_large_bet` | Wallet < 72 h old and trade ≥ ½ threshold | MEDIUM to CRITICAL, by age and size |
| `fresh_wallet_longshot` | Wallet < 72 h old buys an outcome priced < 10% (or sells one > 90%) with ≥ threshold | CRITICAL |
| `pre_resolution_accumulation` | 3+ same-direction trades on one market in 24 h, totalling ≥ threshold, with < 48 h until it resolves | HIGH |
| `quiet_hours_trade` | Trade ≥ threshold between 05:00 and 10:59 UTC from a wallet < 6 days old | LOW |

The risk score adds up wallet freshness (up to 40), size (up to 25), long-shot pricing (20),
and how active the wallet has been in the last day (up to 15).

## Install

Requires Python 3.10+.

```bash
git clone https://github.com/dariangarcia-404/polymarket-whale-watch
cd polymarket-whale-watch
python -m venv .venv && source .venv/bin/activate
pip install -e '.[all]'      # or just '.' if you only want desktop alerts
cp .env.example .env         # optional; edit thresholds and alert channels
```

## Use

```bash
whalewatch monitor                     # run until Ctrl-C
whalewatch monitor -t 10000 -i 15      # $10k threshold, poll every 15 s
whalewatch alerts -s CRITICAL          # recent alerts from the database
whalewatch wallets                     # highest-risk wallets seen
whalewatch test-alert                  # check that your alert channels work
whalewatch config                      # show settings (secrets hidden)
```

On startup it only looks at the last 10 minutes of trades, so a restart doesn't flood you
with old alerts.

## Alerts

Desktop notifications go out for every alert (macOS Notification Center, or `notify-send`
on Linux). SMS, email, and Pushover go out only for the severities in `NOTIFY_SEVERITIES`
(HIGH and CRITICAL by default). Set them up in `.env`; see [`.env.example`](.env.example).

Market titles and trader names are written by other people, so the tool treats them as
untrusted. They are passed to notification commands as arguments (never built into a
script) and HTML-escaped in email.

## Development

```bash
pip install -e '.[dev]'
pytest
```

The detection rules in [`detect.py`](src/whalewatch/detect.py) are pure functions, so they
are easy to tune and test. The test suite also runs the whole loop against a fake Polymarket
API: fetch, filter, wallet lookup, rules, SQLite, de-duplication across restarts.

```
src/whalewatch/
  api.py       Polymarket Data API v2 and Gamma API client
  classify.py  event vs. sports/crypto-price market filter
  detect.py    detection rules and risk score
  monitor.py   polling loop
  store.py     SQLite storage (trades, wallets, alerts)
  notify.py    console, desktop, SMS, email, Pushover
  cli.py       the whalewatch command
```

## Limitations

- **These are heuristics, not proof.** New wallets placing big bets is also what
  privacy-minded traders, market makers, and people who just signed up do. Treat an
  alert as a reason to look, never as evidence of wrongdoing, and don't accuse anyone
  based on it.
- The market filter is keyword-based and will occasionally get a market wrong.
- It polls, so if more than 500 qualifying trades happen between polls, the oldest are skipped.
- Polymarket's API is unofficial and changes. The v1 Data API was retired in 2026, and
  this tool uses v2.

## Disclaimer

For research and education. Not financial advice, and not affiliated with or endorsed by
Polymarket. Prediction markets may be restricted where you live; follow local law.

## License

[MIT](LICENSE) © 2026 Darian Garcia
