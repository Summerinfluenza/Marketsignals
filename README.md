# Market Signals

A lightweight Python script that emails you a daily pre-market report with technical signals — no paid services required.

---

## What it does

Every weekday at **9:00 AM ET** (30 minutes before US market open) you get an email with two sections:

**Daily signals** — fast-moving sentiment indicators
- VIX SMA (10-day)
- Put/Call Ratio SMA (10-day)
- CNN Fear & Greed Index

**Weekly signals** — structural trend indicators
- 200-week Moving Average vs price
- 14-week RSI
- % price is above the 200-week MA

Each indicator is scored YES/NO against a threshold. When enough conditions align, the report flags a **Bottom Watch** or **Top Watch** zone. The email subject line shows the current signal: **BUY**, **SELL**, or **NEUTRAL**.

---

## Files

```
market_signals.py   — indicators, scoring, email, scheduler
```

---

## Running on GitHub Actions (recommended)

This is the easiest way — GitHub runs the script for you on a schedule, no server needed.

### 1. Fork this repo

Click **Fork** at the top of this page.

### 2. Add your secrets

Go to your fork → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret | Value |
|---|---|
| `EMAIL_TO` | Address to receive the report |
| `EMAIL_FROM` | Gmail address to send from |
| `EMAIL_PASSWORD` | Gmail [App Password](https://myaccount.google.com/apppasswords) (not your login password) |

Optional extras:

| Secret | Value |
|---|---|
| `TICKERS` | Comma-separated tickers to track — default is `SPY,QQQ` |

> **Gmail App Password**: go to myaccount.google.com/apppasswords, create one for "Mail", copy the 16-character code.

### 3. Create the workflow file

Create `.github/workflows/daily_signals.yml` in your repo with this content:

```yaml
name: Daily Market Signals

on:
  schedule:
    - cron: '0 14 * * 1-5'   # 14:00 UTC = 9:00 AM ET (adjust for DST if needed)
  workflow_dispatch:           # lets you trigger manually from the Actions tab

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Run signals
        env:
          EMAIL_TO:       ${{ secrets.EMAIL_TO }}
          EMAIL_FROM:     ${{ secrets.EMAIL_FROM }}
          EMAIL_PASSWORD: ${{ secrets.EMAIL_PASSWORD }}
          TICKER:         ${{ secrets.TICKER }}
        run: python market_signals.py both
```

### 4. Done

Go to the **Actions** tab and hit **Run workflow** to test it immediately. After that it runs automatically on weekdays.

---

## Quick test (no email setup)

```bash
python market_signals.py both
```

This prints the full report to the terminal. Email is skipped unless `EMAIL_TO`, `EMAIL_FROM`, and `EMAIL_PASSWORD` are set.

---

## Running locally

```bash
# Clone
git clone https://github.com/your-username/Marketsignals.git
cd Marketsignals

# Set credentials
export EMAIL_TO="you@gmail.com"
export EMAIL_FROM="you@gmail.com"
export EMAIL_PASSWORD="your-app-password"

# Run
python market_signals.py           # both reports
python market_signals.py daily     # daily signals only
python market_signals.py weekly    # weekly signals only
python market_signals.py schedule  # daemon: auto-fires at 9:00 AM ET on weekdays
```

---

## Customisation

All thresholds are at the top of `market_signals.py`:

```python
WEEKLY = {
    "RSI_OVERSOLD":   30,    # RSI below this → bottom condition
    "RSI_OVERBOUGHT": 70,    # RSI above this → top condition
    "PCT_ABOVE_MA":   40.0,  # % above 200w MA → top condition
}

DAILY = {
    "VIX_FEAR":          20,   # VIX SMA above → fear (bottom)
    "VIX_COMPLACENT":    13,   # VIX SMA below → complacency (top)
    "CPC_FEAR":          1.0,  # Put/Call above → fear (bottom)
    "CPC_GREED":         0.8,  # Put/Call below → greed (top)
    "FG_BUY":            25,   # CNN F&G below → extreme fear (bottom)
    "FG_CAUTION":        70,   # CNN F&G above → greed (top)
}
```

---

## Dependencies

No external packages required. stdlib only.

Python 3.9+ required (uses `zoneinfo`).
