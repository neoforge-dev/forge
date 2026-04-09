#!/usr/bin/env bash
# gitsafe — hardened git wrapper for multi-agent nodes (gaea, nova, sati, prya).
#
# Prevents .git/index corruption caused by concurrent agent git operations by
# copying the index to a private tmp file, running the command against it, then
# copying it back with safety checks.
#
# Council S163 (original) + Council S175 (P1 mandate hardening).
#
# Usage:
#   gitsafe add file.py
#   gitsafe commit -m "message"
#   gitsafe status            # passthrough — no tmp index needed
#   gitsafe --help
#
# Environment:
#   GITSAFE_ENABLED=1   force-enable (any node)
#   GITSAFE_ENABLED=0   force-disable (passthrough everywhere)
#   GITSAFE_DEBUG=1     verbose stderr logging

set -euo pipefail

# ── helpers ─────────────────────────────────────────────────────────────────

log() {
  echo "[gitsafe] $*" >&2
}

debug() {
  [[ "${GITSAFE_DEBUG:-0}" == "1" ]] && echo "[gitsafe:debug] $*" >&2 || true
}

usage() {
  cat >&2 <<'EOF'
gitsafe — safe git wrapper for multi-agent nodes

Usage:
  gitsafe [git-command] [args...]
  gitsafe --help

Read-only commands (status, diff, log, show, branch, remote, stash list) are
passed directly to git without the tmp-index dance.

Write commands (add, commit, reset, checkout, merge, rebase, stash push/pop/drop)
use a private tmp index copy to avoid contention on .git/index.lock.

Environment variables:
  GITSAFE_ENABLED=1   force-enable on any node (default: auto-detect)
  GITSAFE_ENABLED=0   force-disable; always pass through to plain git
  GITSAFE_DEBUG=1     verbose logging to stderr

Nodes where gitsafe is auto-enabled: gaea, nova, sati, prya

Examples:
  gitsafe add bin/gitsafe.sh
  gitsafe commit -m "feat: improve wrapper"
  gitsafe status
  gitsafe log --oneline -5

Recovery:
  If gitsafe exits non-zero and leaves .git/index in an inconsistent state,
  run: git fsck --unreachable
  To reset fully: git read-tree HEAD
EOF
  exit 0
}

# ── node detection ───────────────────────────────────────────────────────────

is_multi_agent_node() {
  local node
  node="$(hostname -s 2>/dev/null || hostname)"
  case "$node" in
    gaea|nova|sati|prya) return 0 ;;
    *) return 1 ;;
  esac
}

should_use_safe_mode() {
  local enabled="${GITSAFE_ENABLED:-}"
  if [[ "$enabled" == "1" ]]; then
    debug "force-enabled via GITSAFE_ENABLED=1"
    return 0
  fi
  if [[ "$enabled" == "0" ]]; then
    debug "force-disabled via GITSAFE_ENABLED=0"
    return 1
  fi
  if is_multi_agent_node; then
    debug "auto-enabled: multi-agent node $(hostname -s 2>/dev/null || hostname)"
    return 0
  fi
  debug "passthrough: single-agent node, GITSAFE_ENABLED unset"
  return 1
}

# ── read-only passthrough detection ─────────────────────────────────────────

# Returns 0 (true) if the git subcommand is read-only and needs no index copy.
is_readonly_command() {
  local cmd="${1:-}"
  case "$cmd" in
    status|diff|log|show|branch|remote|ls-files|ls-tree|describe|shortlog|tag|rev-parse|rev-list|cat-file|blame|annotate|grep|bisect)
      return 0 ;;
    stash)
      # "stash list" and "stash show" are read-only; push/pop/drop are not
      local subcmd="${2:-}"
      case "$subcmd" in
        list|show) return 0 ;;
      esac
      return 1 ;;
    *)
      return 1 ;;
  esac
}

# ── stale lock cleanup ───────────────────────────────────────────────────────

cleanup_stale_lock() {
  local lock="$1"
  if [[ ! -f "$lock" ]]; then
    return 0
  fi

  local lock_age
  # stat -c on Linux, stat -f on macOS
  if stat --version >/dev/null 2>&1; then
    # GNU stat (Linux)
    lock_age=$(( $(date +%s) - $(stat -c %Y "$lock" 2>/dev/null || echo "$(date +%s)") ))
  else
    # BSD stat (macOS)
    lock_age=$(( $(date +%s) - $(stat -f %m "$lock" 2>/dev/null || echo "$(date +%s)") ))
  fi

  if (( lock_age > 30 )); then
    log "removing stale .git/index.lock (age: ${lock_age}s > 30s threshold)"
    rm -f "$lock"
  else
    debug "index.lock exists but is fresh (age: ${lock_age}s) — leaving it"
  fi
}

