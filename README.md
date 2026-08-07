# ApocalyptiClock

Once an hour, this checks **real headlines** (via GDELT's free news API)
for **one** risk category at a time — nuclear, conflict, climate,
biosecurity, AI risk, or human factors (misinformation, mass panic,
negligence-driven disasters) — rotating through all six over a 6-hour span.
GDELT rate-limits aggressively if you hit it with a burst of requests, so
each run makes exactly one call instead of six at once. Claude gives a
grounded, cited judgment for that one category: did its risk move up, down,
or hold, and why, citing the specific stories behind the call.

The displayed clock is the baseline plus the sum of each category's running
total. Visitors can toggle any category off in the browser — pure
client-side arithmetic against `clock-data.json`, no server round-trip — to
see what the setting would be without, say, AI risk or climate factored in.
Each category also shows when it was last checked/updated and roughly when
its next check is due, and the log shows exactly which single category
changed on each run — including runs where GDELT or the model returned
nothing usable, logged honestly as "no data" rather than skipped silently.

No random numbers, no canned phrases. If a category's news fetch or the
model's judgment fails for its turn, that category is simply left unchanged
until its next scheduled check rather than faked.

## Files

- `public/index.html` — static frontend: clock, category filters w/ schedule, log
- `public/clock-data.json` — baseline + per-category cumulative deltas, timestamps, rotation state, history. **This is the only pair of files that gets deployed publicly.**
- `update_clock.py` — the real update job (one category's GDELT fetch + Claude assessment per run); writes to `public/clock-data.json`
- `run_update.sh` — wrapper for cron: loads secrets from `.env`, runs the updater, deploys `public/` to Netlify, logs output
- `.env.example` — template for your API key + Netlify credentials; copy to `.env` and fill in real values (never committed, never deployed)
- `requirements.txt` — `requests` + `anthropic`
- `.github/workflows/update-clock.yml` — unused for this local + Netlify setup; only relevant if you later move automation to GitHub Actions instead

## How the rotation works

`update_clock.py` keeps a `rotation_index` in the data file. Each run:
1. Picks the category at `rotation_index` (fixed order: nuclear → conflict →
   climate → biosecurity → ai_risk → human_factors → back to nuclear).
2. Fetches that category's real headlines and asks Claude for a judgment.
3. Updates that category's `cumulative_delta` and `last_updated` if it got
   real data — or just records `last_attempted` if it didn't.
4. Advances `rotation_index` to the next category, win or lose.

Result: every category gets checked roughly once every 6 hours, but GDELT
only ever sees one isolated request per run instead of a burst of six.

## Data shape

```json
{
  "baseline": 85.0,
  "rotation_index": 2,
  "last_run_category": "conflict",
  "last_run_time": "2026-08-07 19:00:00 UTC",
  "categories": {
    "nuclear": {
      "label": "Nuclear",
      "cumulative_delta": -0.3,
      "last_attempted": "2026-08-07 18:00:00 UTC",
      "last_updated": "2026-08-07 18:00:00 UTC"
    }
  },
  "history": [
    {
      "time": "2026-08-07 19:00:00 UTC",
      "category": "conflict",
      "status": "updated",
      "delta": -0.1,
      "direction": "worse",
      "rationale": "...",
      "sources": ["..."]
    },
    {
      "time": "2026-08-07 18:00:00 UTC",
      "category": "nuclear",
      "status": "no_data"
    }
  ]
}
```

Displayed seconds = `baseline + sum(cumulative_delta for enabled categories)`.
The frontend recomputes this live as checkboxes are toggled, and computes
each category's "next check" as `last_attempted + 6h` (since a full
rotation is 6 hourly runs).

## 1. Set up your API key and Netlify credentials

Copy the template and fill in real values — never commit `.env` or put
secrets directly in your crontab, where `crontab -l` would expose them to
anyone with shell access:

```bash
cp .env.example .env
nano .env
```

You'll fill in `NETLIFY_AUTH_TOKEN` and `NETLIFY_SITE_ID` in step 3 below —
leave those blank for now if you don't have them yet.

## 2. Install the Netlify CLI

Requires Node.js/npm. On most Debian/Ubuntu-based systems:

```bash
sudo apt install nodejs npm    # skip if you already have Node
npm install -g netlify-cli
netlify --version               # confirms it installed
```

## 3. Create the site and get your credentials

**Get an auth token** (works over SSH/headless, no browser needed on the
server itself):
1. On any browser, log into https://app.netlify.com (free account).
2. Go to **User settings → Applications → Personal access tokens → New access token**.
3. Copy it into `.env` as `NETLIFY_AUTH_TOKEN`.

**Create the site and link this folder:**

```bash
cd /usr/local/erebusabyss/34-doomsday
netlify deploy --dir=public --auth="$NETLIFY_AUTH_TOKEN"
```

First run walks you through naming the site (or picks a random name like
`apocalypticlock-x7f2.netlify.app`) and creates it. This first deploy is a
**draft** deploy (safe, doesn't go live) — it prints a `Site ID` in the
output. Copy that into `.env` as `NETLIFY_SITE_ID`.

Once both values are in `.env`, do the real first deploy:

```bash
set -a; source .env; set +a
netlify deploy --prod --dir=public --auth="$NETLIFY_AUTH_TOKEN" --site="$NETLIFY_SITE_ID"
```

That prints your live URL, something like `https://apocalypticlock-x7f2.netlify.app`.
Open it — you should see the real site, not raw JSON.

## 4. Test the full automated flow once, manually

```bash
pip install -r requirements.txt --break-system-packages
chmod +x run_update.sh
./run_update.sh
cat update.log
```

This runs the updater (rotates one category) and then deploys `public/` to
Netlify automatically. If GDELT or the model call fails for that category,
you'll see a `[warn]` line and that category stays unchanged — that's the
safety fallback, not a bug. The deploy step still runs either way, so the
timestamps/rotation state stay in sync with what's live.

## 5. Schedule it with cron

```bash
crontab -e
```

Add this line to run it once an hour, on the hour:

```
0 * * * * /usr/local/erebusabyss/34-doomsday/run_update.sh
```

Save and exit. From here, every hour: cron runs the script → one category
gets checked against real headlines → Claude gives a grounded assessment →
`public/clock-data.json` updates → Netlify goes live with the new data,
automatically, with no further action from you. Check `update.log` any
time to see the history of runs.

## 6. Connect your `.com`

Once you've bought the domain (do this early — see notes above on domain
registration):
1. Netlify dashboard → your site → **Domain management → Add a domain**.
2. Enter your domain, e.g. `apocalypticlock.com`.
3. Netlify shows you DNS records to add at your registrar (usually either
   an A record pointing at Netlify's load balancer IP, or nameservers to
   switch to Netlify DNS — either works, Netlify's instructions are
   specific to your registrar).
4. Add those records at your registrar's DNS settings. Propagation usually
   takes minutes to a few hours.
5. Netlify auto-provisions a free HTTPS certificate (Let's Encrypt) once
   the domain resolves — no extra steps needed.

No changes to `run_update.sh` or cron are needed for this — the deploy step
always pushes to your Netlify site, and the custom domain just becomes an
alias that points at it.

## 7. Ads

Once live, apply to an ad network (e.g. Google AdSense) and drop their
verification snippet in `<head>` plus an `ads.txt` in the site root. The ad
container is already in `index.html` (`.ad-slot`) — swap the placeholder text
for the network's actual embed code.

## Notes on honesty (read this before launch)

The disclaimer box in `index.html` says plainly that ApocalyptiClock is an
unofficial, AI-generated reading of the news — not the real Doomsday Clock.
Keep it. It's
what separates "an AI's grounded take on current events, with sources" from
"a number pretending to be more than it is." The former is a genuinely
interesting product; the latter is what makes people distrust a site the
moment they realize the trick.
