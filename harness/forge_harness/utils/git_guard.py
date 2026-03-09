"""
Git guard utilities — detect unsafe repository state.

Detects unsafe states that can cause lock contention or rebase conflicts:
- .git/index.lock — concurrent git operations
- .git/HEAD.lock — ref update in progress
- .git/rebase-apply — rebase in progress (apply)
- .git/rebase-merge — rebase in progress (merge)

Refactored from tests per PLAN.md #11 (Refactor Git Guard to Utils).
"""

from pathlib import Path

# Git lock file path (relative to repo root) — matches fleet.dispatch_client.GIT_LOCK_FILE
GIT_LOCK_FILE = ".git/index.lock"


def check_git_unsafe_state(repo_root: Path) -> tuple[bool, list[str]]:
    """Check git repository for unsafe state.

    Returns:
        (is_unsafe, list of reasons)
    """
    reasons = []
    # Path is repo_root / ".git/index.lock" — GIT_LOCK_FILE includes ".git/"
    if (repo_root / GIT_LOCK_FILE).exists():
        reasons.append("index.lock")
    if (repo_root / ".git" / "HEAD.lock").exists():
        reasons.append("HEAD.lock")
    if (repo_root / ".git" / "rebase-apply").exists() or (
        repo_root / ".git" / "rebase-merge"
    ).exists():
        reasons.append("rebase in progress")
    return (len(reasons) > 0, reasons)
