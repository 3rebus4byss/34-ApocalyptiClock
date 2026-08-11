"""
ApocalyptiClock - hourly update job (one category per run).

What this actually does (no randomness, no placeholder text):
  1. Rotates through the risk categories one at a time, once per hour --
     never bursting multiple GDELT requests together. Over a 6-hour span,
     every category gets exactly one fresh check.
  2. For whichever category is "up" this hour, pulls real headlines from
     the last 6 hours via GDELT's free Doc API, then asks an AI model
     (Google Gemini, free tier -- no billing required) for a grounded,
     cited judgment: did THIS category's risk move up, down, or hold, and
     why -- citing the specific stories that informed the call.
  3. Writes the result to clock-data.json as a per-category cumulative
     offset, plus per-category "last checked" / "last updated" timestamps
     so the frontend can show exactly what changed and when each category
     is next due, without re-running anything.

If GDELT returns nothing for this hour's category, or the model call
fails, no number is invented. That category is left unchanged, logged as
a no-op, and gets picked up again on its next scheduled turn.

MODEL CHOICE: this uses Gemini's free tier (no credit card, generous
daily quota) to keep running costs at zero pre-revenue. The assessment
function is isolated in ask_ai_for_single_assessment() below -- once the
site earns enough to justify it, swap that one function for a Claude (or
other) API call without touching anything else in this file.
"""

import json
import os
import random
import sys
import time
from datetime import datetime, timezone

import requests

GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"

# Free-tier Gemini model. Check https://ai.google.dev/gemini-api/docs/models
# if this ever 404s -- Google renames/retires free-tier model IDs periodically.
# As of mid-2026, this is a current free-tier lightweight model.
GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)

# Real search queries -- one per risk category. Order matters: it defines
# the hourly rotation schedule (index 0 first, then 1, ... then wraps).
CATEGORIES = {
    "nuclear": {
        "label": "Nuclear",
        "query": "nuclear weapons OR nuclear escalation OR proliferation",
    },
    "conflict": {
        "label": "Conflict",
        "query": "war OR armed conflict OR military escalation",
    },
    "climate": {
        "label": "Climate",
        "query": "climate crisis OR extreme weather disaster",
    },
    "biosecurity": {
        "label": "Biosecurity",
        "query": "pandemic OR bioweapon OR disease outbreak",
    },
    "ai_risk": {
        "label": "AI Risk",
        "query": "AI safety OR artificial intelligence risk",
    },
    "human_factors": {
        "label": "Human Factors",
        "query": "misinformation OR disinformation OR mass panic OR "
                  "human error disaster OR conspiracy theory violence",
    },
}
CATEGORY_ORDER = list(CATEGORIES.keys())  # fixed rotation order
ROTATION_HOURS = len(CATEGORY_ORDER)      # each category is "due" every N hours

DATA_FILE = "public/clock-data.json"
STARTING_BASELINE = 85.0  # seconds to midnight, anchored to the real 2025 Bulletin setting
MAX_SHIFT_PER_TICK = 1.0        # cap on a single category's move in one check
MAX_CATEGORY_CUMULATIVE = 15.0  # cap on how far any one category can drift from 0 over time
MAX_RETRIES = 2               # after this many failed GDELT attempts, fall back to RSS
RETRY_BACKOFF_SECONDS = 20      # base wait before retrying a rate-limited/empty response
MIN_RUN_INTERVAL_MINUTES = 4    # guard against accidental back-to-back runs hammering GDELT

# Fallback news sources, used only if GDELT fails MAX_RETRIES times in a row.
# Plain RSS -- no API key, no query-based rate limiting like GDELT's DOC API.
RSS_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://www.theguardian.com/world/rss",
    "https://feeds.npr.org/1004/rss.xml",
]


