#!/usr/bin/env bash
# sparse-checkout-profiles.sh — Configure sparse checkout for 16GB nodes
# Usage: bin/sparse-checkout-profiles.sh <profile>
# Profiles: prya, vega, gaea, full
set -euo pipefail

PROFILE="${1:?Usage: $0 <prya|vega|gaea|full>}"

# Common infra dirs needed by all nodes
INFRA=(
  cmd/
  config/
  forge-shared/
  docs/
  .claude/
  .codex/
  .gemini/
  .cursor/
  .opencode/
  .forge/
  .github/
  bin/
  scripts/
  harness/
  docker/
  CLAUDE.md
  AGENTS.md
  Makefile
  go.work
  pyproject.toml
  .gitignore
)

case "$PROFILE" in
  prya)
    echo "Configuring sparse checkout for prya (hub, 16GB — infra only)"
    git sparse-checkout init --cone
    git sparse-checkout set "${INFRA[@]}"
    ;;
  vega)
    echo "Configuring sparse checkout for vega (16GB — brandfocus-ai domain)"
    git sparse-checkout init --cone
    git sparse-checkout set "${INFRA[@]}" \
      services/voice-coach/ \
      apps/brandfocus-landing/ \
      apps/brandfocus-web/ \
      apps/brand-brain/ \
      tools/investor-pitch-coach/ \
      tools/pkm-ai/
    ;;
  gaea)
    echo "Configuring sparse checkout for gaea (16GB — thebrightharbor + adguild)"
    git sparse-checkout init --cone
    git sparse-checkout set "${INFRA[@]}" \
      services/adguild-platform/ \
      apps/thebrightharbor-landing/ \
      apps/adguild-landing/ \
      apps/kiddo-rewards/ \
      apps/kiddo-rewards-family/ \
      tools/campaign-intelligence/ \
      tools/ad-asset-finder/
    ;;
  full)
    echo "Disabling sparse checkout (full checkout)"
    git sparse-checkout disable
    ;;
  *)
    echo "Unknown profile: $PROFILE"
    echo "Available: prya, vega, gaea, full"
    exit 1
    ;;
esac

echo "Done. Working tree size:"
du -sh . --exclude=.git 2>/dev/null || du -sh .
