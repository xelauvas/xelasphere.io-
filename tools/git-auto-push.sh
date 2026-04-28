#!/bin/bash
# Auto-commit and push all projects to GitHub.
# Runs daily via cron (3am UTC). Treats main as a backup branch for solo
# developer workflow.
#
# Source of truth: /root/xelas.io/xelasphere.io-/tools/git-auto-push.sh
# Deployed to:     /usr/local/bin/git-auto-push.sh
#
# Issue #13 fix (2026-04-28): the original version added gitignored files
# to history because once a file was tracked, subsequent .gitignore
# additions wouldn't untrack it. The new "untrack any newly-ignored files"
# step before each `git add -A` prevents this class of pollution from
# recurring after future .gitignore additions.

LOG="/var/log/git-auto-push.log"
DATE=$(date '+%Y-%m-%d %H:%M')

echo "=== $DATE ===" >> "$LOG"

declare -A PROJECTS=(
    # App repos
    ["xelasphere"]="/root/xelas.io/xelasphere.io-"
    ["arrivo"]="/var/www/arrivo"
    ["eitio"]="/var/www/eitio"
    ["ratepulse"]="/var/www/ratepulse"
    ["hovely"]="/var/www/allocation"
    # Doc repos
    ["xelasphere-doc"]="/var/www/xelasphere-doc"
    ["arrivo-doc"]="/var/www/arrivo-doc"
    ["eitio-doc"]="/var/www/eitio-doc"
    ["ratepulse-doc"]="/var/www/rate-pulse-doc"
    ["hovely-doc"]="/var/www/hovely-doc"
)

for name in "${!PROJECTS[@]}"; do
    dir="${PROJECTS[$name]}"
    cd "$dir" 2>/dev/null || { echo "[$name] DIR NOT FOUND: $dir" >> "$LOG"; continue; }

    # Ensure we're on main branch
    current_branch=$(git branch --show-current 2>/dev/null)
    if [ "$current_branch" != "main" ]; then
        echo "[$name] WARNING: on branch '$current_branch', switching to main" >> "$LOG"
        git checkout main 2>> "$LOG"
    fi

    # Pull remote changes first to avoid push failures
    git pull --rebase origin main 2>> "$LOG"

    # Issue #13 safety: untrack any files that are tracked but match the
    # current .gitignore. Without this, adding a pattern to .gitignore later
    # doesn't actually stop those files from being re-staged by `git add -A`.
    # `git ls-files --ignored --exclude-standard --cached` lists exactly the
    # files in this state. The `|| true` keeps the script going if there are
    # zero such files (xargs may exit 0; this is defensive).
    git ls-files --ignored --exclude-standard --cached -z 2>/dev/null \
        | xargs -0 -r git rm --cached -q -- 2>> "$LOG" || true

    # Check for changes (after the safety untrack above, the staged set
    # may include the untrack deletions — that's intentional, they should
    # be committed so the cleanup is durable).
    if [ -z "$(git status --porcelain 2>/dev/null)" ]; then
        echo "[$name] no changes" >> "$LOG"
        continue
    fi

    # Stage, commit, push
    git add -A 2>> "$LOG"
    git commit -m "auto-backup: $DATE" 2>> "$LOG"
    git push origin main 2>> "$LOG"

    if [ $? -eq 0 ]; then
        echo "[$name] pushed successfully" >> "$LOG"
    else
        echo "[$name] PUSH FAILED" >> "$LOG"
    fi
done

echo "" >> "$LOG"
