#!/bin/bash
# FORGE Git Helpers — GIT_INDEX_FILE workaround for sandbox-restricted nodes
# Source this file: source bin/git-helpers.sh
# Council decision: TC-S162-RETRO-2, P1 (2/2 APPROVE, CRITICAL priority)

gadd() {
    rm -f .git/index.lock
    local tmp="/tmp/forge-git-index-$$"
    cp .git/index "$tmp" && GIT_INDEX_FILE="$tmp" git add "$@" && cp "$tmp" .git/index
    local rc=$?
    rm -f "$tmp"
    return $rc
}

gcommit() {
    rm -f .git/index.lock
    local tmp="/tmp/forge-git-index-$$"
    cp .git/index "$tmp" && GIT_INDEX_FILE="$tmp" git commit "$@" && cp "$tmp" .git/index
    local rc=$?
    rm -f "$tmp"
    return $rc
}

gpull() {
    rm -f .git/index.lock
    local tmp="/tmp/forge-git-index-$$"
    cp .git/index "$tmp" && GIT_INDEX_FILE="$tmp" git pull --rebase "$@" && cp "$tmp" .git/index
    local rc=$?
    rm -f "$tmp"
    # Post-rebase safety check (P2: detect silent reverts)
    local changed
    changed=$(git diff HEAD@{1}..HEAD --name-only 2>/dev/null | wc -l | tr -d ' ')
    if [ "$changed" -gt 0 ]; then
        echo "WARNING: Rebase changed $changed files:"
        git diff HEAD@{1}..HEAD --stat
    fi
    return $rc
}

gpush() {
    rm -f .git/index.lock
    local tmp="/tmp/forge-git-index-$$"
    cp .git/index "$tmp" && GIT_INDEX_FILE="$tmp" git push "$@" && cp "$tmp" .git/index
    local rc=$?
    rm -f "$tmp"
    return $rc
}

# Convenience: add + commit + pull + push in one step
gship() {
    gadd "$@" && gcommit "${@:2}" && gpull origin main && gpush origin main
}

echo "Git helpers loaded: gadd, gcommit, gpull, gpush, gship"
