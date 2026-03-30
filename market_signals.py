"""
Market Top & Bottom Signals v7
Two report modes, both delivered daily at 9:00 AM ET (30 min before open):

  DAILY  — VIX SMA, CPC SMA, Breadth (fast-moving sentiment)
  WEEKLY — 200-week MA, 14-week RSI, % over MA (structural trend)

Twitter feed and Gemini AI summary live in market_news.py (imported automatically).

Usage:
  python market_signals.py            # run both + news once
  python market_signals.py daily      # daily signals + news
  python market_signals.py weekly     # weekly signals only
  python market_signals.py schedule   # daemon: fires every weekday at 9:00 AM ET

Environment variables:
  TICKER                      — ticker to track (default: SPY)
  EMAIL_TO / EMAIL_FROM / EMAIL_PASSWORD
  SMTP_SERVER / SMTP_PORT     — default: smtp.gmail.com:587
  X_BEARER_TOKEN              — enables Twitter feed (market_news.py)
  GEMINI_API_KEY              — enables AI tweet summary (market_news.py)
"""

import os
import re
import sys
import json
import time
import smtplib
import urllib.request
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ── CONFIG ────────────────────────────────────────────────────────────────────

TICKER = os.environ.get("TICKER", "SPY")
ET = ZoneInfo("America/New_York")

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
    "VIX_SMA_LENGTH":    10,
    "CPC_SMA_LENGTH":    10,
    "VIX_FEAR":          20,    # VIX SMA above → fear (bottom signal)
    "VIX_COMPLACENT":    13,    # VIX SMA below → complacency (top signal)
    "CPC_FEAR":          1.0,   # CPC SMA above → fear (bottom signal)
    "CPC_GREED":         0.8,   # CPC SMA below → greed (top signal)
    "BREADTH_THRESHOLD": 50.0,  # % stocks above 50-day MA; below → weak breadth
    "FG_BUY":            25,    # CNN F&G below → extreme fear (bottom signal)
    "FG_CAUTION":        70,    # CNN F&G above → greed (top signal)
}

# ── NEWS INTEGRATION (optional) ───────────────────────────────────────────────

try:
    from market_news import build_twitter_block
except ImportError:
    def build_twitter_block():
        return ""

# ── DATA FETCHERS ─────────────────────────────────────────────────────────────

def fetch_yahoo(symbol, days=None, weeks=None, interval="1d"):
    """Fetch OHLC candles from Yahoo Finance. Returns [{date, close}, ...]."""
    now = int(datetime.now().timestamp())
    period1 = now - (weeks * 7 if weeks else days) * 86400
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
        f"?period1={period1}&period2={now}&interval={interval}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        result = data["chart"]["result"][0]
        ts     = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        return [
            {"date": datetime.fromtimestamp(ts[i]), "close": closes[i]}
            for i in range(len(ts)) if closes[i] is not None
        ]
    except Exception as e:
        print(f"Yahoo fetch error [{symbol}]: {e}")
        return []


def fetch_cnn_fg():
    """
    Fetch current CNN Fear & Greed Index score (0–100).
    Returns float or None on error. No API key required.
    """
    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        score = float(data["fear_and_greed"]["score"])
        rating = data["fear_and_greed"]["rating"]
        print(f"CNN F&G: {score:.1f} ({rating})")
        return score
    except Exception as e:
        print(f"CNN F&G fetch error: {e}")
        return None


def fetch_cpc(num=10):
    """Scrape latest daily CPC values from ycharts (newest first)."""
    url = "https://ycharts.com/indicators/total_putcall_ratio"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"CPC fetch error: {e}")
        return []

    rows = re.findall(
        r'<tr[^>]*>\s*<td[^>]*>([A-Za-z]+ \d{1,2}, \d{4})</td>\s*<td[^>]*>([\d.]+)</td>\s*</tr>',
        html
    )
    values = []
    for _, v in rows:
        try:
            values.append(float(v))
        except ValueError:
            pass

    if not values:
        values = [
            float(v)
            for v in re.findall(r'20[0-9]{2}\s*</td>\s*<td[^>]*>\s*([\d.]+)', html)
        ]

    if values:
        print(f"CPC: scraped {len(values)} values (latest: {values[0]:.3f})")
    else:
        print("ERROR: Could not scrape CPC from ycharts.")
    return values[:num]


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


# ── SIGNAL COMPUTATION ────────────────────────────────────────────────────────

