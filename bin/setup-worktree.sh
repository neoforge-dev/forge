#!/usr/bin/env bash
# setup-worktree.sh — Create a worktree for parallel agent work
#
# Usage:
#   bin/setup-worktree.sh feat/my-feature                    # Full repo
#   bin/setup-worktree.sh feat/my-feature services/allergen-coach  # With scope hint
#
# Conventions:
#   - Worktrees live at ../forge-mono-{branch-slug}
#   - One agent per worktree, one worktree per branch
#   - Short-lived: merge to main → cleanup
#
# Cleanup:
#   git worktree remove ../forge-mono-feat-my-feature
#   git branch -d feat/my-feature

set -euo pipefail

BRANCH="${1:?Usage: $0 <branch-name> [scope]}"
SCOPE="${2:-}"
SLUG="${BRANCH//\//-}"
MONO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WT_PATH="$(dirname "$MONO_ROOT")/forge-mono-${SLUG}"

cd "$MONO_ROOT"

# Create or attach to worktree
if git worktree list --porcelain | grep -q "$WT_PATH"; then
    echo "Worktree already exists: $WT_PATH"
else
    git worktree add "$WT_PATH" -b "$BRANCH" 2>/dev/null \
        || git worktree add "$WT_PATH" "$BRANCH"
    echo "Worktree created: $WT_PATH"
fi

echo ""
echo "Agent instructions:"
echo "  cd $WT_PATH"
[ -n "$SCOPE" ] && echo "  Scope: $WT_PATH/$SCOPE"
echo ""
echo "When done:"
echo "  git worktree remove $WT_PATH"
echo "  git branch -d $BRANCH"
