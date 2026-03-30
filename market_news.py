"""
Market News — RSS feed + Gemini AI summary

Fetches today's posts from watched accounts via RSS (no API keys, no paid plans).
Sources:
  - Direct RSS for sites that publish one (ZeroHedge, Substack)
  - Nitter RSS for X/Twitter-only accounts (tries multiple public instances)

Called automatically by market_signals.py, or standalone:

  python market_news.py              # print today's feed
  python market_news.py summary      # feed + Gemini AI summary

Environment variables:
  GEMINI_API_KEY   — Google Gemini API key (optional, enables AI summary)
                     Get a free key at aistudio.google.com/app/apikey
                     Then: export GEMINI_API_KEY="your_key_here"

No Twitter/X API key required.
"""

import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

ET_TZ = ZoneInfo("America/New_York")

# Gemini model to use
GEMINI_MODEL = "gemini-2.5-flash"

# ── FEED CONFIG ───────────────────────────────────────────────────────────────
# Map display handle → RSS URL.
# For X/Twitter-only accounts use a nitter:// pseudo-URL — the fetcher will
# try multiple public nitter instances automatically.
# Add, remove or swap URLs freely here.

FEEDS = {
    "zerohedge":    "https://feeds.feedburner.com/zerohedge/feed",
    "themarketear": "nitter://themarketear",
    "jam_croissant": "https://jamcroissant.substack.com/feed",
    "ozzy_livin":   "nitter://ozzy_livin",
}

# Public nitter instances to try in order (fallback if one is down)
NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.nl",
    "https://nitter.1d4.us",
]

# ── RSS FETCHER ───────────────────────────────────────────────────────────────

def _fetch_rss_xml(url, timeout=15):
    """Download RSS/Atom XML. Returns raw bytes or None."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; MarketSignalsBot/1.0)"
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        print(f"RSS fetch error [{url}]: {e}")
        return None


def _parse_items_today(xml_bytes):
    """
    Parse RSS or Atom XML and return items published today (ET).
    Returns list of title strings.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        print(f"RSS parse error: {e}")
        return []

    today_et = datetime.now(ET_TZ).date()
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = []

    # Support both RSS <item> and Atom <entry>
    for item in root.iter("item"):
        title   = (item.findtext("title") or "").strip()
        pub_raw = item.findtext("pubDate") or ""
        if _is_today(pub_raw, today_et):
            if title:
                items.append(title)

    for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
        title   = (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
        pub_raw = (
            entry.findtext("{http://www.w3.org/2005/Atom}published") or
            entry.findtext("{http://www.w3.org/2005/Atom}updated") or ""
        )
        if _is_today(pub_raw, today_et):
            if title:
                items.append(title)

    return items


def _is_today(date_str, today_et):
    """Return True if date_str (RSS or ISO 8601) falls on today_et."""
    if not date_str:
        return False
    date_str = date_str.strip()
    # Try RFC 2822 (RSS): "Mon, 30 Mar 2026 14:00:00 +0000"
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT"):
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(ET_TZ).date() == today_et
        except ValueError:
            pass
    # Try ISO 8601 (Atom): "2026-03-30T14:00:00+00:00"
    try:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET_TZ).date() == today_et
    except ValueError:
        pass
    return False


def _fetch_nitter(handle):
    """Try each nitter instance until one returns today's items."""
    for base in NITTER_INSTANCES:
        url  = f"{base}/{handle}/rss"
        data = _fetch_rss_xml(url)
        if data:
            items = _parse_items_today(data)
            if items is not None:   # even empty list is valid
                print(f"Nitter @{handle} via {base}: {len(items)} post(s) today")
                return items
    print(f"Nitter @{handle}: all instances failed.")
    return []


# ── PUBLIC FEED FETCHER ───────────────────────────────────────────────────────

def fetch_feed_today(feeds=None):
    """
    Fetch today's headlines from each configured feed.
    Returns {handle: [headline, ...]}
    """
    if feeds is None:
        feeds = FEEDS

    results = {}
    for handle, url in feeds.items():
        if url.startswith("nitter://"):
            twitter_handle = url[len("nitter://"):]
            results[handle] = _fetch_nitter(twitter_handle)
        else:
            data  = _fetch_rss_xml(url)
            items = _parse_items_today(data) if data else []
            print(f"RSS {handle}: {len(items)} post(s) today")
            results[handle] = items

    return results


# ── GEMINI ────────────────────────────────────────────────────────────────────

def summarize_with_gemini(feed_by_account):
    """
    Summarise today's headlines using Gemini Flash.
    Returns summary string or None.
    Set GEMINI_API_KEY environment variable to enable.
    Install: pip install google-generativeai
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return None

    try:
        import google.generativeai as genai
    except ImportError:
        print("google-generativeai not installed — run: pip install google-generativeai")
        return None

    lines = [
        f"{handle}: {headline}"
        for handle, headlines in feed_by_account.items()
        for headline in headlines
    ]
    if not lines:
        return None

    prompt = (
        "You are a concise financial analyst assistant. "
        "The following are today's headlines from market-focused sources. "
        "In 3-5 bullet points, summarise the key market themes, dominant sentiment "
        "(bullish / bearish / mixed), and any notable tickers or macro calls. "
        "Be brief and direct — no preamble.\n\n"
        + "\n".join(lines)
    )

    try:
        genai.configure(api_key=api_key)
        model    = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini error: {e}")
        return None


# ── FORMATTING ────────────────────────────────────────────────────────────────

def format_news_section(feed_by_account, summary=None):
    """Format the news block for inclusion in a report."""
    if not feed_by_account:
        return ""

    lines = [
        "",
        "╔═══════════════════════════════════════════╗",
        "║  MARKET NEWS — today's feed               ║",
        "╠═══════════════════════════════════════════╣",
    ]

    if summary:
        lines.append("  AI SUMMARY (Gemini)")
        for bullet in summary.splitlines():
            stripped = bullet.strip()
            if stripped:
                lines.append(f"  {stripped}")
        lines.append("")
        lines.append("  ─── HEADLINES ──────────────────────────────")

    for handle, headlines in feed_by_account.items():
        if not headlines:
            lines.append(f"  [{handle}]: (no posts today)")
        else:
            for h in headlines:
                short  = h[:120]
                suffix = "…" if len(h) > 120 else ""
                lines.append(f"  [{handle}] {short}{suffix}")
        lines.append("")

    lines.append("╚═══════════════════════════════════════════╝")
    return "\n".join(lines)


# ── PUBLIC API (imported by market_signals.py) ────────────────────────────────

def build_twitter_block():
    """Fetch feeds, optionally summarise, return formatted report block."""
    feed    = fetch_feed_today()
    summary = summarize_with_gemini(feed) if feed else None
    return format_news_section(feed, summary)


# ── STANDALONE ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    want_summary = len(sys.argv) > 1 and sys.argv[1] == "summary"
    feed    = fetch_feed_today()
    summary = summarize_with_gemini(feed) if (want_summary and feed) else None
    print(format_news_section(feed, summary) or "No posts found today.")
