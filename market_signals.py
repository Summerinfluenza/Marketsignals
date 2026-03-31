"""
Market Top & Bottom Signals v7
Two report modes, both delivered daily at 9:00 AM ET (30 min before open):

  DAILY  — VIX SMA, CPC SMA, Breadth (fast-moving sentiment)
  WEEKLY — 200-week MA, 14-week RSI, % over MA (structural trend)

Usage:
  python market_signals.py            # run both once
  python market_signals.py daily      # daily signals only
  python market_signals.py weekly     # weekly signals only
  python market_signals.py schedule   # daemon: fires every weekday at 9:00 AM ET

Environment variables:
  TICKER                      — ticker to track (default: SPY)
  EMAIL_TO / EMAIL_FROM / EMAIL_PASSWORD
  SMTP_SERVER / SMTP_PORT     — default: smtp.gmail.com:587
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

_tickers_env = os.environ.get("TICKERS") or os.environ.get("TICKER", "SPY,QQQ")
TICKERS = [t.strip().upper() for t in _tickers_env.split(",") if t.strip()]
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
    "VIX_FEAR":       20,    # VIX SMA above → fear (bottom signal)
    "VIX_COMPLACENT": 13,    # VIX SMA below → complacency (top signal)
    "CPC_FEAR":       1.0,   # CPC SMA above → fear (bottom signal)
    "CPC_GREED":      0.8,   # CPC SMA below → greed (top signal)
    "FG_SMA_DAILY":   10,    # 10-day SMA of F&G
    "FG_SMA_WEEKLY":  50,    # 10-week SMA of F&G (≈50 trading days)
    "FG_BUY":         25,    # CNN F&G SMA below → extreme fear (bottom signal)
    "FG_CAUTION":     70,    # CNN F&G SMA above → greed (top signal)
    # Note: breadth is already a component of the CNN F&G index
    "PRICE_SMA":      200,   # 200-day SMA
    "PRICE_RSI":      14,    # 14-day RSI
}

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
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        hist   = data["fear_and_greed_historical"]["data"]
        scores = [float(p["y"]) for p in hist if p.get("y") is not None]
        current = scores[-1] if scores else None
        rating  = data["fear_and_greed"]["rating"]
        print(f"CNN F&G: {current:.1f} ({rating}), {len(scores)} history points")
        return scores
    except Exception as e:
        print(f"CNN F&G fetch error: {e}")
        return []


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

def compute_weekly(ticker):
    """Returns weekly indicator dict or None on error."""
    data = fetch_yahoo(ticker, weeks=250, interval="1wk")
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
    vix_raw = fetch_yahoo("^VIX", days=100, interval="1d")
    cpc_raw = fetch_cpc(DAILY["CPC_SMA_LENGTH"])
    fg_hist = fetch_cnn_fg()

    if not vix_raw:
        return None

    vix_closes = [d["close"] for d in vix_raw]
    vs = sma(vix_closes, DAILY["VIX_SMA_LENGTH"])[-1]
    cs = None
    if len(cpc_raw) >= DAILY["CPC_SMA_LENGTH"]:
        cs = sma(list(reversed(cpc_raw)), DAILY["CPC_SMA_LENGTH"])[-1]

    fg     = fg_hist[-1] if fg_hist else None
    fg_d   = sma(fg_hist, DAILY["FG_SMA_DAILY"])[-1]  if len(fg_hist) >= DAILY["FG_SMA_DAILY"]  else None
    fg_w   = sma(fg_hist, DAILY["FG_SMA_WEEKLY"])[-1] if len(fg_hist) >= DAILY["FG_SMA_WEEKLY"] else None

    b_vix = vs   is not None and vs   > DAILY["VIX_FEAR"]
    b_cpc = cs   is not None and cs   > DAILY["CPC_FEAR"]
    b_fg  = fg_d is not None and fg_d < DAILY["FG_BUY"]
    t_cpc = cs   is not None and cs   < DAILY["CPC_GREED"]
    t_vix = vs   is not None and vs   < DAILY["VIX_COMPLACENT"]
    t_fg  = fg_d is not None and fg_d > DAILY["FG_CAUTION"]

    return {
        "date":    datetime.now(ET),
        "vix_sma": vs, "cpc_sma": cs, "fg": fg, "fg_sma_d": fg_d, "fg_sma_w": fg_w,
        "b_vix": b_vix, "b_cpc": b_cpc, "b_fg": b_fg,
        "t_vix": t_vix, "t_cpc": t_cpc, "t_fg": t_fg,
        "b_score": sum([b_vix, b_cpc, b_fg]),
        "t_score": sum([t_vix, t_cpc, t_fg]),
    }


def compute_price(ticker):
    """Returns daily price, 200d SMA, % vs SMA, and 14d RSI for a ticker."""
    data = fetch_yahoo(ticker, days=300, interval="1d")
    if not data:
        return None
    closes  = [d["close"] for d in data]
    price   = closes[-1]
    sma200  = sma(closes, DAILY["PRICE_SMA"])[-1]
    pct     = ((price - sma200) / sma200 * 100) if sma200 else None
    rsi14   = rsi(closes, DAILY["PRICE_RSI"])[-1]
    return {"ticker": ticker, "price": price, "sma200": sma200, "pct": pct, "rsi": rsi14}


# ── REPORT FORMATTING ─────────────────────────────────────────────────────────

def _fmt(v, d=2): return f"{v:.{d}f}" if v is not None else "N/A"
def _chk(v):      return "YES" if v else "No"


def format_weekly(w, ticker):
    b_zone = w["b_score"] >= WEEKLY["MIN_BOTTOM"]
    t_zone = w["t_score"] >= WEEKLY["MIN_TOP"]
    signal = "→ BOTTOM WATCH" if b_zone else ("→ TOP WATCH" if t_zone else "→ NEUTRAL")
    return f"""
