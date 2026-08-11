"""
ApocalyptiClock - hourly update job (one category per run).

What this actually does (no randomness, no placeholder text):
  1. Rotates through the risk categories one at a time, once per hour.
     Over a 6-hour span, every category gets exactly one fresh check.
  2. For whichever category is "up" this hour, pulls real headlines from
     public RSS feeds (primary), falling back to GDELT Cloud if RSS finds
     nothing, then asks an AI model (Google Gemini, free tier -- no
     billing required) for a grounded, cited judgment: did THIS category's
     risk move up, down, or hold, and why -- citing the specific stories
     that informed the call.
  3. Writes the result to clock-data.json as a per-category cumulative
     offset, plus per-category "last checked" / "last updated" timestamps
     so the frontend can show exactly what changed and when each category
     is next due, without re-running anything.

If both sources return nothing for this hour's category, or the model
call fails, no number is invented. That category is left unchanged,
logged as a no-op, and gets picked up again on its next scheduled turn.

MODEL CHOICE: this uses Gemini's free tier (no credit card, generous
daily quota) to keep running costs at zero pre-revenue. The assessment
function is isolated in ask_ai_for_single_assessment() below -- once the
site earns enough to justify it, swap that one function for a Claude (or
other) API call without touching anything else in this file.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

# Fallback tier (rarely used): GDELT Cloud (gdeltcloud.com), a separate
# newer paid/free-tier product from the GDELT team with real rate-limit
# headers and structured data. Free tier is only 100 query units/month --
# nowhere near enough to be primary at 24 checks/day, so this is used ONLY
# when RSS produces nothing in the same run.
GDELT_CLOUD_ENDPOINT = "https://gdeltcloud.com/api/v2/stories"

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
MIN_RUN_INTERVAL_MINUTES = 4    # guard against accidental back-to-back runs

# Primary source: plain public RSS feeds. No API key, no query-based rate
# limiting, no quota -- has proven more reliable in practice than GDELT's
# free DOC API ever was.
RSS_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://www.theguardian.com/world/rss",
    "https://feeds.npr.org/1004/rss.xml",
]


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

            feed_matches = 0
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
                    feed_matches += 1
                    if len(matches) >= max_records:
                        return matches
            print(f"[info] RSS {feed_url}: {feed_matches} keyword matches.", file=sys.stderr)
        except Exception as exc:
            print(f"[warn] RSS fetch failed for {feed_url}: {exc}", file=sys.stderr)
            continue

    return matches


def fetch_gdeltcloud_headlines(query: str, max_records: int = 5):
    """
    Third-tier fallback: GDELT Cloud's /stories endpoint. Only called if
    GDELT_CLOUD_API_KEY is set AND both the free GDELT DOC API and RSS
    produced nothing this run -- keeps us well within the 100 QU/month
    free tier given this should rarely trigger.

    Prefers real news-source URLs from each story's top_articles (skipping
    entries with a null title, which do occur in their data) over GDELT
    Cloud's own story page, since we want to cite the original article.
    """
    api_key = os.environ.get("GDELT_CLOUD_API_KEY")
    if not api_key:
        return []

    headers = {"Authorization": f"Bearer {api_key}"}
    # GDELT Cloud's search doesn't document boolean OR syntax like the old
    # DOC API -- use just the first keyword phrase, since this fallback only
    # needs to be "good enough", not exhaustive, given the tight quota.
    first_keyword = query.split(" OR ")[0].strip()

    try:
        resp = requests.get(
            GDELT_CLOUD_ENDPOINT,
            headers=headers,
            params={"search": first_keyword, "limit": max_records},
            timeout=20,
        )
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 30))
            print(f"[warn] GDELT Cloud rate-limited, waiting {retry_after}s and retrying once...",
                  file=sys.stderr)
            time.sleep(retry_after)
            resp = requests.get(
                GDELT_CLOUD_ENDPOINT,
                headers=headers,
                params={"search": first_keyword, "limit": max_records},
                timeout=20,
            )

        resp.raise_for_status()
        payload = resp.json()
        stories = payload.get("data", [])

        results = []
        for story in stories:
            # Prefer a real original-source article over GDELT Cloud's own page.
            picked = None
            for article in story.get("top_articles", []) or []:
                if article.get("title") and article.get("url"):
                    picked = {"title": article["title"], "url": article["url"]}
                    break
            if picked is None and story.get("title") and story.get("url"):
                picked = {"title": story["title"], "url": story["url"]}
            if picked:
                results.append(picked)

        return results
    except Exception as exc:
        print(f"[warn] GDELT Cloud fetch failed: {exc}", file=sys.stderr)
        return []


def get_headlines_for_category(cat_id: str):
    """
    Two-tier fallback chain:
      1. RSS feeds (primary) -- free, no quota, no auth
      2. GDELT Cloud, if RSS finds nothing AND GDELT_CLOUD_API_KEY is set
         (rare, keeps us well under its 100 QU/month free quota)
    Returns (headlines, source_used) where source_used is
    'rss', 'gdeltcloud', or None.
    """
    query = CATEGORIES[cat_id]["query"]
    label = CATEGORIES[cat_id]["label"]

    keywords = query.split(" OR ")
    headlines = fetch_rss_headlines(keywords)
    if headlines:
        print(f"[info] RSS succeeded for '{label}': {len(headlines)} matching headlines.")
        return headlines, "rss"

    print(f"[info] RSS produced nothing for '{label}' "
          f"(no matching headlines across {len(RSS_FEEDS)} feeds). "
          f"Trying GDELT Cloud (fallback)...")
    headlines = fetch_gdeltcloud_headlines(query)
    if headlines:
        print(f"[info] GDELT Cloud fallback succeeded for '{label}': {len(headlines)} headlines.")
        return headlines, "gdeltcloud"

    print(f"[info] Both sources produced nothing for '{label}' this hour.")
    return [], None


def ask_ai_for_single_assessment(cat_id: str, headlines: list, current_cumulative: float,
                                   recent_entries: list):
    """
    Send this one category's real headlines to Gemini (free tier) and ask
    for a grounded, cited judgment. Returns
      {"direction", "delta_seconds", "rationale", "cited_urls"}
    or None if the model call fails or returns something unusable.

    current_cumulative and recent_entries give the model context on where
    this category's risk level already stands and what was said in its
    last few checks -- without this, every check was an isolated snapshot
    with no memory, which caused genuinely independent judgments to
    sometimes cancel each other out (a "worse" call followed shortly by
    an unrelated "better" call nets to ~0, looking like nothing happened
    even though two real assessments were made). With context, the model
    is asked to judge change relative to where things already stand, which
    produces more deliberate, less noisy movement -- closer to how the
    real Bulletin's annual assessment works.
    """
    if not headlines:
        return None

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[warn] GEMINI_API_KEY not set -- skipping model assessment.", file=sys.stderr)
        return None

    label = CATEGORIES[cat_id]["label"]
    digest = "\n".join(f"- {h['title']} ({h['url']})" for h in headlines)

    if recent_entries:
        history_lines = "\n".join(
            f"- {e['time']}: {e['direction']} ({e['delta']:+.2f}s) -- {e['rationale']}"
            for e in recent_entries
        )
        history_context = (
            f"\nThis category's recent check history (most recent last):\n{history_lines}\n"
        )
    else:
        history_context = "\nThis category has no prior check history yet -- this is its first assessment.\n"

    system_prompt = (
        f"You are producing a periodic risk assessment for ApocalyptiClock, a website "
        f"that tracks catastrophic risk across separate categories, modeled loosely on "
        f"the Bulletin of the Atomic Scientists' Doomsday Clock but independent from it. "
        f"You are assessing ONLY the '{label}' category this run. You will be given REAL "
        f"headlines from the last 6 hours in this category, PLUS the category's current "
        f"cumulative standing and its recent check history. Base your judgment ONLY on "
        f"the headlines provided -- do not use outside knowledge of events not listed, "
        f"and do not invent stories.\n\n"
        f"IMPORTANT: judge whether the NEW headlines represent a genuine change relative "
        f"to the established recent trend, not just whether they sound concerning in "
        f"isolation. If the new headlines are simply continuing coverage of an "
        f"already-reflected situation with no material new development, the correct "
        f"call is 'steady' with a delta near 0 -- do not re-penalize or re-reward the "
        f"same ongoing situation on every check. Only call 'worse' or 'better' when "
        f"headlines show an actual escalation or de-escalation beyond what's already "
        f"been accounted for.\n\n"
        "Respond with ONLY a JSON object, no other text, no markdown fences, in exactly "
        "this shape:\n"
        '{"direction": "worse" | "better" | "steady", '
        '"delta_seconds": <number between -1.0 and 1.0, negative means closer to '
        'midnight i.e. worse>, '
        '"rationale": "<1-2 sentences grounded in the specific headlines below>", '
        '"cited_urls": ["<url1>", "<url2>"]}\n'
        "Be conservative: most checks should produce a small move (under 0.3s) or none "
        "at all. Only move further for genuinely significant NEW developments clearly "
        "reflected in multiple headlines."
    )

    user_prompt = (
        f"Current cumulative standing for '{label}': {current_cumulative:+.2f}s "
        f"relative to baseline.\n"
        f"{history_context}\n"
        f"Real '{label}' headlines from the past 6 hours:\n{digest}\n\n"
        f"Return your JSON assessment now."
    )

    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 800,
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
        try:
            result = json.loads(text)
        except json.JSONDecodeError as parse_exc:
            # Show what actually came back, truncated or not -- makes future
            # failures like this one immediately diagnosable instead of just
            # showing "Unterminated string" with no context.
            print(f"[warn] Gemini returned unparseable JSON: {parse_exc}", file=sys.stderr)
            print(f"[warn] Raw response was: {text[:1000]}", file=sys.stderr)
            return None

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

    # Manual override for testing a specific category, instead of following
    # the strict rotation. Two ways to set it:
    #   - CLI arg:      python3 update_clock.py nuclear
    #   - env var:      CATEGORY_OVERRIDE=nuclear python3 update_clock.py
    # The GitHub Actions workflow_dispatch input feeds this via the env var.
    override = None
    if len(sys.argv) > 1 and sys.argv[1].strip():
        override = sys.argv[1].strip()
    elif os.environ.get("CATEGORY_OVERRIDE", "").strip():
        override = os.environ["CATEGORY_OVERRIDE"].strip()

    if override:
        if override not in CATEGORIES:
            print(f"[error] Unknown category override '{override}'. "
                  f"Valid options: {', '.join(CATEGORY_ORDER)}", file=sys.stderr)
            sys.exit(1)
        print(f"[info] Manual override: forcing category '{override}' "
              f"(bypassing normal rotation for this run only).")
    else:
        # Guard against accidental back-to-back runs (manual testing, overlapping
        # cron triggers) hammering GDELT within the same rate-limit window.
        # Skipped entirely when a manual override is used, since that's an
        # intentional one-off test, not a background scheduling collision.
        if data.get("last_run_time"):
            try:
                last_run = datetime.strptime(data["last_run_time"], "%Y-%m-%d %H:%M:%S UTC")
                last_run = last_run.replace(tzinfo=timezone.utc)
                elapsed_minutes = (datetime.now(timezone.utc) - last_run).total_seconds() / 60
                if elapsed_minutes < MIN_RUN_INTERVAL_MINUTES:
                    print(f"[info] Last run was {elapsed_minutes:.1f}m ago (minimum interval is "
                          f"{MIN_RUN_INTERVAL_MINUTES}m). Skipping this run entirely -- "
                          f"skipping this run entirely.")
                    return
            except ValueError:
                pass  # malformed timestamp, don't block on it

    # Which single category is "up" this run -- either the override, or
    # whatever the normal rotation says.
    if override:
        cat_id = override
        idx = CATEGORY_ORDER.index(cat_id)  # keep rotation_index math correct below
    else:
        idx = data["rotation_index"] % len(CATEGORY_ORDER)
        cat_id = CATEGORY_ORDER[idx]
    label = CATEGORIES[cat_id]["label"]

    headlines, source = get_headlines_for_category(cat_id)

    cat_state = data["categories"][cat_id]
    current_cumulative = cat_state["cumulative_delta"]
    recent_entries = [
        e for e in data.get("history", [])
        if e.get("category") == cat_id and e.get("status") == "updated"
    ][-3:]  # last 3 real updates for this category, most recent last

    assessment = (
        ask_ai_for_single_assessment(cat_id, headlines, current_cumulative, recent_entries)
        if headlines else None
    )
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
            "data_source": source,   # 'rss' or 'gdeltcloud' -- transparency on where headlines came from
        })
        print(f"Updated '{label}' at {timestamp} (via {source}): "
              f"{assessment['delta_seconds']:+}s ({assessment['direction']})")

    data["history"] = data["history"][-60:]  # keep the log from growing forever
    data["last_run_category"] = cat_id
    data["last_run_time"] = timestamp
    if not override:
        data["rotation_index"] = (idx + 1) % len(CATEGORY_ORDER)
    # else: leave rotation_index untouched -- a manual test shouldn't skip
    # or reorder the categories scheduled runs will check next.

    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

    total = data["baseline"] + sum(c["cumulative_delta"] for c in data["categories"].values())
    next_cat = CATEGORY_ORDER[data["rotation_index"]]
    print(f"All-categories total: {round(total, 2)}s. Next up: {CATEGORIES[next_cat]['label']}.")


if __name__ == "__main__":
    main()
