#!/usr/bin/env python3
"""
update_site.py
Called by the GitHub Action. Reads the raw sales text from the
SALES_TEXT environment variable, asks Gemini to generate:
  1. The HTML event rows  (replaces the events block in index.html)
  2. A fresh JSON-LD block (replaces the existing ld+json script tag)
Then patches index.html in place.
"""

import os
import re
import json
import urllib.request
import urllib.error

# ── Config ────────────────────────────────────────────────────────────
API_KEY    = os.environ["GEMINI_API_KEY"]
SALES_TEXT = os.environ["SALES_TEXT"]
INDEX_FILE = "index.html"
MODEL      = "gemini-2.0-flash"   # free tier model

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{MODEL}:generateContent?key={API_KEY}"
)

# Markers that wrap the replaceable blocks in index.html
EVENTS_START = "<!-- EVENTS:START -->"
EVENTS_END   = "<!-- EVENTS:END -->"
JSONLD_START = "<!-- JSONLD:START -->"
JSONLD_END   = "<!-- JSONLD:END -->"

# ── Prompt ────────────────────────────────────────────────────────────
PROMPT = """
You are a code generator for the Warehouse Wardrobe website (warehousewardrobe.com.au).
Convert the raw sales list below into two things.

────────────────────────────────────────────────────────
1. HTML_EVENTS
────────────────────────────────────────────────────────
A sequence of event-row anchor tags and dividers in this exact pattern:

<a href="https://www.instagram.com/warehouse.wardrobe" target="_blank" rel="noopener noreferrer"
  class="event-row group flex flex-col md:flex-row md:items-center justify-between px-6 py-7 rounded-xl cursor-pointer">
  <div class="flex items-center gap-10 md:gap-16">
    <div class="text-center min-w-[52px]">
      <span class="block font-headline text-3xl text-primary">DD–DD</span>
      <span class="block font-label text-[9px] uppercase tracking-widest text-secondary">Mon</span>
    </div>
    <div>
      <h4 class="event-title font-headline text-2xl text-on-surface transition-all">Brand Name</h4>
      <p class="font-body text-sm text-secondary mt-0.5">Full address · hours per day</p>
    </div>
  </div>
  <div class="mt-4 md:mt-0 flex items-center gap-4 pl-[92px] md:pl-0">
    <span class="chip chip-free">Free Entry</span>
    <span class="material-symbols-outlined text-primary transition-transform group-hover:translate-x-1" style="font-size:18px;">arrow_forward</span>
  </div>
</a>
<div class="h-px bg-outline-variant/30 mx-6"></div>

Rules for HTML_EVENTS:
- For online-only events use chip-public class and "Online" label; set href to the brand URL
- Place a divider <div> after every event including the last
- Output only the raw rows and dividers — no wrapper divs
- Date format: "DD–DD" (en-dash) for ranges, "DD" for single days
- Keep hours concise: "Fri–Sat 9am–5pm · Sun 10am–3pm"
- Condense multi-line hours into one readable line

────────────────────────────────────────────────────────
2. JSONLD
────────────────────────────────────────────────────────
A complete <script type="application/ld+json"> tag containing a Schema.org @graph with:
- A WebSite node (url, name, description, sameAs Instagram + TikTok)
- An ItemList node with one SaleEvent per event
- AEST timezone offset +10:00 on all datetime values
- OnlineEventAttendanceMode for online events, OfflineEventAttendanceMode for physical
- isAccessibleForFree: true for all unless stated otherwise
- Approximate geo coordinates (lat/lng) for physical venues

────────────────────────────────────────────────────────
OUTPUT FORMAT
────────────────────────────────────────────────────────
Return a single JSON object with exactly two string keys:
{
  "HTML_EVENTS": "<raw HTML string>",
  "JSONLD": "<complete script tag string>"
}

No explanation. No markdown fences. No extra text. Only the JSON object.

────────────────────────────────────────────────────────
SALES LIST
────────────────────────────────────────────────────────
""".strip()


# ── Call Gemini ───────────────────────────────────────────────────────
def call_gemini(sales_text: str) -> dict:
    import time

    payload = json.dumps({
        "contents": [
            {
                "parts": [
                    {"text": PROMPT + "\n\n" + sales_text}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,          # low temp = consistent structured output
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json"  # ask Gemini to return raw JSON
        }
    }).encode()

    max_retries = 5
    wait = 15  # seconds — doubles each retry: 15 → 30 → 60 → 120s

    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(
            GEMINI_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                body = json.loads(resp.read())

            # Extract text from Gemini response structure
            raw_text = body["candidates"][0]["content"]["parts"][0]["text"].strip()

            # Strip markdown fences if present
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
            raw_text = re.sub(r"\s*```$",          "", raw_text)

            return json.loads(raw_text)

        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt == max_retries:
                    raise RuntimeError(
                        f"Gemini rate limit hit {max_retries} times in a row. "
                        "Wait a minute and re-run the workflow."
                    ) from e
                print(f"  Rate limited (429) — waiting {wait}s before retry {attempt}/{max_retries}…")
                time.sleep(wait)
                wait *= 2  # exponential backoff
            else:
                raise


# ── Patch index.html ──────────────────────────────────────────────────
def patch_between(html: str, start_marker: str, end_marker: str, replacement: str) -> str:
    pattern = re.compile(
        re.escape(start_marker) + r".*?" + re.escape(end_marker),
        re.DOTALL
    )
    new_block = f"{start_marker}\n{replacement}\n      {end_marker}"
    result, count = pattern.subn(new_block, html)
    if count == 0:
        raise ValueError(
            f"Markers '{start_marker}' … '{end_marker}' not found in {INDEX_FILE}.\n"
            "Make sure the comment markers exist in your index.html."
        )
    return result


# ── Main ──────────────────────────────────────────────────────────────
def main():
    print(f"Reading {INDEX_FILE}…")
    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    print("Calling Gemini API…")
    result = call_gemini(SALES_TEXT)

    print("Patching event rows…")
    html = patch_between(html, EVENTS_START, EVENTS_END, result["HTML_EVENTS"])

    print("Patching JSON-LD…")
    html = patch_between(html, JSONLD_START, JSONLD_END, result["JSONLD"])

    print(f"Writing {INDEX_FILE}…")
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print("Done ✓")


if __name__ == "__main__":
    main()
