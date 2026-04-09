#!/usr/bin/env bash
# node-setup.sh — Set up a new FORGE fleet node from scratch
#
# Usage:
#   bash bin/node-setup.sh              # Interactive setup
#   bash bin/node-setup.sh --check      # Verify existing setup
#
# Replaces the missing bin/node-migrate-v3 referenced in docs.
# Idempotent — safe to run multiple times.
set -euo pipefail

FORGE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOSTNAME=$(hostname)
MODE="${1:-setup}"

echo "=== FORGE Node Setup ==="
echo "Node:      $HOSTNAME"
echo "Repo:      $FORGE_ROOT"
echo "Mode:      $MODE"
echo ""

# ── Phase 1: Prerequisites ──────────────────────────────────────────────────

check_prereq() {
  local name="$1" cmd="$2"
  if command -v "$cmd" &>/dev/null; then
    echo "  [ok] $name: $(command -v "$cmd")"
    return 0
  else
    echo "  [MISSING] $name — install: $3"
    return 1
  fi
}

echo "Phase 1: Prerequisites"
MISSING=0
check_prereq "git"       git       "apt install git / brew install git" || MISSING=$((MISSING+1))
check_prereq "go"        go        "https://go.dev/dl/" || MISSING=$((MISSING+1))
check_prereq "tmux"      tmux      "apt install tmux / brew install tmux" || MISSING=$((MISSING+1))
check_prereq "curl"      curl      "apt install curl" || MISSING=$((MISSING+1))
check_prereq "jq"        jq        "apt install jq / brew install jq" || MISSING=$((MISSING+1))

if [ "$MISSING" -gt 0 ]; then
  echo ""
  echo "  $MISSING prerequisite(s) missing. Install them and re-run."
  [ "$MODE" = "--check" ] && exit 1
fi
echo ""

# ── Phase 2: Build Binaries ─────────────────────────────────────────────────

echo "Phase 2: Build binaries"

cd "$FORGE_ROOT/cmd/forge"
echo "  Building forge CLI..."
go build -o forge . 2>&1 | tail -3
mkdir -p "$HOME/.local/bin"
cp forge "$HOME/.local/bin/forge"
echo "  [ok] forge installed to ~/.local/bin/forge"

cd "$FORGE_ROOT/cmd/forged"
echo "  Building forged daemon..."
go build -o forged . 2>&1 | tail -3
cp forged "$HOME/.local/bin/forged"
echo "  [ok] forged installed to ~/.local/bin/forged"

# Ensure PATH includes ~/.local/bin
if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
  echo "  [warn] Add to your shell profile: export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
echo ""

# ── Phase 3: Environment ────────────────────────────────────────────────────

echo "Phase 3: Environment"

export FORGE_ROOT="$FORGE_ROOT"
echo "  FORGE_ROOT=$FORGE_ROOT"

# Detect hub vs worker node
if [ "$HOSTNAME" = "prya" ]; then
  FORGE_API_URL="http://localhost:8081"
  echo "  Node type: HUB (daemon runs locally)"
else
  FORGE_API_URL="http://prya:8081"
  echo "  Node type: WORKER (connects to prya:8081)"
  # Check Tailscale connectivity
  if curl -sf --connect-timeout 3 "$FORGE_API_URL/health" &>/dev/null; then
    echo "  [ok] prya reachable via Tailscale"
  else
    echo "  [warn] Cannot reach prya:8081 — check Tailscale: tailscale status"
  fi
fi
export FORGE_API_URL
echo "  FORGE_API_URL=$FORGE_API_URL"
echo ""

# ── Phase 4: Git Hooks ──────────────────────────────────────────────────────

echo "Phase 4: Git hooks"
cd "$FORGE_ROOT"
bash .forge/scripts/install-hooks.sh 2>&1 | sed 's/^/  /'
echo ""

# ── Phase 5: Cron Jobs ──────────────────────────────────────────────────────

echo "Phase 5: Cron jobs"

CRON_NEEDED=0