def fetch_category_headlines(query: str, max_records: int = 8):
    """Pull real recent article titles + URLs from GDELT. Returns [] on failure."""
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": max_records,
        "timespan": "6h",
        "sort": "hybridrel",
        "format": "json",
    }
    headers = {"User-Agent": "ApocalyptiClock/1.0 (+news risk aggregator)"}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(GDELT_ENDPOINT, params=params, headers=headers, timeout=20)

            if resp.status_code == 429:
                wait = RETRY_BACKOFF_SECONDS * attempt
                print(f"[warn] GDELT rate-limited (429), attempt {attempt}/{MAX_RETRIES}, "
                      f"waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue

            resp.raise_for_status()

            # GDELT sometimes returns HTTP 200 with an EMPTY body when it's
            # throttling instead of a clean 429. Treat that the same way.
            body = resp.text.strip()
            if not body:
                wait = RETRY_BACKOFF_SECONDS * attempt
                print(f"[warn] GDELT returned an empty response (likely soft-throttled), "
                      f"attempt {attempt}/{MAX_RETRIES}, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue

            payload = json.loads(body)
            articles = payload.get("articles", [])
            return [
                {"title": a.get("title", "").strip(), "url": a.get("url", "")}
                for a in articles
                if a.get("title")
            ]
        except json.JSONDecodeError:
            wait = RETRY_BACKOFF_SECONDS * attempt
            print(f"[warn] GDELT returned non-JSON (likely throttled), "
                  f"attempt {attempt}/{MAX_RETRIES}, waiting {wait}s...", file=sys.stderr)
            time.sleep(wait)
            continue
        except Exception as exc:
            print(f"[warn] GDELT fetch failed: {exc}", file=sys.stderr)
            return []

    print(f"[warn] GDELT still unavailable after {MAX_RETRIES} attempts, falling back to RSS.",
          file=sys.stderr)
    return []


def fetch_rss_headlines(keywords: list, max_records: int = 8):
    """
    Fallback source when GDELT is unavailable. Pulls from a handful of major
    outlets' public RSS feeds and keeps only headlines matching at least one
    of the category's keywords (same keywords used in the GDELT query,
    substring-matched case-insensitively against the title).
    """
    import xml.etree.ElementTree as ET

    matches = []
    headers = {"User-Agent": "ApocalyptiClock/1.0 (+news risk aggregator)"}

    for feed_url in RSS_FEEDS:
        try:
            resp = requests.get(feed_url, headers=headers, timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)

            for item in root.iter("item"):
                title_el = item.find("title")
                link_el = item.find("link")
                if title_el is None or link_el is None:
                    continue
                title = (title_el.text or "").strip()
                link = (link_el.text or "").strip()
                if not title or not link:
                    continue

                title_lower = title.lower()
                if any(kw.strip().lower() in title_lower for kw in keywords if kw.strip()):
                    matches.append({"title": title, "url": link})
                    if len(matches) >= max_records:
                        return matches
        except Exception as exc:
            print(f"[warn] RSS fetch failed for {feed_url}: {exc}", file=sys.stderr)
            continue

    return matches


def get_headlines_for_category(cat_id: str):
    """
    Try GDELT first (primary source). If it fails after MAX_RETRIES attempts,
    fall back to RSS feeds filtered by the same category keywords. Returns
    (headlines, source_used) where source_used is 'gdelt', 'rss', or None.
    """
    query = CATEGORIES[cat_id]["query"]
    headlines = fetch_category_headlines(query)
    if headlines:
        return headlines, "gdelt"

    keywords = query.split(" OR ")
    headlines = fetch_rss_headlines(keywords)
    if headlines:
        print(f"[info] RSS fallback found {len(headlines)} matching headlines for "
              f"'{CATEGORIES[cat_id]['label']}'.")
        return headlines, "rss"

    return [], None


def ask_ai_for_single_assessment(cat_id: str, headlines: list):
    """
    Send this one category's real headlines to Gemini (free tier) and ask
    for a grounded, cited judgment. Returns
      {"direction", "delta_seconds", "rationale", "cited_urls"}
    or None if the model call fails or returns something unusable.
    """
    if not headlines:
        return None

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[warn] GEMINI_API_KEY not set -- skipping model assessment.", file=sys.stderr)
        return None

    label = CATEGORIES[cat_id]["label"]
    digest = "\n".join(f"- {h['title']} ({h['url']})" for h in headlines)

    system_prompt = (
        f"You are producing a periodic risk assessment for ApocalyptiClock, a website "
        f"that tracks catastrophic risk across separate categories, modeled loosely on "
        f"the Bulletin of the Atomic Scientists' Doomsday Clock but independent from it. "
        f"You are assessing ONLY the '{label}' category this run. You will be given REAL "
        f"headlines from the last 6 hours in this category. Base your judgment ONLY on "
        f"these headlines -- do not use outside knowledge of events not listed, and do "
        f"not invent stories.\n\n"
        "Respond with ONLY a JSON object, no other text, no markdown fences, in exactly "
        "this shape:\n"
        '{"direction": "worse" | "better" | "steady", '
        '"delta_seconds": <number between -1.0 and 1.0, negative means closer to '
        'midnight i.e. worse>, '
        '"rationale": "<1-2 sentences grounded in the specific headlines below>", '
        '"cited_urls": ["<url1>", "<url2>"]}\n'
        "Be conservative: most checks should produce a small move (under 0.3s). Only "
        "move further for genuinely significant developments clearly reflected in "
        "multiple headlines."
    )

    user_prompt = f"Real '{label}' headlines from the past 6 hours:\n{digest}\n\nReturn your JSON assessment now."

    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 400,
            "responseMimeType": "application/json",
        },
    }

    try:
        resp = requests.post(
            GEMINI_ENDPOINT,
            params={"key": api_key},
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()

        text = payload["candidates"][0]["content"]["parts"][0]["text"].strip()
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(text)

        delta = float(result["delta_seconds"])
        delta = max(-MAX_SHIFT_PER_TICK, min(MAX_SHIFT_PER_TICK, delta))
        return {
            "direction": result.get("direction", "steady"),
            "delta_seconds": round(delta, 2),
            "rationale": result.get("rationale", ""),
            "cited_urls": result.get("cited_urls", []) or [],
        }
    except Exception as exc:
        print(f"[warn] Gemini assessment failed: {exc}", file=sys.stderr)
        return None


def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            content = f.read().strip()
        return json.loads(content) if content else None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def default_data():
    return {
        "baseline": STARTING_BASELINE,
        "rotation_index": 0,
        "last_run_category": None,
        "last_run_time": None,
        "categories": {
            cat_id: {
                "label": cat["label"],
                "cumulative_delta": 0.0,
                "last_attempted": None,   # every time this category comes up in rotation
                "last_updated": None,     # only when that attempt actually produced real data
            }
            for cat_id, cat in CATEGORIES.items()
        },
        "history": [],
    }


def main():
    data = load_data() or default_data()

    # Backfill in case a category was added, or the schema predates rotation fields.
    data.setdefault("categories", {})
    for cat_id, cat in CATEGORIES.items():
        data["categories"].setdefault(cat_id, {
            "label": cat["label"], "cumulative_delta": 0.0,
            "last_attempted": None, "last_updated": None,
        })
    data.setdefault("rotation_index", 0)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Guard against accidental back-to-back runs (manual testing, overlapping
    # cron triggers) hammering GDELT within the same rate-limit window.
    if data.get("last_run_time"):
        try:
            last_run = datetime.strptime(data["last_run_time"], "%Y-%m-%d %H:%M:%S UTC")
            last_run = last_run.replace(tzinfo=timezone.utc)
            elapsed_minutes = (datetime.now(timezone.utc) - last_run).total_seconds() / 60
            if elapsed_minutes < MIN_RUN_INTERVAL_MINUTES:
                print(f"[info] Last run was {elapsed_minutes:.1f}m ago (minimum interval is "
                      f"{MIN_RUN_INTERVAL_MINUTES}m). Skipping this run entirely -- "
                      f"no request sent to GDELT.")
                return
        except ValueError:
            pass  # malformed timestamp, don't block on it

    # Which single category is "up" this run.
    idx = data["rotation_index"] % len(CATEGORY_ORDER)
    cat_id = CATEGORY_ORDER[idx]
    label = CATEGORIES[cat_id]["label"]

    headlines, source = get_headlines_for_category(cat_id)
    assessment = ask_ai_for_single_assessment(cat_id, headlines) if headlines else None

    cat_state = data["categories"][cat_id]
    cat_state["last_attempted"] = timestamp

    if assessment is None:
        print(f"[info] No update for '{label}' this hour (no data or model call failed).")
        data["history"].append({
            "time": timestamp,
            "category": cat_id,
            "status": "no_data",
        })
    else:
        new_cumulative = cat_state["cumulative_delta"] + assessment["delta_seconds"]
        new_cumulative = max(-MAX_CATEGORY_CUMULATIVE,
                              min(MAX_CATEGORY_CUMULATIVE, round(new_cumulative, 2)))
        cat_state["cumulative_delta"] = new_cumulative
        cat_state["last_updated"] = timestamp

        data["history"].append({
            "time": timestamp,
            "category": cat_id,
            "status": "updated",
            "delta": assessment["delta_seconds"],
            "direction": assessment["direction"],
            "rationale": assessment["rationale"],
            "sources": assessment["cited_urls"],
            "data_source": source,   # 'gdelt' or 'rss' -- transparency on where headlines came from
        })
        print(f"Updated '{label}' at {timestamp} (via {source}): "
              f"{assessment['delta_seconds']:+}s ({assessment['direction']})")

    data["history"] = data["history"][-60:]  # keep the log from growing forever
    data["last_run_category"] = cat_id
    data["last_run_time"] = timestamp
    data["rotation_index"] = (idx + 1) % len(CATEGORY_ORDER)

    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

    total = data["baseline"] + sum(c["cumulative_delta"] for c in data["categories"].values())
    next_cat = CATEGORY_ORDER[data["rotation_index"]]
    print(f"All-categories total: {round(total, 2)}s. Next up: {CATEGORIES[next_cat]['label']}.")


if __name__ == "__main__":
    main()