def compute_weekly():
    """Returns weekly indicator dict or None on error."""
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
    """Returns daily indicator dict or None on error."""
    vix_raw = fetch_yahoo("^VIX",     days=100, interval="1d")
    breadth = fetch_yahoo("^SPXA50R", days=30,  interval="1d")
    cpc_raw = fetch_cpc(DAILY["CPC_SMA_LENGTH"])
    fg      = fetch_cnn_fg()

    if not vix_raw:
        return None

    vix_closes = [d["close"] for d in vix_raw]
    vs = sma(vix_closes, DAILY["VIX_SMA_LENGTH"])[-1]
    br = breadth[-1]["close"] if breadth else None
    cs = None
    if len(cpc_raw) >= DAILY["CPC_SMA_LENGTH"]:
        cs = sma(list(reversed(cpc_raw)), DAILY["CPC_SMA_LENGTH"])[-1]

    b_vix = vs is not None and vs > DAILY["VIX_FEAR"]
    b_cpc = cs is not None and cs > DAILY["CPC_FEAR"]
    b_fg  = fg is not None and fg < DAILY["FG_BUY"]
    t_cpc = cs is not None and cs < DAILY["CPC_GREED"]
    t_vix = vs is not None and vs < DAILY["VIX_COMPLACENT"]
    t_br  = br is not None and br < DAILY["BREADTH_THRESHOLD"]
    t_fg  = fg is not None and fg > DAILY["FG_CAUTION"]

    return {
        "date":    datetime.now(ET),
        "vix_sma": vs, "cpc_sma": cs, "breadth": br, "fg": fg,
        "b_vix": b_vix, "b_cpc": b_cpc, "b_fg": b_fg,
        "t_vix": t_vix, "t_cpc": t_cpc, "t_br": t_br, "t_fg": t_fg,
        "b_score": sum([b_vix, b_cpc, b_fg]),
        "t_score": sum([t_vix, t_cpc, t_br, t_fg]),
    }


# ── REPORT FORMATTING ─────────────────────────────────────────────────────────

def _fmt(v, d=2): return f"{v:.{d}f}" if v is not None else "N/A"
def _chk(v):      return "YES" if v else "No"


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


def format_daily(d):
    b_zone = d["b_score"] >= 2
    t_zone = d["t_score"] >= 2
    signal = "→ BOTTOM WATCH" if b_zone else ("→ TOP WATCH" if t_zone else "→ NEUTRAL")
    cs_str = _fmt(d["cpc_sma"], 3) if d["cpc_sma"] is not None else "N/A"
    br_str = _fmt(d["breadth"], 1) if d["breadth"] is not None else "N/A"
    fg_str = _fmt(d["fg"], 1)      if d["fg"]      is not None else "N/A"
    return f"""
╔═══════════════════════════════════════════╗
║  DAILY SIGNALS — {TICKER:<6}   {d['date'].strftime('%Y-%m-%d')}  ║
╠═══════════════════════════════════════════╣
  VIX SMA ({DAILY['VIX_SMA_LENGTH']}d):      {_fmt(d['vix_sma'])}
  CPC SMA ({DAILY['CPC_SMA_LENGTH']}d):      {cs_str}
  Breadth (SPXA50R): {br_str}%
  CNN Fear & Greed:  {fg_str}/100

  BOTTOM conditions ({d['b_score']}/3, need 2)
    VIX SMA > {DAILY['VIX_FEAR']}:       {_chk(d['b_vix']):3s}  (VIX SMA = {_fmt(d['vix_sma'])})
    CPC SMA > {DAILY['CPC_FEAR']}:      {_chk(d['b_cpc']):3s}  (CPC SMA = {cs_str})
    F&G < {DAILY['FG_BUY']} (extreme fear): {_chk(d['b_fg']):3s}  (F&G = {fg_str})

  TOP conditions ({d['t_score']}/4, need 2)
    CPC SMA < {DAILY['CPC_GREED']}:      {_chk(d['t_cpc']):3s}  (CPC SMA = {cs_str})
    VIX SMA < {DAILY['VIX_COMPLACENT']}:       {_chk(d['t_vix']):3s}  (VIX SMA = {_fmt(d['vix_sma'])})
    Breadth < {DAILY['BREADTH_THRESHOLD']}%:    {_chk(d['t_br']):3s}  ({br_str}%)
    F&G > {DAILY['FG_CAUTION']} (greed):       {_chk(d['t_fg']):3s}  (F&G = {fg_str})

  {signal}
╚═══════════════════════════════════════════╝"""


# ── RUNNERS ───────────────────────────────────────────────────────────────────

def run_daily():
    print("Fetching daily data...")
    d = compute_daily()
    if not d:
        print("ERROR: daily data unavailable.")
        return
    twitter = build_twitter_block()
    report  = format_daily(d) + twitter
    print(report)
    _send_email(f"{TICKER} Daily Signals {d['date'].strftime('%Y-%m-%d')}", report)


def run_weekly():
    print("Fetching weekly data...")
    w = compute_weekly()
    if not w:
        print("ERROR: weekly data unavailable.")
        return
    report = format_weekly(w)
    print(report)
    _send_email(f"{TICKER} Weekly Signals {w['date'].strftime('%Y-%m-%d')}", report)


def run_both():
    print("Fetching all data...")
    d = compute_daily()
    w = compute_weekly()
    if not d and not w:
        print("ERROR: no data available.")
        return

    twitter = build_twitter_block()
    parts = [s for s in [
        format_daily(d)  if d else None,
        format_weekly(w) if w else None,
        twitter          if twitter else None,
    ] if s]
    report = "\n".join(parts)

    print(report)
    date_str = datetime.now(ET).strftime("%Y-%m-%d")
    _send_email(f"{TICKER} Market Signals {date_str}", report)


# ── EMAIL ─────────────────────────────────────────────────────────────────────

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