# ── index copy with retry ────────────────────────────────────────────────────

copy_index_with_retry() {
  local src="$1"
  local dst="$2"
  local attempt=0
  local max_attempts=3
  local delay=0.5

  while (( attempt < max_attempts )); do
    attempt=$(( attempt + 1 ))
    if cp "$src" "$dst" 2>/dev/null; then
      debug "index copy succeeded on attempt $attempt"
      return 0
    fi
    if (( attempt < max_attempts )); then
      log "index copy failed (attempt $attempt/$max_attempts) — retrying in ${delay}s"
      sleep "$delay"
    fi
  done

  log "ERROR: failed to copy index after $max_attempts attempts"
  log "Recovery: verify disk space with 'df -h' and check '$src' is readable"
  return 1
}

# ── copy-back safety check ───────────────────────────────────────────────────

safe_copy_back() {
  local tmp="$1"
  local dst="$2"

  # Verify tmp is non-empty
  if [[ ! -s "$tmp" ]]; then
    log "ERROR: tmp index is empty — refusing to overwrite $dst"
    log "Recovery: your original index is intact at $dst"
    return 1
  fi

  # Verify size is within ±50% of original
  local orig_size tmp_size
  if stat --version >/dev/null 2>&1; then
    orig_size=$(stat -c %s "$dst" 2>/dev/null || echo 0)
    tmp_size=$(stat -c %s "$tmp" 2>/dev/null || echo 0)
  else
    orig_size=$(stat -f %z "$dst" 2>/dev/null || echo 0)
    tmp_size=$(stat -f %z "$tmp" 2>/dev/null || echo 0)
  fi

  debug "index size check: original=${orig_size}B tmp=${tmp_size}B"

  if (( orig_size > 0 )); then
    # Allow tmp to be between 50% and 200% of original size
    local lower=$(( orig_size / 2 ))
    local upper=$(( orig_size * 2 ))
    if (( tmp_size < lower || tmp_size > upper )); then
      log "ERROR: tmp index size (${tmp_size}B) is outside ±50% of original (${orig_size}B)"
      log "Recovery: original index is intact at $dst; inspect with 'git status'"
      return 1
    fi
  fi

  cp "$tmp" "$dst"
  debug "index copied back successfully"
}

# ── main ─────────────────────────────────────────────────────────────────────

main() {
  # Handle --help before anything else
  if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
  fi

  # No arguments — just delegate to git for its own usage message
  if [[ $# -eq 0 ]]; then
    exec git
  fi

  local git_cmd="${1}"

  # If safe mode is not needed, pass through directly
  if ! should_use_safe_mode; then
    exec git "$@"
  fi

  # Read-only commands pass through without tmp index overhead
  if is_readonly_command "$@"; then
    debug "passthrough (read-only): $git_cmd"
    exec git "$@"
  fi

  # ── write path: use tmp index ──────────────────────────────────────────────

  local git_dir
  git_dir="$(git rev-parse --git-dir 2>/dev/null)" || {
    echo "[gitsafe] error: not inside a git repository" >&2
    echo "[gitsafe] Recovery: cd into your git repo and retry" >&2
    exit 1
  }

  # Resolve to absolute path (git returns relative path when inside the repo)
  if [[ "$git_dir" != /* ]]; then
    git_dir="$(pwd)/$git_dir"
  fi

  local index="$git_dir/index"
  local lock="$git_dir/index.lock"
  local tmp="/tmp/forge-git-index-$$"

  # Verify index exists
  if [[ ! -f "$index" ]]; then
    echo "[gitsafe] error: git index not found at $index" >&2
    echo "[gitsafe] Recovery: run 'git init' or verify this is a valid git repo" >&2
    exit 1
  fi

  # Clean up stale lock before we start
  cleanup_stale_lock "$lock"

  # Always clean up tmp on exit
  trap 'rm -f "$tmp"' EXIT

  # Copy index to tmp (with retry)
  copy_index_with_retry "$index" "$tmp" || exit 1

  debug "running: GIT_INDEX_FILE=$tmp git $*"

  # Run the git command against the tmp index
  GIT_INDEX_FILE="$tmp" git "$@"
  local git_exit=$?

  if (( git_exit != 0 )); then
    debug "git exited with code $git_exit — skipping copy-back"
    exit "$git_exit"
  fi

  # Copy back with safety checks
  safe_copy_back "$tmp" "$index" || exit 1

  exit 0
}

main "$@"
