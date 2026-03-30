"""
Market News — Gemini-powered daily feed

Uses Gemini with Google Search grounding to find and summarize today's content
from market sources. Targets each source's actual web presence (site, Substack, etc.)
since X/Twitter posts are not indexed by Google Search.

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

# ── SOURCES ───────────────────────────────────────────────────────────────────
# Each entry: display_name → search prompt.
# Prompts target each source's actual web presence — X posts are not indexed
# by Google Search, so asking for tweets returns nothing useful.

SOURCES = {
    "ZeroHedge": (
        "Search zerohedge.com for their latest articles published today. "
        "Summarize the top 2-3 stories and their market implications in 3-4 sentences. "
        "Focus on sentiment (bullish/bearish), key macro themes, and any specific ticker calls."
    ),
    "TheMarketEar": (
        "Search themarketear.com for their latest market observations published today. "
        "Summarize the key themes and market calls in 3-4 sentences. "
        "Focus on sentiment, asset classes mentioned, and any notable warnings or opportunities."
    ),
}


# ── FETCH + SUMMARIZE ─────────────────────────────────────────────────────────

def fetch_and_summarize():
    """
    Ask Gemini (with Google Search) to find and summarize today's content
    from each source. Returns {name: summary_text} or {} on failure.
    """
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
    for name, prompt_template in SOURCES.items():
        prompt = f"Today is {today}. {prompt_template}"
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                ),
            )
            results[name] = response.text.strip()
            print(f"Gemini: fetched {name}")
        except Exception as e:
            print(f"Gemini error [{name}]: {e}")
            results[name] = None

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

    for name, summary in results.items():
        lines.append(f"  {name}")
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
