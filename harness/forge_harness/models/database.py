"""SQLAlchemy ORM models for FORGE Command Center database.

Defines the persistent schema for agents, tasks, sessions, nodes,
and related tables. These models correspond to the raw SQL schema
in migrations/env.py and are used by Alembic for auto-discovery.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


def generate_uuid() -> str:
    return str(uuid.uuid4())


# ──────────────────────────────────────────────
# Legacy task-recommendation tables
# ──────────────────────────────────────────────


class Task(Base):
    """Legacy task table for recommendation engine."""

    __tablename__ = "tasks"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    subject = Column(String(500), nullable=False)
    description = Column(Text)
    priority = Column(Integer, default=5)
    domain = Column(String(100))
    project = Column(String(100))
    status = Column(String(11), default="pending")
    task_type = Column(String(18), default="general")
    complexity = Column(String(1), default="2")
    estimated_duration_minutes = Column(Integer)
    dependencies = Column(Text, default="[]")  # JSON
    blocked = Column(Boolean, default=False)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    # Relationships
    assigned_agent = relationship(
        "Agent", back_populates="current_task", foreign_keys="Agent.current_task_id"
    )
    recommendations = relationship("TaskRecommendation", back_populates="task")
    history = relationship("TaskHistory", back_populates="task")


class Agent(Base):
    """Legacy agent table for recommendation engine."""

    __tablename__ = "agents"

    id = Column(String(100), primary_key=True)
    name = Column(String(200), nullable=False)
    model = Column(String(100), nullable=False)
    status = Column(String(6), default="idle")
    max_complexity = Column(String(1), default="3")
    preferred_domains = Column(Text, default="[]")  # JSON
    current_task_id = Column(String(36), ForeignKey("tasks.id"))
    last_activity = Column(DateTime)
    total_tasks_completed = Column(Integer, default=0)
    success_rate = Column(Float, default=0.0)
    avg_task_duration_minutes = Column(Float, default=0.0)
    avg_quality_score = Column(Float, default=0.0)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    # Relationships
    current_task = relationship(
        "Task", back_populates="assigned_agent", foreign_keys=[current_task_id]
    )
    capabilities = relationship("AgentCapability", back_populates="agent")
    recommendations = relationship("TaskRecommendation", back_populates="agent")


class AgentCapability(Base):
    """Agent capability scores by task type."""

    __tablename__ = "agent_capabilities"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    agent_id = Column(String(100), ForeignKey("agents.id"), nullable=False)
    task_type = Column(String(18), nullable=False)
    confidence_score = Column(Float, default=0.0)
    tasks_completed = Column(Integer, default=0)
    tasks_failed = Column(Integer, default=0)
    last_used = Column(DateTime)

    agent = relationship("Agent", back_populates="capabilities")


class TaskRecommendation(Base):
    """Task-agent recommendation scores and outcomes."""

    __tablename__ = "task_recommendations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    task_id = Column(String(36), ForeignKey("tasks.id"), nullable=False)
    agent_id = Column(String(100), ForeignKey("agents.id"), nullable=False)
    priority_score = Column(Float, default=0.0)
    capability_match_score = Column(Float)
    final_score = Column(Float)
    was_recommended = Column(Boolean, default=False)
    was_accepted = Column(Boolean, default=False)
    was_completed = Column(Boolean, default=False)
    quality_score = Column(Float)
    actual_duration_minutes = Column(Integer)
    feedback = Column(Text)
    recommended_at = Column(DateTime)
    accepted_at = Column(DateTime)
    completed_at = Column(DateTime)

    task = relationship("Task", back_populates="recommendations")
    agent = relationship("Agent", back_populates="recommendations")


class TaskHistory(Base):
    """Task status change history."""

    __tablename__ = "task_history"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    task_id = Column(String(36), ForeignKey("tasks.id"), nullable=False)
    previous_status = Column(String(11))
    new_status = Column(String(11), nullable=False)
    changed_by = Column(String(100))
    change_reason = Column(Text)
    created_at = Column(DateTime)
    extra_data = Column(Text)  # JSON

    task = relationship("Task", back_populates="history")


# ──────────────────────────────────────────────
# Command Center tables
# ──────────────────────────────────────────────


class CCAgent(Base):
    """Command Center agent record (maps tmux sessions to persistent state)."""

    __tablename__ = "cc_agents"

    id = Column(String(100), primary_key=True)
    session_id = Column(String(100), unique=True)
    name = Column(String(200), nullable=False)
    role = Column(String(100))
    status = Column(String(20), default="offline")
    current_task_id = Column(String(100))
    domain = Column(String(100))
    project = Column(String(100))
    skills = Column(Text, default="[]")  # JSON
    token_usage = Column(Text, default="{}")  # JSON
    last_activity = Column(DateTime)
    tmux_session = Column(String(100))
    node_id = Column(String(100), default="local")
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC))

    # Relationships
    tasks = relationship("CCTask", back_populates="agent", foreign_keys="CCTask.assigned_to")
    assignments = relationship("TaskAssignment", back_populates="agent")
    sessions = relationship("Session", back_populates="agent")


class CCTask(Base):
    """Command Center task record."""

    __tablename__ = "cc_tasks"

    id = Column(String(100), primary_key=True)
    title = Column(String(500), nullable=False)
    description = Column(Text)
    status = Column(String(20), default="pending")
    assigned_to = Column(String(100), ForeignKey("cc_agents.id"))
    priority = Column(Integer, default=5)
    domain = Column(String(100))
    project = Column(String(100))
    acceptance_criteria = Column(Text, default="[]")  # JSON
    deliverables = Column(Text, default="[]")  # JSON
    blocked_by = Column(Text, default="[]")  # JSON
    blocks = Column(Text, default="[]")  # JSON
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC))

    agent = relationship("CCAgent", back_populates="tasks", foreign_keys=[assigned_to])
    assignments = relationship("TaskAssignment", back_populates="task")


class TaskAssignment(Base):
    """Task assignment tracking."""

    __tablename__ = "task_assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(100), ForeignKey("cc_tasks.id"), nullable=False)
    agent_id = Column(String(100), ForeignKey("cc_agents.id"), nullable=False)
    assigned_at = Column(DateTime, default=lambda: datetime.now(UTC))
    completed_at = Column(DateTime)
    notification_sent = Column(Boolean, default=False)

    task = relationship("CCTask", back_populates="assignments")
    agent = relationship("CCAgent", back_populates="assignments")


class Session(Base):
    """Agent work session record."""

    __tablename__ = "sessions"

    id = Column(String(100), primary_key=True)
    agent_id = Column(String(100), ForeignKey("cc_agents.id"), nullable=False)
    start_time = Column(DateTime, default=lambda: datetime.now(UTC))
    end_time = Column(DateTime)
    context_summary = Column(Text)
    files_modified = Column(Text, default="[]")  # JSON
    token_usage = Column(Integer, default=0)
    cost_estimate = Column(Float, default=0.0)
    deliverables = Column(Text, default="[]")  # JSON
    checkpoint_data = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC))

    agent = relationship("CCAgent", back_populates="sessions")


class Node(Base):
    """Multi-node infrastructure record."""

    __tablename__ = "nodes"

    id = Column(String(100), primary_key=True)
    name = Column(String(200), nullable=False)
    hostname = Column(String(255))
    ip_address = Column(String(45))
    status = Column(String(20), default="offline")
    last_heartbeat = Column(DateTime)
    capabilities = Column(Text, default="[]")  # JSON
    cpu_cores = Column(Integer, default=0)
    memory_gb = Column(Float, default=0.0)
    disk_gb = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC))

    sync_logs = relationship("SyncLog", back_populates="node")


class SyncLog(Base):
    """Cross-node synchronization log."""

    __tablename__ = "sync_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    table_name = Column(String(100), nullable=False)
    record_id = Column(String(100), nullable=False)
    operation = Column(String(10), nullable=False)
    node_id = Column(String(100), ForeignKey("nodes.id"))
    old_value = Column(Text)
    new_value = Column(Text)
    timestamp = Column(DateTime, default=lambda: datetime.now(UTC))
    checksum = Column(String(64))

    node = relationship("Node", back_populates="sync_logs")
