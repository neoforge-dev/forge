"""GitGuard integration — v3 approvals bridge and git state checks.

This package provides:
- bridge: Store approval queue data in v3 SQLite (Phase 1 integration).
- References to utils.git_guard for repo lock/rebase detection (see cli_v2.git_guard).
"""

from .bridge import (
    SQLiteApprovalStorage,
    create_v3_approval_storage,
    get_v3_db_path,
)

__all__ = [
    "SQLiteApprovalStorage",
    "get_v3_db_path",
    "create_v3_approval_storage",
]
