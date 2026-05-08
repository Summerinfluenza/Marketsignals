# Market Signals v17

A lightweight Python script that emails you a daily pre-market report with statistical outlier signals — no paid services required.

---

## What it does

Every weekday at **9:00 AM ET** (30 minutes before US market open) you get an email with two sections:

**Daily signals** — statistical outlier model for short-term extremes
- VXN Z-Score (Nasdaq-100 volatility vs 50-day baseline)
- Put/Call Open Interest Ratio Z-Score (equity-only, scraped from ycharts.com)
- Chaikin Money Flow (CMF) — institutional buying/selling pressure
- Volume climax detection (200% of average)
- 14-day RSI
- 200-day SMA vs price

**Weekly signals** — structural trend indicators
- 200-week Moving Average vs price
- 14-week RSI
- % price above the 200-week MA

Each indicator is scored as a statistical condition. When enough conditions align, the report flags an **OUTLIER BUY**, **OUTLIER SELL**, or **NEUTRAL** signal.

---

## Signal Logic (Daily)

| Condition | Bottom (Buy) | Top (Sell) |
|---|---|---|
| VXN Z-Score | \> +2.5 SD | — |
| PCR Z-Score | \> +2.5 SD | — |
| PCR raw | — | \< 0.70 (euphoria) |
| Volume | — | \> 200% of avg (climax) |
| CMF | — | \< 0 (distribution) |
| RSI (14d) | \< 30 (oversold) | \> 80 (overbought) |

**Bottom trigger**: ≥ 2 of 3 conditions met (VXN outlier, PCR outlier, oversold RSI).  
**Top trigger**: ≥ 3 of 4 conditions met, and PCR euphoria floor is **required**.

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

All thresholds are at the top of `market_signals.py` in the `OUTLIER`, `DAILY`, and `WEEKLY` config blocks:

```python
OUTLIER = {
    "LOOKBACK":    50,    # 50-day window to determine "normal"
    "THRESHOLD":   2.5,   # Standard Deviations (Z-Score)
    "PCR_FLOOR":   0.70,  # Euphoria floor for tops (equity OI put/call)
    "VOL_CLIMAX":  2.0,   # 200% of average volume
}

DAILY = {
    "PRICE_SMA":    200,   # 200-day SMA
    "PRICE_RSI":     14,   # 14-day RSI
}

WEEKLY = {
    "MA_LENGTH":      200,   # 200-week simple MA
    "RSI_LENGTH":     14,    # 14-week RSI
    "RSI_OVERSOLD":   30,
    "RSI_OVERBOUGHT": 70,
    "PCT_ABOVE_MA":   40.0,  # % price above 200w MA → top condition
    "MIN_BOTTOM":     2,
    "MIN_TOP":        3,
}
```

---

## Data Sources

| Indicator | Source |
|---|---|
| QQQ price, volume, OHLCV | Yahoo Finance (free, no API key) |
| ^VXN (Nasdaq-100 volatility) | Yahoo Finance |
| Equity Put/Call Open Interest Ratio | ycharts.com (scraped) |
| CNN Fear & Greed Index | cnn.com (fetched, shown on demand) |

No API keys, no paid subscriptions, no external packages — stdlib only. Python 3.9+ required (uses `zoneinfo`).