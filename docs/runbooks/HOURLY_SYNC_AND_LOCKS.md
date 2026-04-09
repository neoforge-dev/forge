# Hourly Sync and Git Locks

## Purpose

Standardize safe multi-node synchronization and prevent concurrent Git conflicts.

## Components

1. Lock manager: `.forge/scripts/git-lock.sh`
2. Hourly sync runner: `.forge/scripts/hourly-sync.sh`
3. User systemd units:
   - `.forge/systemd/forge-hourly-sync.service`
   - `.forge/systemd/forge-hourly-sync.timer`
4. Installer: `scripts/install-hourly-sync-timer.sh`

## Install Hourly Sync (this node)

```bash
cd /home/openclaw/work/FORGE
chmod +x .forge/scripts/git-lock.sh .forge/scripts/hourly-sync.sh scripts/install-hourly-sync-timer.sh
scripts/install-hourly-sync-timer.sh
```

## Timer Behavior

1. Runs every hour (`OnCalendar=hourly`)
2. Acquires repo lock before sync
3. Skips if working tree is dirty
4. Skips if rebase/cherry-pick/merge is active
5. Rebase syncs `main` with `origin/main` (`--no-recurse-submodules`)
6. Updates submodules to superproject pointers
7. Logs to `.forge/logs/hourly-sync.log`

## Manual Lock Commands

```bash
# Acquire/release repo lock
.forge/scripts/git-lock.sh acquire --scope repo --agent forge:prya --task manual-sync --ttl 1800
.forge/scripts/git-lock.sh release --scope repo --agent forge:prya

# Inspect locks
.forge/scripts/git-lock.sh list
.forge/scripts/git-lock.sh status --scope repo
.forge/scripts/git-lock.sh cleanup
```

## Dispatch Guardrails

1. `forge dispatch send` checks Git lock by default.
2. If you need to clear a stale index lock: `rm -f .git/index.lock`
3. Push with retry on lock contention: `forge git push --retry`