╔═══════════════════════════════════════════╗
║  WEEKLY SIGNALS — {ticker:<6}  {w['date'].strftime('%Y-%m-%d')}  ║
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


def format_daily(d, prices=None):
    b_zone = d["b_score"] >= 2
    t_zone = d["t_score"] >= 2
    signal = "→ BOTTOM WATCH" if b_zone else ("→ TOP WATCH" if t_zone else "→ NEUTRAL")
    cs_str   = _fmt(d["cpc_sma"], 3) if d["cpc_sma"]   is not None else "N/A"
    fg_str   = _fmt(d["fg"],    1)   if d["fg"]         is not None else "N/A"
    fg_d_str = _fmt(d["fg_sma_d"],1) if d["fg_sma_d"]  is not None else "N/A"
    fg_w_str = _fmt(d["fg_sma_w"],1) if d["fg_sma_w"]  is not None else "N/A"
    label    = " & ".join(TICKERS)

    price_sections = ""
    for p in (prices or []):
        if not p:
            continue
        pct_str = (f"+{p['pct']:.2f}%" if p["pct"] >= 0 else f"{p['pct']:.2f}%") if p["pct"] is not None else "N/A"
        price_sections += f"""
  ── {p['ticker']} ──────────────────────────────
  Price:              {_fmt(p['price'])}
  200-Day SMA:        {_fmt(p['sma200'])}
  % vs 200d SMA:      {pct_str}
  RSI (14d):          {_fmt(p['rsi'], 1)}"""

    return f"""
╔═══════════════════════════════════════════╗
║  DAILY SIGNALS — {label:<6}   {d['date'].strftime('%Y-%m-%d')}  ║
╠═══════════════════════════════════════════╣
  VIX SMA ({DAILY['VIX_SMA_LENGTH']}d):          {_fmt(d['vix_sma'])}
  CPC SMA ({DAILY['CPC_SMA_LENGTH']}d):          {cs_str}
  CNN Fear & Greed:       {fg_str}/100
  F&G SMA ({DAILY['FG_SMA_DAILY']}d):           {fg_d_str}/100
  F&G SMA (10w):          {fg_w_str}/100
{price_sections}

  BOTTOM conditions ({d['b_score']}/3, need 2)
    VIX SMA > {DAILY['VIX_FEAR']}:             {_chk(d['b_vix']):3s}  (VIX SMA = {_fmt(d['vix_sma'])})
    CPC SMA > {DAILY['CPC_FEAR']}:            {_chk(d['b_cpc']):3s}  (CPC SMA = {cs_str})
    F&G {DAILY['FG_SMA_DAILY']}d SMA < {DAILY['FG_BUY']}:       {_chk(d['b_fg']):3s}  (F&G SMA = {fg_d_str})

  TOP conditions ({d['t_score']}/3, need 2)
    CPC SMA < {DAILY['CPC_GREED']}:            {_chk(d['t_cpc']):3s}  (CPC SMA = {cs_str})
    VIX SMA < {DAILY['VIX_COMPLACENT']}:            {_chk(d['t_vix']):3s}  (VIX SMA = {_fmt(d['vix_sma'])})
    F&G {DAILY['FG_SMA_DAILY']}d SMA > {DAILY['FG_CAUTION']}:       {_chk(d['t_fg']):3s}  (F&G SMA = {fg_d_str})

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
    prices = [compute_price(t) for t in TICKERS]
    report = format_daily(d, prices)
    print(report)
    tickers_str = " & ".join(TICKERS)
    label = _signal_label(d["b_score"], d["t_score"], 2, 2)
    _send_email(f"{tickers_str} Daily Signals {d['date'].strftime('%Y-%m-%d')} — {label}", report)


def run_weekly():
    print("Fetching weekly data...")
    parts  = []
    labels = []
    for ticker in TICKERS:
        w = compute_weekly(ticker)
        if not w:
            print(f"ERROR: weekly data unavailable for {ticker}.")
            continue
        parts.append(format_weekly(w, ticker))
        labels.append(_signal_label(w["b_score"], w["t_score"], WEEKLY["MIN_BOTTOM"], WEEKLY["MIN_TOP"]))
    if not parts:
        return
    report = "\n".join(parts)
    print(report)
    tickers_str = " & ".join(TICKERS)
    label = "BUY" if "BUY" in labels else ("SELL" if "SELL" in labels else "NEUTRAL")
    date_str = datetime.now(ET).strftime("%Y-%m-%d")
    _send_email(f"{tickers_str} Weekly Signals {date_str} — {label}", report)


def run_both():
    print("Fetching all data...")
    d = compute_daily()
    weekly_parts = []
    weekly_results = []
    for ticker in TICKERS:
        w = compute_weekly(ticker)
        if w:
            weekly_parts.append(format_weekly(w, ticker))
            weekly_results.append(w)

    if not d and not weekly_parts:
        print("ERROR: no data available.")
        return

    prices = [compute_price(t) for t in TICKERS]
    parts = [s for s in [
        format_daily(d, prices) if d else None,
        *weekly_parts,
    ] if s]
    report = "\n".join(parts)

    print(report)
    date_str = datetime.now(ET).strftime("%Y-%m-%d")
    tickers_str = " & ".join(TICKERS)
    if d:
        label = _signal_label(d["b_score"], d["t_score"], 2, 2)
    else:
        labels = [_signal_label(w["b_score"], w["t_score"], WEEKLY["MIN_BOTTOM"], WEEKLY["MIN_TOP"]) for w in weekly_results]
        label = "BUY" if "BUY" in labels else ("SELL" if "SELL" in labels else "NEUTRAL")
    _send_email(f"{tickers_str} Market Signals {date_str} — {label}", report)


# ── EMAIL ─────────────────────────────────────────────────────────────────────

def _to_html(text):
    """Convert plain-text report to colored HTML email."""
    import html as _html
    GREEN  = "#2a9d2a"
    RED    = "#cc2222"
    GRAY   = "#888888"

    in_bottom = False
    in_top    = False
    html_lines = []

    for line in text.split("\n"):
        esc = _html.escape(line)

        # Track which condition block we're in
        if "BOTTOM conditions" in line:
            in_bottom, in_top = True, False
        elif "TOP conditions" in line:
            in_bottom, in_top = False, True
        elif line.strip().startswith("→"):
            in_bottom = in_top = False

        # Color the signal line
        if "→ BOTTOM WATCH" in esc:
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
            color = GREEN if in_bottom else (RED if in_top else GREEN)
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
