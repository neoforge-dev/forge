"""V3 Task Manager - SQLite-based task state machine.

Replaces file-based .forge_tasks/ and .forge/dispatches/ with ACID transactions.
"""

from .database import TaskDatabase, get_task_db
from .openclaw_bridge import OpenClawBridge, get_openclaw_bridge
from .scheduler import ResourceScheduler, get_scheduler
from .xnode_bridge import XNodeBridge, get_xnode_bridge

__all__ = [
    'TaskDatabase', 'get_task_db',
    'ResourceScheduler', 'get_scheduler',
    'XNodeBridge', 'get_xnode_bridge',
    'OpenClawBridge', 'get_openclaw_bridge'
]
