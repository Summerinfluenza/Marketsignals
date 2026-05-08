"""
Market Top & Bottom Signals v17 — Statistical Outlier Model
Two report modes, both delivered daily at 9:00 AM ET (30 min before open):

  DAILY  — Z-Score outliers (VXN + PCR), CMF money flow, volume climax
  WEEKLY — 200-week MA, 14-week RSI, % over MA (structural trend)

Usage:
  python market_signals.py            # run both once
  python market_signals.py daily      # daily signals only
  python market_signals.py weekly     # weekly signals only
  python market_signals.py schedule   # daemon: fires every weekday at 9:00 AM ET

Environment variables:
  EMAIL_TO / EMAIL_FROM / EMAIL_PASSWORD
  SMTP_SERVER / SMTP_PORT     — default: smtp.gmail.com:587

Tracks QQQ (Nasdaq-100 ETF). Uses ^VXN (Nasdaq-100 volatility index).
IV Percentile is derived from ^VXN daily closes (Yahoo Finance).
Equity Put/Call Open Interest Ratio is scraped from ycharts.com.
"""

import os
import re
import sys
import json
import time
import smtplib
import urllib.request
import urllib.parse
import math
import functools
import statistics
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ── CONFIG ────────────────────────────────────────────────────────────────────

TICKER = "QQQ"
VXN    = "^VXN"
ET     = ZoneInfo("America/New_York")