# Check heartbeat cron
if ! crontab -l 2>/dev/null | grep -q heartbeat-refresh; then
  echo "  [MISSING] heartbeat-refresh cron"
  CRON_NEEDED=1
fi

# Check dark factory cron (hub only)
if [ "$HOSTNAME" = "prya" ] && ! crontab -l 2>/dev/null | grep -q dark-factory; then
  echo "  [MISSING] dark-factory cron"
  CRON_NEEDED=1
fi

if [ "$CRON_NEEDED" -eq 1 ] && [ "$MODE" != "--check" ]; then
  echo "  Installing cron jobs..."
  (crontab -l 2>/dev/null || true; cat << CRON

# FORGE heartbeat — keep tmux agents registered as online
*/2 * * * * $FORGE_ROOT/.forge/scripts/heartbeat-refresh.sh >> /tmp/heartbeat-refresh.log 2>&1
CRON
  ) | sort -u | crontab -

  if [ "$HOSTNAME" = "prya" ]; then
    (crontab -l 2>/dev/null; cat << CRON

# FORGE dark factory — autonomous task dispatch
*/10 * * * * FORGE_API_URL=http://prya:8081 $FORGE_ROOT/.forge/scripts/dark-factory-v3.sh >> $FORGE_ROOT/.forge/heartbeat/logs/dark-factory.log 2>&1

# FORGE daily digest
3 7 * * * FORGE_ROOT=$FORGE_ROOT PATH=$FORGE_ROOT/cmd/forge:$HOME/.local/bin:\$PATH forge notify daily --quiet >> /tmp/forge-notify-daily.log 2>&1

# FORGE gate alerts every 4h
17 */4 * * * FORGE_ROOT=$FORGE_ROOT PATH=$FORGE_ROOT/cmd/forge:$HOME/.local/bin:\$PATH forge notify gates --quiet >> /tmp/forge-notify-gates.log 2>&1

# FORGE preflight every 30 min
*/30 * * * * FORGE_ROOT=$FORGE_ROOT PATH=$FORGE_ROOT/cmd/forge:$HOME/.local/bin:\$PATH forge preflight --quiet >> /tmp/forge-preflight.log 2>&1
CRON
    ) | sort -u | crontab -
  fi
  echo "  [ok] Cron jobs installed"
else
  echo "  [ok] Cron jobs already present"
fi
echo ""

# ── Phase 6: Disk Space ─────────────────────────────────────────────────────

echo "Phase 6: Disk space"
FREE_GB=$(df -BG / | tail -1 | awk '{print $4}' | tr -d 'G')
if [ "$FREE_GB" -lt 3 ]; then
  echo "  [WARN] Only ${FREE_GB}GB free — need 3GB minimum"
else
  echo "  [ok] ${FREE_GB}GB free"
fi
echo ""

# ── Phase 7: Verification ───────────────────────────────────────────────────

echo "Phase 7: Verification"

PASS=0; FAIL=0

verify() {
  if eval "$2" &>/dev/null; then
    echo "  [ok] $1"
    PASS=$((PASS+1))
  else
    echo "  [FAIL] $1"
    FAIL=$((FAIL+1))
  fi
}

verify "forge CLI"          "forge --version"
verify "forged binary"      "forged --version"
verify "git hooks"          "[ -L .git/hooks/pre-commit ]"
verify "heartbeat cron"     "crontab -l 2>/dev/null | grep -q heartbeat-refresh"
verify "FORGE_ROOT set"     "[ -n '$FORGE_ROOT' ]"
verify "API reachable"      "curl -sf --connect-timeout 3 $FORGE_API_URL/health"

echo ""
echo "=== Result: $PASS passed, $FAIL failed ==="

if [ "$FAIL" -eq 0 ]; then
  echo ""
  echo "Node $HOSTNAME is ready!"
  echo ""
  echo "Next steps:"
  echo "  1. Start tmux sessions:  bash .forge/scripts/forge-startup.sh"
  echo "  2. Attach to fleet:      tmux attach -t forge"
  echo "  3. Check fleet health:   forge status"
else
  echo ""
  echo "Fix the failures above, then re-run: bash bin/node-setup.sh"
fi
