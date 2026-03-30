"""
Market News — Gemini-powered daily feed

Uses Gemini with Google Search grounding to fetch and summarize today's posts
from watched X/Twitter accounts. No RSS, no scraping, no Twitter API needed.

Environment variables:
  GEMINI_API_KEY   — required. Get a free key at aistudio.google.com/app/apikey
                     export GEMINI_API_KEY="your_key_here"

Install: pip install google-genai
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ET_TZ        = ZoneInfo("America/New_York")
GEMINI_MODEL = "gemini-2.5-flash"

# Accounts to summarize — add or remove handles here
ACCOUNTS = ["zerohedge", "themarketear", "jam_croissant", "ozzy_livin"]


# ── FETCH + SUMMARIZE ─────────────────────────────────────────────────────────

def fetch_and_summarize(accounts=None):
    """
    Ask Gemini (with Google Search) to find and summarize today's posts
    from each account. Returns {handle: summary_text} or {} on failure.
    """
    if accounts is None:
        accounts = ACCOUNTS

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("GEMINI_API_KEY not set — skipping news feed.")
        return {}

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("google-genai not installed — run: pip install google-genai")
        return {}

    client = genai.Client(api_key=api_key)
    today  = datetime.now(ET_TZ).strftime("%B %d, %Y")

    results = {}
    for handle in accounts:
        prompt = (
            f"Search X (formerly Twitter) for posts by @{handle} published today, {today}. "
            f"Summarize what they posted in 2-4 sentences. "
            f"Cover: overall sentiment (bullish / bearish / mixed), key market themes, "
            f"any specific tickers or macro calls mentioned. "
            f"If there are no posts today, say so in one sentence."
        )
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                ),
            )
            results[handle] = response.text.strip()
            print(f"Gemini: fetched @{handle}")
        except Exception as e:
            print(f"Gemini error [@{handle}]: {e}")
            results[handle] = None

    return results


# ── FORMATTING ────────────────────────────────────────────────────────────────

def format_news_section(results):
    """Format the Gemini news block for inclusion in a report."""
    if not results:
        return ""

    lines = [
        "",
        "╔═══════════════════════════════════════════╗",
        "║  MARKET NEWS — Gemini daily feed          ║",
        "╠═══════════════════════════════════════════╣",
    ]

    for handle, summary in results.items():
        lines.append(f"  @{handle}")
        if summary:
            for line in summary.splitlines():
                stripped = line.strip()
                if stripped:
                    lines.append(f"    {stripped}")
        else:
            lines.append("    (unavailable)")
        lines.append("")

    lines.append("╚═══════════════════════════════════════════╝")
    return "\n".join(lines)


# ── PUBLIC API (imported by market_signals.py) ────────────────────────────────

def build_twitter_block():
    results = fetch_and_summarize()
    return format_news_section(results)


# ── STANDALONE ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    results = fetch_and_summarize()
    print(format_news_section(results) or "No results.")
