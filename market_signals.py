"""
Market Top & Bottom Signals v5 (Weekly)
Fetches data from Yahoo Finance + CBOE, scores conditions, emails results.
Run via GitHub Actions on a weekly schedule.
"""

import os
import json
import smtplib
import urllib.request
import urllib.error
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

# --- CONFIG (matches Pine Script thresholds) ---
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
    "RSI_LENGTH": 14,
    "VIX_SMA_LENGTH": 10,
    "CPC_SMA_LENGTH": 10,
    "MA_LENGTH": 40,
}

# --- DATA FETCHERS ---

def fetch_yahoo_weekly(symbol, weeks=80):
    """Fetch weekly candles from Yahoo Finance."""
    now = int(datetime.now().timestamp())
    period1 = now - weeks * 7 * 86400
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
        f"?period1={period1}&period2={now}&interval=1wk"
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


def fetch_cboe_cpc_weekly(num_weeks=30):
    """Fetch CBOE total put/call ratio CSV, convert to weekly."""
    url = "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/totalpc.csv"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            csv_text = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"CBOE fetch error: {e}")
        return []

    lines = csv_text.strip().split("\n")
    # Find header row
    start_idx = 0
    for i, line in enumerate(lines):
        if "DATE" in line.upper() and "P/C" in line.upper():
            start_idx = i + 1
            break

    daily = []
    for line in lines[start_idx:]:
        cols = line.strip().split(",")
        if len(cols) < 5:
            continue
        try:
            ratio = float(cols[4])
            parts = cols[0].split("/")
            dt = datetime(int(parts[2]), int(parts[0]), int(parts[1]))
            daily.append({"date": dt, "close": ratio})
        except (ValueError, IndexError):
            continue

    # Convert to weekly (last daily value per week)
    weekly = []
    last_week_key = None
    for i, d in enumerate(daily):
        week_key = (d["date"] - timedelta(days=d["date"].weekday())).strftime("%Y-%W")
        if week_key != last_week_key:
            if last_week_key is not None and i > 0:
                weekly.append(daily[i - 1]["close"])
            last_week_key = week_key
    if daily:
        weekly.append(daily[-1]["close"])

    return weekly[-num_weeks:]


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
    ticker_data = fetch_yahoo_weekly(CONFIG["TICKER"], 80)
    vix_data = fetch_yahoo_weekly("^VIX", 30)
    cpc_weekly = fetch_cboe_cpc_weekly(30)
    breadth_data = fetch_yahoo_weekly("^SPXA50R", 10)

    if not ticker_data or not vix_data:
        print("ERROR: Failed to fetch ticker or VIX data.")
        return

    has_cpc = len(cpc_weekly) >= CONFIG["CPC_SMA_LENGTH"]
    has_breadth = len(breadth_data) > 0

    ticker_closes = [d["close"] for d in ticker_data]
    vix_closes = [d["close"] for d in vix_data]

    ma_arr = sma(ticker_closes, CONFIG["MA_LENGTH"])
    rsi_arr = rsi(ticker_closes, CONFIG["RSI_LENGTH"])
    vix_sma_arr = sma(vix_closes, CONFIG["VIX_SMA_LENGTH"])
    cpc_sma_arr = sma(cpc_weekly, CONFIG["CPC_SMA_LENGTH"]) if has_cpc else []

    last = lambda arr: arr[-1] if arr else None
    price = last(ticker_closes)
    ma = last(ma_arr)
    rs = last(rsi_arr)
    vs = last(vix_sma_arr)
    cs = last(cpc_sma_arr) if has_cpc else None
    br = breadth_data[-1]["close"] if has_breadth else None
    pct_ma = ((price - ma) / ma) * 100 if ma else None
    dt = ticker_data[-1]["date"]

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
    check = lambda v: "YES ✅" if v else "No  ❌"

    report = f"""
═══════════════════════════════════════════
  WEEKLY MARKET SIGNALS — {CONFIG['TICKER']}
  {dt.strftime('%Y-%m-%d')}
═══════════════════════════════════════════

  {CONFIG['TICKER']} Price:      {fmt(price)}
  VIX SMA({CONFIG['VIX_SMA_LENGTH']}w):    {fmt(vs)}
  CPC SMA({CONFIG['CPC_SMA_LENGTH']}w):    {fmt(cs, 3) if cs else 'N/A'}
  RSI({CONFIG['RSI_LENGTH']}w):        {fmt(rs)}
  40-Week MA:       {fmt(ma)}
  % Above 40w MA:   {fmt(pct_ma)}%
  Breadth:          {fmt(br, 1)}%

───────────────────────────────────────────
  BOTTOM CONDITIONS ({b_score}/4)
───────────────────────────────────────────
  VIX SMA > {CONFIG['VIX_FEAR']}:          {check(b1)}  ({fmt(vs)})
  CPC SMA > {CONFIG['CPC_FEAR']}:         {check(b2)}  ({fmt(cs, 3) if cs else 'N/A'})
  RSI < {CONFIG['RSI_OVERSOLD']}:              {check(b3)}  ({fmt(rs)})
  Price < 40w MA:         {check(b4)}  ({fmt(price)} vs {fmt(ma)})

───────────────────────────────────────────
  TOP CONDITIONS ({t_score}/5)
───────────────────────────────────────────
  RSI > {CONFIG['RSI_OVERBOUGHT']}:              {check(t1)}  ({fmt(rs)})
  CPC SMA < {CONFIG['CPC_GREED']}:         {check(t2)}  ({fmt(cs, 3) if cs else 'N/A'})
  VIX SMA < {CONFIG['VIX_COMPLACENT']}:          {check(t3)}  ({fmt(vs)})
  % Above MA > {CONFIG['PCT_ABOVE_MA']}%:   {check(t4)}  ({fmt(pct_ma)}%)
  Breadth < {CONFIG['BREADTH_THRESHOLD']}%:      {check(t5)}  ({fmt(br, 1)}%)

═══════════════════════════════════════════
  SIGNAL:  *** {signal} ***
  Bottom: {b_score}/4  |  Top: {t_score}/5
═══════════════════════════════════════════
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

    subject = f"{CONFIG['TICKER']} Weekly Signal: {signal} ({dt.strftime('%Y-%m-%d')})"

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