#!/usr/bin/env bash
# Wrapper for cron:
#   1. Load secrets from .env
#   2. Run the updater (rotates one category, writes public/clock-data.json)
#   3. Push the public/ folder live to Netlify
#   4. Log everything
#
# Adjust PROJECT_DIR if this script lives somewhere other than the repo root.

set -euo pipefail

PROJECT_DIR="/usr/local/erebusabyss/34-doomsday"
LOG_FILE="$PROJECT_DIR/update.log"

cd "$PROJECT_DIR"

if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  source "$PROJECT_DIR/.env"
  set +a
fi

{
  echo "=== $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="

  python3 update_clock.py

  if [ -z "${NETLIFY_AUTH_TOKEN:-}" ] || [ -z "${NETLIFY_SITE_ID:-}" ]; then
    echo "[warn] NETLIFY_AUTH_TOKEN or NETLIFY_SITE_ID not set in .env -- skipping deploy."
  else
    echo "--- deploying public/ to Netlify ---"
    netlify deploy --prod --dir=public --auth="$NETLIFY_AUTH_TOKEN" --site="$NETLIFY_SITE_ID"
  fi

  echo ""
} >> "$LOG_FILE" 2>&1
