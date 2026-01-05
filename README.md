# 🐋 Polymarket Whale & Insider Detector

Real-time monitoring system to detect potential insider trading activity on Polymarket political markets.

## Features

- **🐋 Whale Trade Detection** - Alerts on large trades exceeding customizable thresholds
- **🆕 New Wallet Tracking** - Flags fresh wallets making significant first-time bets
- **🔥 High-Conviction Contrarians** - Detects new wallets betting against market consensus
- **📈 Pre-Event Accumulation** - Spots position building before market resolution
- **🌙 Off-Hours Trading** - Identifies large trades at unusual times
- **🔔 Desktop Notifications** - Real-time alerts with sound for critical events
- **📊 Risk Scoring** - Calculates risk scores (0-100) for tracked wallets

## Installation

```bash
# Clone or navigate to the project
cd polymarket-bot

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment config
cp .env.example .env
```

## Usage

### Start Monitoring

```bash
# Start the whale detector
python main.py monitor

# With custom threshold ($10,000 minimum)
python main.py monitor --threshold 10000

# With faster polling (15 seconds)
python main.py monitor --interval 15

# Disable desktop notifications
python main.py monitor --no-notifications
```

### View Data

```bash
# Show recent alerts
python main.py alerts

# Filter by severity
python main.py alerts --severity CRITICAL

# View tracked wallets by risk score
python main.py wallets

# Show only flagged wallets
python main.py wallets --flagged

# View tracked political markets
python main.py markets
```

### Test & Configure

```bash
# Send a test notification
python main.py test-notification

# View current configuration
python main.py config
```

## Detection Algorithms

### 1. New Wallet Large Bet
Triggers when:
- Wallet age < 72 hours
- Trade value > $2,500
- < 3 previous trades

**Severity: HIGH to CRITICAL**

### 2. High-Conviction Contrarian
Triggers when:
- New wallet (< 72 hours)
- Buying outcome with < 25% implied probability
- Or selling outcome with > 75% implied probability

**Severity: CRITICAL** - This is the strongest insider signal

### 3. Pre-Event Accumulation
Triggers when:
- Market resolves within 48 hours
- Multiple same-direction trades in 24 hours
- Total position > whale threshold

**Severity: HIGH**

### 4. Whale Trade
Triggers on any trade exceeding the configured threshold.

**Severity: MEDIUM to CRITICAL** (based on size)

## Configuration

Edit `.env` to customize:

| Setting | Default | Description |
|---------|---------|-------------|
| `WHALE_TRADE_THRESHOLD_USD` | 5000 | Minimum USD to flag as whale |
| `NEW_WALLET_AGE_HOURS` | 72 | Hours to consider wallet "new" |
| `SUSPICIOUS_WIN_RATE` | 0.85 | Win rate to flag as suspicious |
| `POLL_INTERVAL_SECONDS` | 30 | API polling frequency |

## Alert Severities

- **LOW** 🟢 - Minor anomaly, informational
- **MEDIUM** 🟡 - Whale activity, worth watching
- **HIGH** 🔴 - Strong insider signal, investigate
- **CRITICAL** ⚠️ - Very suspicious pattern, immediate attention

## Architecture

```
polymarket-bot/
├── main.py              # CLI interface and monitoring orchestrator
├── polymarket_client.py # API client for Polymarket (Gamma, CLOB, Data APIs)
├── detector.py          # Detection algorithms and risk scoring
├── notifier.py          # Notification system (desktop, console)
├── models.py            # Database models (SQLAlchemy)
├── config.py            # Configuration management
├── requirements.txt     # Python dependencies
└── .env                 # Local configuration
```

## Example Alert

```
╭──────────────────────────────────────────────────────────────╮
│ 🔥 High Conviction Contrarian: $7,500                        │
├──────────────────────────────────────────────────────────────┤
│ New wallet betting AGAINST consensus:                        │
│ • Market: Will Biden win the 2024 election?                  │
│ • Market Price: 15% probability                              │
│ • Their Bet: BUY YES                                         │
│ • Size: $7,500.00                                            │
│ • Wallet Age: 2.3 hours                                      │
│                                                              │
│ 🚨 HIGH ALERT: Betting against market with conviction!       │
╰──────────────────────────────────────────────────────────────╯
                                                    [CRITICAL]
```

## Limitations

- Polymarket API may have rate limits
- Detection is heuristic-based (false positives possible)
- Cannot access private wallet information
- Market classification uses keyword matching

## Disclaimer

This tool is for research and educational purposes only. Trading on prediction markets carries risk. Not financial advice. The detection of "insider trading" is speculative and based on behavioral patterns that may have legitimate explanations.

## License

MIT

