# ApocalyptiClock

Every 4 hours, this checks **real headlines** for **one** risk category at a
time — nuclear, conflict, climate, biosecurity, AI risk, or information
warfare (misinformation, disinformation, propaganda) — rotating through all
six over a 24-hour span. Since there are 6 categories and 6 runs/day (00:00,
04:00, 08:00, 12:00, 16:00, 20:00 UTC), each category lands on the exact same
clock time every day. An AI model (Gemini, free tier) gives a grounded,
cited judgment: did that category's risk move up, down, or hold, and why,
citing the specific stories behind the call — with awareness of the
category's current standing and recent history, so it judges genuine
change rather than re-scoring the same ongoing situation every check.

**Runs entirely on GitHub's servers via GitHub Actions** — not your local
machine. It keeps running whether your computer is on, off, or unplugged.
The site is hosted on **GitHub Pages**, free, no credit card, no
credit-based billing to run out of.

No random numbers, no canned phrases. If a category's news fetch or the
model's judgment fails for its turn, that category is simply left unchanged
until its next scheduled check rather than faked.

## Architecture

```
GitHub Actions (every 4 hours)
  → fetch headlines for one category (RSS primary, GDELT Cloud fallback)
  → ask Gemini for a grounded assessment, with context on current standing
    and recent history for that category
  → commit public/clock-data.json back to the repo
  → deploy public/ to GitHub Pages
```

Your local machine is only used for *editing code* and *local testing* —
production runs entirely on GitHub's infrastructure, using secrets stored
in the repo (Settings → Secrets and variables → Actions), not your local
`.env` file. `.env` is for local testing only.

## Files

