"""
Data Models for FORGE Harness.

Provides:
- Pydantic dataclasses for task analysis (TaskType, Skill, AgentProfile, etc.)
- SQLAlchemy ORM models for persistent database (Base, Task, Agent, CCAgent, etc.)
- Delivery state model for cross-node xnode message tracking
"""

from .database import (
    Agent,
    AgentCapability,
    Base,
    CCAgent,
    CCTask,
    Node,
    Session,
    SyncLog,
    Task,
    TaskAssignment,
    TaskHistory,
    TaskRecommendation,
)
from .delivery import (
    TERMINAL_DELIVERY_STATES,
    VALID_DELIVERY_TRANSITIONS,
    DeliveryRecord,
    DeliveryState,
    DeliveryStats,
    DeliveryTransitionError,
    validate_delivery_transition,
)
from .lease import (
    VALID_LEASE_TRANSITIONS,
    LeaseState,
    LeaseStats,
    LeaseTransitionError,
    TaskLease,
    validate_lease_transition,
)
from .task_models import (
    AgentProfile,
    CodebaseContext,
    ComplexityLevel,
    RecommendationFeedback,
    Skill,
    TaskAnalysis,
    TaskType,
    TaskTypeHistory,
)
from .workflow import StepStatus

__all__ = [
    # Workflow enums
    "StepStatus",
    # Delivery state model (xnode cross-node tracking)
    "DeliveryState",
    "DeliveryRecord",
    "DeliveryStats",
    "DeliveryTransitionError",
    "VALID_DELIVERY_TRANSITIONS",
    "TERMINAL_DELIVERY_STATES",
    "validate_delivery_transition",
    # Lease model (control-plane task ownership & path locking)
    "LeaseState",
    "TaskLease",
    "LeaseStats",
    "LeaseTransitionError",
    "VALID_LEASE_TRANSITIONS",
    "validate_lease_transition",
    # Pydantic dataclass models
    "TaskType",
    "ComplexityLevel",
    "Skill",
    "TaskAnalysis",
    "TaskTypeHistory",
    "AgentProfile",
    "CodebaseContext",
    "RecommendationFeedback",
    # SQLAlchemy ORM models
    "Base",
    "Task",
    "Agent",
    "AgentCapability",
    "TaskRecommendation",
    "TaskHistory",
    "CCAgent",
    "CCTask",
    "TaskAssignment",
    "Session",
    "Node",
    "SyncLog",
]
