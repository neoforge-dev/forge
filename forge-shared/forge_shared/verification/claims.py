"""Claim Envelope Schema for FORGE verification system.

This schema defines the structure for evidence-based completion claims.
Supports task ID tracking, agent identification, change tracking, and risk assessment.
"""

from pydantic import BaseModel, Field, field_validator
from typing import List
from enum import Enum


class RiskLevel(str, Enum):
    """Risk level enumeration for completion claims."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ClaimEnvelope(BaseModel):
    """Envelope for completion claims with evidence tracking.

    Used to submit completion claims that require verification through evidence.
    Tracks what was changed, by whom, and provides digest of all artifacts.

    Attributes:
        task_id: Unique identifier for the task being completed
        agent_id: Which agent completed the work
        branch: Git branch where work was done
        commit_sha: Git commit hash for verification
        changed_files: List of files modified/created
        checks_run: Commands executed and their results
        artifacts: Evidence produced (test reports, screenshots, logs)
        risk_level: Risk classification (low, medium, high, critical)
        evidence_digest: SHA256 digest of all evidence files
    """

    task_id: str = Field(..., description="Task identifier", min_length=1)
    agent_id: str = Field(..., description="Agent who completed work", min_length=1)
    branch: str = Field(..., description="Git branch", min_length=1)
    commit_sha: str = Field(..., description="Git commit SHA", min_length=7)
    changed_files: List[str] = Field(..., description="List of modified files")
    checks_run: List[dict] = Field(..., description="Command execution log")
    artifacts: List[str] = Field(..., description="Evidence artifacts produced")
    risk_level: RiskLevel = Field(..., description="Risk level: low|medium|high|critical")
    evidence_digest: str = Field(..., description="SHA256 digest of all evidence", min_length=64)

    @field_validator("evidence_digest")
    @classmethod
    def validate_sha256(cls, v):
        """Validate that evidence_digest is a valid SHA256 hex string."""
        if len(v) != 64:
            raise ValueError("evidence_digest must be exactly 64 hex characters")
        if not all(c in "0123456789abcdef" for c in v):
            raise ValueError("evidence_digest must be valid hex")
        return v
