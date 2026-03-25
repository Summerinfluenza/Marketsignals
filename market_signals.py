"""
Market Top & Bottom Signals v5
Exact match to Pine Script on a weekly TradingView chart.

Pine behavior on weekly chart:
- ta.sma(close, 200) = 200-WEEK MA
- ta.rsi(close, 14)  = 14-WEEK RSI
- request.security("CPC", "D", close)  = daily CPC, SMA on daily
- request.security("CBOE:VIX", "D", close) = daily VIX, SMA on daily
- request.security("S5FI", "D", close) = daily breadth
- pct_over_ma uses weekly close vs 200-week MA
"""

import os
import re
import json
import smtplib
import urllib.request
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# --- CONFIG (matches Pine input.* defaults) ---
CONFIG = {
    "TICKER": "SPY",
    "VIX_FEAR": 20,
    "VIX_COMPLACENT": 13,
    "RSI_OVERSOLD": 30,
    "RSI_OVERBOUGHT": 70,
    "CPC_FEAR": 1.0,
    "CPC_GREED": 0.8,
    "PCT_ABOVE_MA": 40.0,
    "BREADTH_THRESHOLD": 50.0,
    "MIN_BOTTOM": 2,
    "MIN_TOP": 3,
    "RSI_LENGTH": 14,          # 14 weeks (weekly candles)
    "VIX_SMA_LENGTH": 10,      # 10 days (daily data)
    "CPC_SMA_LENGTH": 10,      # 10 days (daily data)
    "MA_LENGTH": 200,          # 200 weeks (weekly candles)
}


# --- DATA FETCHERS ---

def fetch_yahoo(symbol, days=None, weeks=None, interval="1d"):
    """Fetch candles from Yahoo Finance."""
    now = int(datetime.now().timestamp())
    if weeks:
        period1 = now - weeks * 7 * 86400
    else:
        period1 = now - days * 86400
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
        f"?period1={period1}&period2={now}&interval={interval}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        result = data["chart"]["result"][0]
        ts = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        return [
            {"date": datetime.fromtimestamp(ts[i]), "close": closes[i]}
            for i in range(len(ts))
            if closes[i] is not None
        ]
    except Exception as e:
        print(f"Yahoo fetch error [{symbol}]: {e}")
        return []


def fetch_cpc_from_ycharts(num_values=10):
    """Scrape latest daily CPC values from ycharts historical data table."""
    url = "https://ycharts.com/indicators/total_putcall_ratio"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"ycharts fetch error: {e}")
        return []

    # Extract values from the historical data tables
    # Pattern: table rows with date and value like "March 11, 2026 | 1.05"
    values = []
    rows = re.findall(
        r'<tr[^>]*>\s*<td[^>]*>([A-Za-z]+ \d{1,2}, \d{4})</td>\s*<td[^>]*>([\d.]+)</td>\s*</tr>',
        html
    )
    for date_str, val_str in rows:
        try:
            val = float(val_str)
            values.append(val)
        except ValueError:
            continue

    if not values:
        print("WARNING: Could not parse CPC data from ycharts. Trying fallback pattern...")
        # Fallback: look for any float values in table cells after date patterns
        values = [float(v) for v in re.findall(r'(?:20[0-9]{2})\s*</td>\s*<td[^>]*>\s*([\d.]+)', html)]

    if values:
        print(f"Scraped {len(values)} CPC values from ycharts (latest: {values[0]})")
        # Values are in reverse chronological order on the page, take first num_values
        return values[:num_values]
    else:
        print("ERROR: Failed to scrape CPC from ycharts.")
        return []


# --- INDICATORS ---

def sma(arr, period):
    out = []
    for i in range(len(arr)):
        if i < period - 1:
            out.append(None)
        else:
            out.append(sum(arr[i - period + 1 : i + 1]) / period)
    return out


def rsi(closes, period):
    out = [None] * period
    gains = losses = 0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / period
    avg_loss = losses / period
    out.append(100 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss))
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
        out.append(100 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss))
    return out


# --- MAIN ---

