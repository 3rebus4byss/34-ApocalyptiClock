#!/usr/bin/env bash
# git_sync.sh -- commit local changes, pull the bot's updates, push.
#
# The hourly GitHub Actions bot commits public/clock-data.json on its own
# schedule. If you've made local edits since the bot last ran, a plain
# `git push` will get rejected because your branch is behind. This script
# handles that automatically: it commits your changes, pulls the bot's
# update, resolves the (expected, harmless) clock-data.json conflict by
# always keeping the bot's live version, and pushes the result.
#
# Usage:
#   ./git_sync.sh "your commit message"
#   ./git_sync.sh                          (uses a default message)

set -e
cd "$(dirname "$0")"

COMMIT_MSG="${1:-Local update}"

# --- Stage and commit local changes, if any ---
git add -A
if ! git diff --cached --quiet; then
    git commit -m "$COMMIT_MSG"
    echo "[info] Committed local changes: $COMMIT_MSG"
else
    echo "[info] No local changes to commit."
fi

# --- Pull the bot's updates ---
echo "[info] Pulling latest from origin/main..."
if git pull --no-rebase; then
    echo "[info] Pull completed cleanly, no conflicts."
else
    echo "[warn] Merge conflict detected -- attempting automatic resolution..."

    # The only file that regularly conflicts is the bot-managed data file.
    # The bot's version is always the live, correct one -- always keep it.
    if git status --porcelain | grep -q "public/clock-data.json"; then
        git checkout --theirs public/clock-data.json
        git add public/clock-data.json
        echo "[info] Resolved public/clock-data.json using the bot's version."
    fi

    # If anything else is still conflicted, stop here rather than guess.
    if git status --porcelain | grep -qE "^(UU|AA|DD)"; then
        echo "[error] Unresolved conflicts remain outside clock-data.json:"
        git status --porcelain | grep -E "^(UU|AA|DD)"
        echo "Resolve these manually, then run: git commit && git push"
        exit 1
    fi

    git commit -m "Merge: auto-resolved clock-data.json conflict with bot update"
    echo "[info] Merge conflict resolved and committed."
fi

# --- Push ---
echo "[info] Pushing to origin/main..."
git push
echo "[info] Done."
