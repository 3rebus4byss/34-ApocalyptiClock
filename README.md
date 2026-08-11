# ApocalyptiClock

Every hour, this checks **real headlines** for **one** risk category at a
time — nuclear, conflict, climate, biosecurity, AI risk, or human factors
(misinformation, mass panic, negligence) — rotating through all six over a
6-hour span. An AI model (Gemini, free tier) gives a grounded, cited
judgment: did that category's risk move up, down, or hold, and why, citing
the specific stories behind the call.

**Runs entirely on GitHub's servers via GitHub Actions** — not your local
machine. It keeps running whether your computer is on, off, or unplugged.
The site is hosted on **GitHub Pages**, free, no credit card, no credit-based
billing to run out of.

No random numbers, no canned phrases. If a category's news fetch or the
model's judgment fails for its turn, that category is simply left unchanged
until its next scheduled check rather than faked.

## Architecture

```
GitHub Actions (hourly cron)
  → fetch headlines for one category (3-tier fallback, see below)
  → ask Gemini for a grounded assessment
  → commit public/clock-data.json back to the repo
  → deploy public/ to GitHub Pages
```

Your local machine is only used for *editing code* and *local testing* —
production runs entirely on GitHub's infrastructure, using secrets stored
in the repo (Settings → Secrets and variables → Actions), not your local
`.env` file. `.env` is for local testing only.

## Files

- `public/index.html` — static frontend: clock, category filters w/ schedule, log
- `public/clock-data.json` — baseline + per-category cumulative deltas, timestamps, rotation state, history
- `public/robots.txt` — blocks search engine indexing (remove when ready to launch publicly)
- `public/sw.js` — Monetag ad-network verification service worker
- `update_clock.py` — the update job: one category's headline fetch + Gemini assessment per run
- `.github/workflows/update-clock.yml` — the GitHub Actions workflow that runs everything hourly, and deploys to Pages
- `.env.example` — template for **local testing only**; copy to `.env` and fill in real values
- `test_gemini.py` / `test_gdeltcloud.py` — standalone scripts to test each API key in isolation
- `run_update.sh` / `git_sync.sh` — local helper scripts (running the updater locally, syncing git safely against the bot's commits)
- `requirements.txt` — just `requests`

## Three-tier headline fallback

Each category check tries, in order:

1. **GDELT's free DOC API** (primary) — no key needed, but can be
   unreliable (rate limits, occasional outages).
2. **RSS feeds** (BBC, Al Jazeera, The Guardian, NPR) — used if GDELT fails,
   filtered by the category's keywords.
3. **GDELT Cloud** (`gdeltcloud.com`) — used only if *both* above fail.
   Free tier is 100 query units/month, far too small to be primary at
   24 checks/day, so this stays a rare last resort.

The history log records which tier actually supplied each successful
update (`"data_source": "gdelt" | "rss" | "gdeltcloud"`).

## How the rotation works

`update_clock.py` keeps a `rotation_index` in the data file. Each run:
1. Picks the category at `rotation_index` (fixed order: nuclear → conflict
   → climate → biosecurity → ai_risk → human_factors → back to nuclear).
2. Fetches that category's real headlines (3-tier fallback above) and asks
   Gemini for a judgment.
3. Updates that category's `cumulative_delta` and `last_updated` if it got
   real data — or just records `last_attempted` if it didn't.
4. Advances `rotation_index` to the next category, win or lose.

Result: every category gets checked roughly once every 6 hours.

## Manual per-category testing

GitHub → **Actions** → **Update ApocalyptiClock** → **Run workflow** shows
a dropdown to force a specific category to run immediately, instead of
following the normal rotation. Useful for testing without waiting for the
schedule. This doesn't disturb `rotation_index` for subsequent scheduled runs.

Locally, the same override works via CLI arg or env var:
```bash
python3 update_clock.py nuclear
# or
CATEGORY_OVERRIDE=nuclear python3 update_clock.py
```

## Setup

### 1. Get a free Gemini API key
No credit card, no billing setup. Go to https://aistudio.google.com/apikey,
sign in with any Google account, click "Create API key."

### 2. (Optional) Get a free GDELT Cloud key
Sign up at `gdeltcloud.com/auth/sign-up`, get a key at
`gdeltcloud.com/api-keys`. This is the rare third-tier fallback — the site
works fine without it, just with slightly less redundancy.

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

From here it runs itself every hour, automatically, with no further action.

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
since it safely handles the case where the hourly bot has also committed
in the meantime:
```bash
./git_sync.sh "your commit message"
```

## Ads

Ad network codes are already wired into `public/index.html`:
- Adsterra Banner 728x90 and Native Banner in the visible `ad-slot` divs
- Monetag In-Page Push tag in `<head>`

`public/sw.js` is Monetag's site-verification service worker — must stay
at the `public/` root to work. `public/robots.txt` currently blocks all
search engines (pre-launch); remove or edit it when ready to be indexed.

## Notes on honesty (read this before launch)

The disclaimer box in `index.html` says plainly that ApocalyptiClock is an
independent, unofficial, AI-generated reading of the news — not the real
Doomsday Clock. Keep it. It's what separates "an AI's grounded take on
current events, with sources" from "a number pretending to be more than it
is." The former is a genuinely interesting product; the latter is what
makes people distrust a site the moment they realize the trick.