def run():
    print("Fetching data...")

    # WEEKLY candles for price, RSI, 200-week MA (need 220+ weeks for warmup)
    ticker_weekly = fetch_yahoo(CONFIG["TICKER"], weeks=250, interval="1wk")

    # DAILY data for VIX, breadth (matching Pine's request.security("...", "D", close))
    vix_daily = fetch_yahoo("^VIX", days=100, interval="1d")
    breadth_daily = fetch_yahoo("^SPXA50R", days=30, interval="1d")

    # CPC from ycharts (daily, latest 10 values for SMA)
    cpc_daily = fetch_cpc_from_ycharts(CONFIG["CPC_SMA_LENGTH"])

    if not ticker_weekly or not vix_daily:
        print("ERROR: Failed to fetch ticker or VIX data.")
        return

    has_cpc = len(cpc_daily) >= CONFIG["CPC_SMA_LENGTH"]
    has_breadth = len(breadth_daily) > 0

    # Weekly indicators (RSI, 200-week MA, % over MA)
    weekly_closes = [d["close"] for d in ticker_weekly]
    ma_arr = sma(weekly_closes, CONFIG["MA_LENGTH"])
    rsi_arr = rsi(weekly_closes, CONFIG["RSI_LENGTH"])

    # Daily indicators (VIX SMA)
    vix_closes = [d["close"] for d in vix_daily]
    vix_sma_arr = sma(vix_closes, CONFIG["VIX_SMA_LENGTH"])

    # CPC SMA — ycharts data is newest-first, reverse for SMA calc
    if has_cpc:
        cpc_reversed = list(reversed(cpc_daily))  # oldest to newest
        cpc_sma_arr = sma(cpc_reversed, CONFIG["CPC_SMA_LENGTH"])
    else:
        cpc_sma_arr = []

    # Latest values
    price = weekly_closes[-1]
    ma = ma_arr[-1]
    rs = rsi_arr[-1]
    vs = vix_sma_arr[-1] if vix_sma_arr else None
    cs = cpc_sma_arr[-1] if cpc_sma_arr else None
    br = breadth_daily[-1]["close"] if has_breadth else None
    pct_ma = ((price - ma) / ma) * 100 if ma else None
    dt = ticker_weekly[-1]["date"]

    # --- BOTTOM SCORING (4 conditions) ---
    b1 = vs is not None and vs > CONFIG["VIX_FEAR"]
    b2 = cs is not None and cs > CONFIG["CPC_FEAR"]
    b3 = rs is not None and rs < CONFIG["RSI_OVERSOLD"]
    b4 = ma is not None and price < ma
    b_score = sum([b1, b2, b3, b4])
    buy_signal = b_score >= CONFIG["MIN_BOTTOM"]

    # --- TOP SCORING (5 conditions) ---
    t1 = rs is not None and rs > CONFIG["RSI_OVERBOUGHT"]
    t2 = cs is not None and cs < CONFIG["CPC_GREED"]
    t3 = vs is not None and vs < CONFIG["VIX_COMPLACENT"]
    t4 = pct_ma is not None and pct_ma > CONFIG["PCT_ABOVE_MA"]
    t5 = br is not None and br < CONFIG["BREADTH_THRESHOLD"]
    t_score = sum([t1, t2, t3, t4, t5])
    sell_signal = t_score >= CONFIG["MIN_TOP"]

    signal = "BUY ZONE" if buy_signal else ("SELL ZONE" if sell_signal else "NEUTRAL")

    # --- BUILD REPORT ---
    fmt = lambda v, d=2: f"{v:.{d}f}" if v is not None else "N/A"
    chk = lambda v: "YES" if v else "No"

    report = f"""
=============================================
  WEEKLY MARKET SIGNALS — {CONFIG['TICKER']}
  {dt.strftime('%Y-%m-%d')}
=============================================

  {CONFIG['TICKER']} Price:        {fmt(price)}
  200-Week MA:       {fmt(ma)}
  % Above 200w MA:   {fmt(pct_ma)}%
  RSI(14w):          {fmt(rs)}
  VIX SMA(10d):      {fmt(vs)}
  CPC SMA(10d):      {fmt(cs, 3) if cs else 'N/A'}
  Breadth:           {fmt(br, 1) if br else 'N/A'}%

---------------------------------------------
  BOTTOM CONDITIONS ({b_score}/4, need {CONFIG['MIN_BOTTOM']})
---------------------------------------------
  VIX SMA > {CONFIG['VIX_FEAR']}:            {chk(b1):3s}   (VIX SMA = {fmt(vs)})
  CPC SMA > {CONFIG['CPC_FEAR']}:           {chk(b2):3s}   (CPC SMA = {fmt(cs, 3) if cs else 'N/A'})
  RSI < {CONFIG['RSI_OVERSOLD']}:                {chk(b3):3s}   (RSI = {fmt(rs)})
  Price < 200w MA:          {chk(b4):3s}   ({fmt(price)} vs {fmt(ma)})

---------------------------------------------
  TOP CONDITIONS ({t_score}/5, need {CONFIG['MIN_TOP']})
---------------------------------------------
  RSI > {CONFIG['RSI_OVERBOUGHT']}:                {chk(t1):3s}   (RSI = {fmt(rs)})
  CPC SMA < {CONFIG['CPC_GREED']}:           {chk(t2):3s}   (CPC SMA = {fmt(cs, 3) if cs else 'N/A'})
  VIX SMA < {CONFIG['VIX_COMPLACENT']}:            {chk(t3):3s}   (VIX SMA = {fmt(vs)})
  % Above MA > {CONFIG['PCT_ABOVE_MA']}%:     {chk(t4):3s}   ({fmt(pct_ma)}%)
  Breadth < {CONFIG['BREADTH_THRESHOLD']}%:        {chk(t5):3s}   ({fmt(br, 1) if br else 'N/A'}%)

=============================================
  SIGNAL:  *** {signal} ***
  Bottom: {b_score}/4  |  Top: {t_score}/5
=============================================
"""

    print(report)

    # --- SEND EMAIL ---
    email_to = os.environ.get("EMAIL_TO", "")
    email_from = os.environ.get("EMAIL_FROM", "")
    email_password = os.environ.get("EMAIL_PASSWORD", "")
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))

    if not email_to or not email_from or not email_password:
        print("Email credentials not set. Skipping email.")
        print("Set EMAIL_TO, EMAIL_FROM, EMAIL_PASSWORD as environment variables.")
        return

    subject = f"{CONFIG['TICKER']} Weekly: {signal} ({dt.strftime('%Y-%m-%d')})"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to
    msg.attach(MIMEText(report, "plain"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(email_from, email_password)
            server.sendmail(email_from, email_to, msg.as_string())
        print(f"Email sent to {email_to}")
    except Exception as e:
        print(f"Email send error: {e}")


if __name__ == "__main__":
    run()