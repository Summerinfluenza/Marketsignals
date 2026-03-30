# Market Signals

A lightweight Python script that emails you a daily pre-market report with technical signals and a curated news feed — no paid services required.

---

## What it does

Every weekday at **9:00 AM ET** (30 minutes before US market open) you get an email with two sections:

**Daily signals** — fast-moving sentiment indicators
- VIX SMA (10-day)
- Put/Call Ratio SMA (10-day)
- Market Breadth (% of S&P 500 stocks above 50-day MA)
- CNN Fear & Greed Index

**Weekly signals** — structural trend indicators
- 200-week Moving Average vs price
- 14-week RSI
- % price is above the 200-week MA

Each indicator is scored YES/NO against a threshold. When enough conditions align, the report flags a **Bottom Watch** or **Top Watch** zone.

**News feed** (optional) — today's headlines from ZeroHedge, The Market Ear, Jam Croissant and Ozzy Livin, pulled via RSS. If you add a Gemini API key, the headlines are summarised into 3–5 bullet points by AI.

---

## Files

```
market_signals.py   — indicators, scoring, email, scheduler
market_news.py      — RSS news feed + optional Gemini summary
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
| `TICKER` | Ticker to track — default is `SPY` |
| `GEMINI_API_KEY` | [Free Gemini key](https://aistudio.google.com/app/apikey) — enables AI news summary |

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
          python-version: '3.11'
      - name: Install dependencies
        run: pip install google-generativeai
      - name: Run signals
        env:
          EMAIL_TO:       ${{ secrets.EMAIL_TO }}
          EMAIL_FROM:     ${{ secrets.EMAIL_FROM }}
          EMAIL_PASSWORD: ${{ secrets.EMAIL_PASSWORD }}
          TICKER:         ${{ secrets.TICKER }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
        run: python market_signals.py both
```

### 4. Done

Go to the **Actions** tab and hit **Run workflow** to test it immediately. After that it runs automatically on weekdays.

---

## Running locally

```bash
# Clone
git clone https://github.com/your-username/Marketsignals.git
cd Marketsignals

# Optional: install Gemini for AI summaries
pip install google-generativeai

# Set credentials
export EMAIL_TO="you@gmail.com"
export EMAIL_FROM="you@gmail.com"
export EMAIL_PASSWORD="your-app-password"
export GEMINI_API_KEY="your-key"   # optional

# Run
python market_signals.py           # both reports + news
python market_signals.py daily     # daily signals only
python market_signals.py weekly    # weekly signals only
python market_signals.py schedule  # daemon: auto-fires at 9:00 AM ET on weekdays

# News feed only
python market_news.py              # headlines
python market_news.py summary      # headlines + Gemini summary
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

To follow different accounts, edit `FEEDS` in `market_news.py`:

```python
FEEDS = {
    "zerohedge":     "https://feeds.feedburner.com/zerohedge/feed",
    "jam_croissant": "https://jamcroissant.substack.com/feed",
    "themarketear":  "nitter://themarketear",   # X-only accounts use nitter://
    "ozzy_livin":    "nitter://ozzy_livin",
}
```

---

## Dependencies

| Package | Required | Purpose |
|---|---|---|
| *(stdlib only)* | Always | Signals, RSS feed, email |
| `google-generativeai` | Optional | Gemini AI news summary |

Python 3.9+ required (uses `zoneinfo`).
