# Marketsignals# Market Top \& Bottom Signals v5

Weekly market signal scanner that emails you BUY/SELL/NEUTRAL signals based on VIX, Put/Call Ratio, RSI, Moving Average, and Market Breadth.

## Setup (5 minutes)

### 1\. Fork or create this repo on GitHub

Copy these files into a new GitHub repo:

```
market\\\_signals.py
.github/workflows/weekly\\\_signals.yml
```

### 2\. Set up email (Gmail recommended)

For Gmail, you need an **App Password** (not your regular password):

* Go to https://myaccount.google.com/apppasswords
* Create an app password for "Mail"
* Copy the 16-character password

### 3\. Add secrets to GitHub

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these 5 secrets:

|Secret Name|Value|
|-|-|
|`EMAIL\\\_TO`|your.email@gmail.com|
|`EMAIL\\\_FROM`|your.email@gmail.com|
|`EMAIL\\\_PASSWORD`|your-16-char-app-password|
|`SMTP\\\_SERVER`|smtp.gmail.com|
|`SMTP\\\_PORT`|587|

### 4\. Done!

The script runs automatically every **Friday at 9 PM UTC** (after US market close).

To test it immediately:

* Go to **Actions** tab → **Weekly Market Signals** → **Run workflow** → **Run workflow**

## What you get

A weekly email with:

* Current values for all indicators (VIX SMA, CPC SMA, RSI, % above MA, Breadth)
* Each condition scored YES/NO for both bottom and top signals
* Final signal: **BUY ZONE**, **SELL ZONE**, or **NEUTRAL**

## Customization

Edit the `CONFIG` dict in `market\\\_signals.py` to change:

* `TICKER`: SPY, QQQ, IWM, etc.
* Thresholds for all indicators
* `MIN\\\_BOTTOM` / `MIN\\\_TOP`: how many conditions must be met

## No dependencies

Uses only Python standard library — no pip install needed.