# Statistical Outlier Parameters
OUTLIER = {
    "LOOKBACK":    50,    # 50-day window to determine "normal"
    "THRESHOLD":   2.5,   # Standard Deviations (Z-Score)
    "PCR_FLOOR":   0.70,  # Euphoria floor for tops (equity OI put/call)
    "VOL_CLIMAX":  2.0,   # 200% of average volume
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

DAILY = {
    "VXN_SMA_LENGTH":    10,
    "CPC_SMA_LENGTH":    10,
    "IVP_WINDOW":        252,   # 1-year lookback for IV percentile (from ^VXN)
    "PRICE_SMA":         200,   # 200-day SMA
    "PRICE_RSI":         14,    # 14-day RSI
}

# ── RETRY HELPERS ──────────────────────────────────────────────────────────────

def retry(max_attempts=3, backoff=2):
    """Decorator: retry on exception with exponential backoff."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_err = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    last_err = e
                    if attempt < max_attempts:
                        wait = backoff ** attempt
                        print(f"  Retry {attempt}/{max_attempts} for {fn.__name__} in {wait}s: {e}")
                        time.sleep(wait)
            raise last_err  # type: ignore
        return wrapper
    return decorator


# ── DATA FETCHERS ─────────────────────────────────────────────────────────────

@retry(max_attempts=3, backoff=2)
def fetch_yahoo(symbol, days=None, weeks=None, interval="1d"):
    """
    Fetch OHLCV candles from Yahoo Finance.
    Returns [{date, open, high, low, close, volume}, ...].
    """
    now = int(datetime.now().timestamp())
    if weeks:
        days_needed = weeks * 7
    elif days:
        days_needed = days
    else:
        days_needed = 365
    period1 = now - days_needed * 86400
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
        f"?period1={period1}&period2={now}&interval={interval}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    result = data["chart"]["result"][0]
    ts     = result["timestamp"]
    quotes = result["indicators"]["quote"][0]
    opens  = quotes.get("open",  [None] * len(ts))
    highs  = quotes.get("high",  [None] * len(ts))
    lows   = quotes.get("low",   [None] * len(ts))
    closes = quotes.get("close", [None] * len(ts))
    vols   = quotes.get("volume",[None] * len(ts))
    candles = []
    for i in range(len(ts)):
        if closes[i] is not None:
            candles.append({
                "date":   datetime.fromtimestamp(ts[i]),
                "open":   opens[i]   if opens[i]   is not None else closes[i],
                "high":   highs[i]   if highs[i]   is not None else closes[i],
                "low":    lows[i]    if lows[i]    is not None else closes[i],
                "close":  closes[i],
                "volume": vols[i]    if vols[i]    is not None else 0,
            })
    if not candles:
        raise RuntimeError(f"No valid candles for {symbol}")
    return candles


@retry(max_attempts=3, backoff=2)
def fetch_cnn_fg():
    """
    Fetch CNN Fear & Greed Index historical scores (0–100), oldest→newest.
    Returns list of floats or [] on error. No API key required.
    CNN requires browser-like headers (Referer/Origin) or returns HTTP 418.
    """
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    req = urllib.request.Request(url, headers={
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer":         "https://edition.cnn.com/markets/fear-and-greed",
        "Origin":          "https://edition.cnn.com",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    hist   = data["fear_and_greed_historical"]["data"]
    scores = [float(p["y"]) for p in hist if p.get("y") is not None]
    current = scores[-1] if scores else None
    rating  = data["fear_and_greed"]["rating"]
    print(f"CNN F&G: {current:.1f} ({rating}), {len(scores)} history points")
    return scores


@retry(max_attempts=3, backoff=3)
def fetch_cpc(num=10):
    """
    Scrape latest daily Equity Put/Call Open Interest Ratio from ycharts.com.
    Open interest reflects outstanding contracts (more stable than volume-based).
    Equity-only excludes index options — most relevant for QQQ/Nasdaq stocks.
    Tries OI endpoint first, falls back through volume and total put/call.
    Uses multiple fallback regex patterns for robustness against page changes.
    """
    urls = [
        "https://ycharts.com/indicators/cboe_equity_put_call_open_interest_ratio",
        "https://ycharts.com/indicators/equity_putcall_open_interest_ratio",
        "https://ycharts.com/indicators/equity_putcall_ratio",
        "https://ycharts.com/indicators/cboe_equity_put_call_ratio",
        "https://ycharts.com/indicators/total_putcall_ratio",
    ]
    html = None
    used_url = None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            used_url = url
            break
        except Exception:
            continue
    if html is None:
        raise RuntimeError("Could not fetch Put/Call Ratio from any ycharts endpoint.")

    values = []

    # Pattern 1: <tr> with <td>date</td><td>value</td> structure
    rows = re.findall(
        r'<td[^>]*>\s*([A-Za-z]+ \d{1,2}, \d{4})\s*</td>\s*<td[^>]*>\s*([\d.]+)\s*</td>',
        html, re.IGNORECASE
    )
    if rows:
        for _, v in rows:
            try:
                values.append(float(v))
            except ValueError:
                pass

    # Pattern 2: looser match for numbers near recognizable date strings
    if not values:
        raw = re.findall(
            r'(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}, \d{4})\s*</td>\s*<td[^>]*>\s*([\d.]+)',
            html
        )
        for v in raw:
            try:
                values.append(float(v))
            except ValueError:
                pass

    # Pattern 3: any <td> with a float after a four-digit year pattern
    if not values:
        raw2 = re.findall(r'20[0-9]{2}\s*</td>\s*<td[^>]*>\s*([\d.]+)', html)
        for v in raw2:
            try:
                values.append(float(v))
            except ValueError:
                pass

    if values:
        print(f"CPC (equity OI): scraped {len(values)} values from ycharts.com (latest: {values[0]:.3f})")
    else:
        raise RuntimeError("Could not scrape Put/Call Ratio from ycharts.com — page structure may have changed.")
    return values[:num]


def compute_iv_percentile(vxn_data, window_days=252):
    """
    Compute IV Percentile from ^VXN (Nasdaq-100 Volatility Index) daily closes.
    ^VXN itself represents the implied volatility of Nasdaq-100 options.

    IV Percentile = % of days in the lookback window where VXN closed
    below today's closing level. High percentile = fear (IV elevated vs history).

    Source: Yahoo Finance ^VXN data (fetched by fetch_yahoo).
    Returns percentile (0-100) or None if insufficient data.
    """
    if len(vxn_data) < 21:  # need at least a month
        return None
    closes = [d["close"] for d in vxn_data]
    today  = closes[-1]
    window = closes[-window_days:] if len(closes) >= window_days else closes
    below  = sum(1 for c in window if c < today)
    percentile = (below / len(window)) * 100
    return percentile


# ── INDICATORS ────────────────────────────────────────────────────────────────

def sma(arr, period):
    return [
        None if i < period - 1 else sum(arr[i - period + 1:i + 1]) / period
        for i in range(len(arr))
    ]


def rsi(closes, period):
    out = [None] * period
    gains = losses = 0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_gain, avg_loss = gains / period, losses / period
    out.append(100 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss))
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
        out.append(100 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss))
    return out


# ── STATISTICAL HELPERS ────────────────────────────────────────────────────────

def get_z_score(current, history):
    """Calculate how many standard deviations today is from the mean."""
    if len(history) < 20:
        return 0
    mu = statistics.mean(history)
    sigma = statistics.stdev(history)
    return (current - mu) / sigma if sigma != 0 else 0


def compute_cmf(candles, period=20):
    """Manually calculate Chaikin Money Flow for the latest candle."""
    if len(candles) < period:
        return 0
    mf_values = []
    vol_values = []
    for c in candles[-period:]:
        denom = (c['high'] - c['low'])
        mfm = ((c['close'] - c['low']) - (c['high'] - c['close'])) / denom if denom != 0 else 0
        mf_values.append(mfm * c['volume'])
        vol_values.append(c['volume'])

    total_vol = sum(vol_values)
    return sum(mf_values) / total_vol if total_vol != 0 else 0


# ── SIGNAL COMPUTATION ────────────────────────────────────────────────────────

def compute_weekly():
    """Returns weekly indicator dict for QQQ or None on error."""
    data = fetch_yahoo(TICKER, weeks=250, interval="1wk")
    if not data:
        return None

    closes  = [d["close"] for d in data]
    ma_arr  = sma(closes, WEEKLY["MA_LENGTH"])
    rsi_arr = rsi(closes, WEEKLY["RSI_LENGTH"])

    price = closes[-1]
    ma    = ma_arr[-1]
    rs    = rsi_arr[-1]
    pct   = ((price - ma) / ma * 100) if ma else None

    b_rsi = rs  is not None and rs  < WEEKLY["RSI_OVERSOLD"]
    b_ma  = ma  is not None and price < ma
    t_rsi = rs  is not None and rs  > WEEKLY["RSI_OVERBOUGHT"]
    t_pct = pct is not None and pct > WEEKLY["PCT_ABOVE_MA"]

    return {
        "date":  data[-1]["date"],
        "price": price, "ma": ma, "pct": pct, "rsi": rs,
        "b_rsi": b_rsi, "b_ma": b_ma,
        "t_rsi": t_rsi, "t_pct": t_pct,
        "b_score": sum([b_rsi, b_ma]),
        "t_score": sum([t_rsi, t_pct]),
    }


def compute_daily():
    """Returns outlier-based daily indicators using Z-scores and CMF."""
    # Fetch extended history for statistical baselines
    vxn_raw = fetch_yahoo(VXN, days=500, interval="1d")
    # Fetch QQQ specifically for volume climax, CMF, and RSI
    qqq_raw = fetch_yahoo(TICKER, days=100, interval="1d")
    # Get a larger sample of CPC for Z-score calculation
    cpc_hist = fetch_cpc(num=100)

    if not vxn_raw or not cpc_hist:
        return None

    # 1. Current Values
    curr_vxn = vxn_raw[-1]["close"]
    curr_cpc = cpc_hist[0]  # YCharts newest first
    curr_rsi = rsi([d["close"] for d in qqq_raw], DAILY["PRICE_RSI"])[-1]
    curr_vol = qqq_raw[-1].get("volume", 0)

    # 2. Historical Baselines (last 50 days)
    vxn_hist = [d["close"] for d in vxn_raw[-OUTLIER["LOOKBACK"]-1:-1]]
    cpc_hist_closes = cpc_hist[1:OUTLIER["LOOKBACK"]+1]
    vol_hist = [d.get("volume", 0) for d in qqq_raw[-OUTLIER["LOOKBACK"]-1:-1] if d.get("volume")]

    # 3. Z-Scores (The Outlier Filter)
    vxn_z = get_z_score(curr_vxn, vxn_hist)
    cpc_z = get_z_score(curr_cpc, cpc_hist_closes)

    # 4. Climax & Money Flow
    cmf_val = compute_cmf(qqq_raw)
    avg_vol = statistics.mean(vol_hist) if vol_hist else 1
    is_vol_climax = curr_vol > (avg_vol * OUTLIER["VOL_CLIMAX"])

    # 5. Signal Logic
    # BOTTOM: Need VXN and PCR to BOTH be statistical outliers + Oversold RSI
    b_vxn_outlier = vxn_z > OUTLIER["THRESHOLD"]
    b_cpc_outlier = cpc_z > OUTLIER["THRESHOLD"]
    b_rsi_oversold = curr_rsi < 30

    # TOP: Need PCR Euphoria + Volume Climax + Negative Money Flow
    t_pcr_euphoria = curr_cpc < OUTLIER["PCR_FLOOR"]
    t_neg_flow = cmf_val < 0
    t_rsi_overbought = curr_rsi > 80

    b_score = sum([b_vxn_outlier, b_cpc_outlier, b_rsi_oversold])
    t_score = sum([t_pcr_euphoria, is_vol_climax, t_neg_flow, t_rsi_overbought])

    return {
        "date":         datetime.now(ET),
        "vxn_z":        vxn_z,
        "cpc_z":        cpc_z,
        "cpc_curr":     curr_cpc,
        "cmf":          cmf_val,
        "rsi":          curr_rsi,
        "vol_climax":   is_vol_climax,
        "b_score":      b_score,
        "t_score":      t_score,
        "b_trigger":    b_score >= 2,       # Requires at least 2 outlier conditions
        "t_trigger":    t_score >= 3 and t_pcr_euphoria,  # Top strictly requires low PCR
    }


def compute_price():
    """Returns daily price, 200d SMA, % vs SMA, and 14d RSI for QQQ."""
    data = fetch_yahoo(TICKER, days=300, interval="1d")
    if not data:
        return None
    closes  = [d["close"] for d in data]
    price   = closes[-1]
    sma200  = sma(closes, DAILY["PRICE_SMA"])[-1]
    pct     = ((price - sma200) / sma200 * 100) if sma200 else None
    rsi14   = rsi(closes, DAILY["PRICE_RSI"])[-1]
    return {"ticker": TICKER, "price": price, "sma200": sma200, "pct": pct, "rsi": rsi14}


# ── REPORT FORMATTING ─────────────────────────────────────────────────────────

def _fmt(v, d=2):
    return f"{v:.{d}f}" if v is not None else "N/A"

def _chk(v):
    return "YES" if v else "No"

def _ivp_label(ivp):
    """Return a descriptive label for the IV percentile."""
    if ivp is None:
        return "N/A"
    if ivp > 90:
        return f"{_fmt(ivp,1)}% ← EXTREME FEAR"
    if ivp > 70:
        return f"{_fmt(ivp,1)}% ← High"
    if ivp < 15:
        return f"{_fmt(ivp,1)}% ← EXTREME COMPLACENCY"
    if ivp < 30:
        return f"{_fmt(ivp,1)}% ← Low"
    return f"{_fmt(ivp,1)}%"


def format_weekly(w):
    b_zone = w["b_score"] >= WEEKLY["MIN_BOTTOM"]
    t_zone = w["t_score"] >= WEEKLY["MIN_TOP"]
    signal = "→ BOTTOM WATCH" if b_zone else ("→ TOP WATCH" if t_zone else "→ NEUTRAL")
    return f"""
╔═══════════════════════════════════════════╗
║  WEEKLY SIGNALS — {TICKER:<6}  {w['date'].strftime('%Y-%m-%d')}  ║
╠═══════════════════════════════════════════╣
  Price:            {_fmt(w['price'])}
  200-Week MA:      {_fmt(w['ma'])}
  % Above 200w MA:  {_fmt(w['pct'])}%
  RSI (14w):        {_fmt(w['rsi'])}

  BOTTOM conditions ({w['b_score']}/2, need {WEEKLY['MIN_BOTTOM']})
    RSI < {WEEKLY['RSI_OVERSOLD']}:            {_chk(w['b_rsi']):3s}  (RSI = {_fmt(w['rsi'])})
    Price < 200w MA:   {_chk(w['b_ma']):3s}  ({_fmt(w['price'])} vs {_fmt(w['ma'])})

  TOP conditions ({w['t_score']}/2)
    RSI > {WEEKLY['RSI_OVERBOUGHT']}:            {_chk(w['t_rsi']):3s}  (RSI = {_fmt(w['rsi'])})
    % > {WEEKLY['PCT_ABOVE_MA']}% above MA:  {_chk(w['t_pct']):3s}  ({_fmt(w['pct'])}%)

  {signal}
╚═══════════════════════════════════════════╝"""


def format_daily(d, price_info=None):
    signal = "→ OUTLIER BUY" if d["b_trigger"] else ("→ OUTLIER SELL" if d["t_trigger"] else "→ NEUTRAL")

    price_section = ""
    if price_info:
        p = price_info
        pct_str = (f"+{p['pct']:.2f}%" if p['pct'] >= 0 else f"{p['pct']:.2f}%") if p['pct'] is not None else "N/A"
        price_section = f"""
  ── {p['ticker']} ──────────────────────────────
  Price:              {_fmt(p['price'])}
  200-Day SMA:        {_fmt(p['sma200'])}
  % vs 200d SMA:      {pct_str}
  RSI (14d):          {_fmt(p['rsi'], 1)}"""

    return f"""
╔═══════════════════════════════════════════╗
║  STATISTICAL SIGNALS — QQQ    {d['date'].strftime('%Y-%m-%d')}  ║
╠═══════════════════════════════════════════╣
  VXN Z-Score:        {_fmt(d['vxn_z'])} SD
  PCR Z-Score:        {_fmt(d['cpc_z'])} SD
  PCR Current:        {_fmt(d['cpc_curr'], 3)}
  Money Flow (CMF):   {_fmt(d['cmf'], 3)}
  RSI (14d):          {_fmt(d['rsi'], 1)}
  Volume Climax:      {_chk(d['vol_climax'])}
{price_section}

  BOTTOM CONDITIONS (Statistical Outliers — need 2 of 3)
    VXN > +{OUTLIER['THRESHOLD']} SD:          {_chk(d['vxn_z'] > OUTLIER['THRESHOLD']):3s}
    PCR > +{OUTLIER['THRESHOLD']} SD:          {_chk(d['cpc_z'] > OUTLIER['THRESHOLD']):3s}
    RSI < 30:                 {_chk(d['rsi'] < 30):3s}

  TOP CONDITIONS (Euphoria & Distribution — need 3 of 4, requires PCR floor)
    PCR < {OUTLIER['PCR_FLOOR']}:              {_chk(d['cpc_curr'] < OUTLIER['PCR_FLOOR']):3s}
    Volume > 200% Avg:        {_chk(d['vol_climax']):3s}
    Inst. Selling (CMF < 0):  {_chk(d['cmf'] < 0):3s}
    RSI > 80:                 {_chk(d['rsi'] > 80):3s}

  {signal}
╚═══════════════════════════════════════════╝"""


# ── RUNNERS ───────────────────────────────────────────────────────────────────

def _signal_label(b_score, t_score, b_min, t_min):
    if b_score >= b_min:
        return "BUY"
    if t_score >= t_min:
        return "SELL"
    return "NEUTRAL"


def run_daily():
    print("Fetching daily data...")
    d = compute_daily()
    if not d:
        print("ERROR: daily data unavailable.")
        return
    price = compute_price()
    report = format_daily(d, price)
    print(report)
    label = "BUY" if d["b_trigger"] else ("SELL" if d["t_trigger"] else "NEUTRAL")
    _send_email(f"{TICKER} Daily Signals {d['date'].strftime('%Y-%m-%d')} — {label}", report)


def run_weekly():
    print("Fetching weekly data...")
    w = compute_weekly()
    if not w:
        print("ERROR: weekly data unavailable.")
        return
    report = format_weekly(w)
    print(report)
    label = _signal_label(w["b_score"], w["t_score"], WEEKLY["MIN_BOTTOM"], WEEKLY["MIN_TOP"])
    date_str = datetime.now(ET).strftime("%Y-%m-%d")
    _send_email(f"{TICKER} Weekly Signals {date_str} — {label}", report)


def run_both():
    print("Fetching all data...")
    d = compute_daily()
    w = compute_weekly()

    if not d and not w:
        print("ERROR: no data available.")
        return

    price = compute_price()
    parts = [s for s in [
        format_daily(d, price) if d else None,
        format_weekly(w) if w else None,
    ] if s]
    report = "\n".join(parts)

    print(report)
    date_str = datetime.now(ET).strftime("%Y-%m-%d")
    if d:
        label = "BUY" if d["b_trigger"] else ("SELL" if d["t_trigger"] else "NEUTRAL")
    else:
        label = _signal_label(w["b_score"], w["t_score"], WEEKLY["MIN_BOTTOM"], WEEKLY["MIN_TOP"]) if w else "NEUTRAL"
    _send_email(f"{TICKER} Market Signals {date_str} — {label}", report)


# ── EMAIL ─────────────────────────────────────────────────────────────────────

def _to_html(text):
    """Convert plain-text report to colored HTML email."""
    import html as _html
    GREEN  = "#2a9d2a"
    RED    = "#cc2222"
    ORANGE = "#e67e00"
    GRAY   = "#888888"

    in_bottom = False
    in_top    = False
    in_extreme = False
    html_lines = []

    for line in text.split("\n"):
        esc = _html.escape(line)

        # Track which condition block we're in
        if "BOTTOM CONDITIONS" in line:
            in_bottom, in_top, in_extreme = True, False, False
        elif "TOP CONDITIONS" in line:
            in_bottom, in_top, in_extreme = False, True, False
        elif "EXTREME thresholds" in line:
            in_bottom, in_top, in_extreme = False, False, True
        elif line.strip().startswith("→") or line.strip().startswith("╚"):
            in_bottom = in_top = in_extreme = False

        # Color extreme warning lines
        if "EXTREME FEAR" in esc:
            esc = f'<span style="color:{RED};font-weight:bold">{esc}</span>'
        elif "EXTREME COMPLACENCY" in esc:
            esc = f'<span style="color:{ORANGE};font-weight:bold">{esc}</span>'

        # Color the signal line
        if "→ OUTLIER BUY" in esc:
            esc = esc.replace("→ OUTLIER BUY",
                f'<span style="color:{GREEN};font-weight:bold">→ OUTLIER BUY</span>')
        elif "→ OUTLIER SELL" in esc:
            esc = esc.replace("→ OUTLIER SELL",
                f'<span style="color:{RED};font-weight:bold">→ OUTLIER SELL</span>')
        elif "→ BOTTOM WATCH" in esc:
            esc = esc.replace("→ BOTTOM WATCH",
                f'<span style="color:{GREEN};font-weight:bold">→ BOTTOM WATCH</span>')
        elif "→ TOP WATCH" in esc:
            esc = esc.replace("→ TOP WATCH",
                f'<span style="color:{RED};font-weight:bold">→ TOP WATCH</span>')
        elif "→ NEUTRAL" in esc:
            esc = esc.replace("→ NEUTRAL",
                f'<span style="color:{GRAY};font-weight:bold">→ NEUTRAL</span>')

        # Color YES/No in condition rows
        if re.search(r"\bYES\b", esc):
            if in_extreme:
                color = ORANGE
            elif in_bottom:
                color = GREEN
            elif in_top:
                color = RED
            else:
                color = GREEN
            esc = re.sub(r"\bYES\b",
                f'<span style="color:{color};font-weight:bold">YES</span>', esc)
        if re.search(r"\bNo\b", esc):
            esc = re.sub(r"\bNo\b",
                f'<span style="color:{GRAY}">No </span>', esc)

        html_lines.append(esc)

    body = "\n".join(html_lines)
    return f"""<!DOCTYPE html>
<html>
<body style="background:#ffffff;margin:0;padding:20px;">
<pre style="font-family:'Courier New',Courier,monospace;font-size:13px;color:#222222;line-height:1.5;white-space:pre;">{body}</pre>
</body>
</html>"""


def _send_email(subject, body):
    to   = os.environ.get("EMAIL_TO",       "")
    frm  = os.environ.get("EMAIL_FROM",     "")
    pwd  = os.environ.get("EMAIL_PASSWORD", "")
    srv  = os.environ.get("SMTP_SERVER",    "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT",  "587"))

    if not (to and frm and pwd):
        print("Email credentials not set (EMAIL_TO / EMAIL_FROM / EMAIL_PASSWORD) — skipping.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, frm, to
    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(_to_html(body), "html"))  # html last = preferred by clients

    try:
        with smtplib.SMTP(srv, port) as server:
            server.starttls()
            server.login(frm, pwd)
            server.sendmail(frm, to, msg.as_string())
        print(f"Email sent → {to}")
    except Exception as e:
        print(f"Email error: {e}")


# ── SCHEDULER ─────────────────────────────────────────────────────────────────

def _next_9am_et():
    """Return next 9:00 AM ET as an aware datetime (skips weekends)."""
    now    = datetime.now(ET)
    target = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    while target.weekday() >= 5:   # skip Sat (5) and Sun (6)
        target += timedelta(days=1)
    return target


def start_scheduler():
    """Daemon: sleeps until next weekday 9:00 AM ET, then fires run_both()."""
    print("Scheduler started — reports every weekday at 09:00 ET. Ctrl+C to stop.")
    while True:
        target = _next_9am_et()
        wait   = (target - datetime.now(ET)).total_seconds()
        print(f"Next report: {target.strftime('%Y-%m-%d %H:%M %Z')} (in {wait/3600:.1f}h)")
        time.sleep(max(wait, 0))
        run_both()


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

_COMMANDS = {
    "daily":    run_daily,
    "weekly":   run_weekly,
    "both":     run_both,
    "schedule": start_scheduler,
}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "both"
    fn  = _COMMANDS.get(cmd)
    if fn is None:
        print(f"Unknown command '{cmd}'. Options: {', '.join(_COMMANDS)}")
        sys.exit(1)
    fn()