- `public/index.html` — static frontend: clock, category filters w/ schedule, log, background image, logo
- `public/clock-data.json` — baseline + per-category cumulative deltas, timestamps, rotation state, history
- `public/robots.txt` — fully open, allows all crawlers (search engines and AI) now that the site is public
- `public/assets/images/` — background image and logo
- `update_clock.py` — the update job: one category's headline fetch + Gemini assessment per run
- `.github/workflows/update-clock.yml` — the GitHub Actions workflow that runs everything on schedule, and deploys to Pages
- `.env.example` — template for **local testing only**; copy to `.env` and fill in real values
- `test_gemini.py` / `test_gdeltcloud.py` — standalone scripts to test each API key in isolation
- `run_update.sh` / `git_sync.sh` — local helper scripts (running the updater locally, syncing git safely against the bot's commits)
- `requirements.txt` — just `requests`

## Two-tier headline fallback

Each category check tries, in order:

1. **RSS feeds** (BBC, Al Jazeera, The Guardian, NPR) — primary. Free, no
   auth, no quota, filtered by the category's keywords.
2. **GDELT Cloud** (`gdeltcloud.com`) — used only if RSS finds nothing.
   Free tier is 100 query units/month, far too small to be primary at this
   run frequency, so this stays a rare fallback.

(An earlier version used GDELT's free DOC API as primary, but it proved
unreliable in practice — rate limits and occasional infrastructure
outages — so it was dropped entirely in favor of RSS.)

The history log records which tier actually supplied each successful
update (`"data_source": "rss" | "gdeltcloud"`).

## How the rotation works

`update_clock.py` keeps a `rotation_index` in the data file. Each run:
1. Picks the category at `rotation_index` (fixed order: nuclear → conflict
   → climate → biosecurity → ai_risk → human_factors → back to nuclear).
2. Fetches that category's real headlines (2-tier fallback above).
3. Asks Gemini for a judgment, giving it the category's current cumulative
   standing and its last 3 real updates as context — so it can recognize
   "no material new development" and correctly hold steady, rather than
   treating every check as an isolated snapshot with no memory.
4. Updates that category's `cumulative_delta` and `last_updated` if it got
   real data — or just records `last_attempted` if it didn't.
5. Advances `rotation_index` to the next category, win or lose.

## Manual per-category testing

GitHub → **Actions** → **Update ApocalyptiClock** → **Run workflow** shows
a dropdown to force a specific category to run immediately, instead of
following the normal rotation. Useful for testing without waiting for the
schedule. This doesn't disturb `rotation_index` for subsequent scheduled
runs.

Locally, the same override works via CLI arg or env var:
```bash
python3 update_clock.py nuclear
# or
CATEGORY_OVERRIDE=nuclear python3 update_clock.py
```
(`auto` or leaving it blank means normal rotation, not an override.)

## Setup

### 1. Get a free Gemini API key
No credit card, no billing setup. Go to https://aistudio.google.com/apikey,
sign in with any Google account, click "Create API key."

### 2. (Optional) Get a free GDELT Cloud key
Sign up at `gdeltcloud.com/auth/sign-up`, get a key at
`gdeltcloud.com/api-keys`. This is the rare fallback tier — the site works
fine without it, just with slightly less redundancy if RSS ever misses.

### 3. Add both as GitHub repository secrets
Repo → **Settings → Secrets and variables → Actions → New repository
secret**:
- `GEMINI_API_KEY`
- `GDELT_CLOUD_API_KEY` (optional)

### 4. Enable GitHub Pages
Repo → **Settings → Pages** → under "Build and deployment," set **Source**
to **GitHub Actions** (not "Deploy from a branch").

### 5. Trigger a run
**Actions** tab → **Update ApocalyptiClock** → **Run workflow**. Once it
succeeds, your site is live at `https://<your-username>.github.io/<repo-name>/`.

From here it runs itself every 4 hours, automatically, with no further
action.

### 6. Local testing (optional)
```bash
cp .env.example .env
nano .env   # fill in real keys
pip install -r requirements.txt --break-system-packages
set -a; source .env; set +a
python3 test_gemini.py        # confirms Gemini key works
python3 test_gdeltcloud.py    # confirms GDELT Cloud key works (if set)
python3 update_clock.py       # runs one real rotation step locally
```

Local runs write to `public/clock-data.json` just like the real workflow.
Use `git_sync.sh` (instead of plain `git push`) when pushing local changes,
since it safely handles the case where the scheduled bot has also
committed in the meantime:
```bash
./git_sync.sh "your commit message"
```
If a real merge conflict happens outside `clock-data.json` (unlikely,
since the bot only ever touches that one file), the script stops and shows
you exactly what needs manual resolution rather than guessing.

## Analytics

Cloudflare Web Analytics is wired in via a script tag before `</body>` in
`index.html` — free, privacy-respecting, no cookies. Check traffic at
Cloudflare dashboard → **Analytics & Logs → Web Analytics**.

## SEO / search indexing

`public/robots.txt` is fully open (`Allow: /`), so all search engines and
AI crawlers can index the site. A Google Search Console verification meta
tag is in `<head>` (`google-site-verification`) — use Search Console's
**URL Inspection → Request Indexing** to speed up initial discovery for a
brand-new domain with no existing backlinks. Bing Webmaster Tools can
import verification directly from an existing Search Console setup.

## Customizing the look

- **Background image**: `public/assets/images/`, referenced in the `body`
  CSS rule in `index.html`. Bump the `?v=` query param whenever you replace
  the file, or browsers will keep showing the old cached version.
- **Logo**: same folder, referenced in the `.logo-image` `<img>` tag near
  the top of the page body. Same cache-busting rule applies.

## Notes on honesty (read this before launch)

The disclaimer box in `index.html` says plainly that ApocalyptiClock is an
independent, unofficial, AI-generated reading of the news — not the real
Doomsday Clock. Keep it. It's what separates "an AI's grounded take on
current events, with sources" from "a number pretending to be more than it
is." The former is a genuinely interesting product; the latter is what
makes people distrust a site the moment they realize the trick.
