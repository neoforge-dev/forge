"""Claim Envelope model for completion verification.

This module provides the ClaimEnvelope schema used by agents
to submit completion claims with evidence tracking.
"""

from pydantic import BaseModel, Field


class ClaimEnvelope(BaseModel):
    """Envelope for completion claims with evidence tracking.

    Used to submit completion claims that require verification through evidence.
    Tracks what was changed, by whom, and provides digest of all artifacts.

    Attributes:
        task_id: Unique identifier for task being completed
        agent_id: Which agent completed work
        branch: Git branch where work was done
        commit_sha: Git commit hash for verification
        changed_files: List of files modified/created
        checks_run: Commands executed and their results
        artifacts: Evidence produced (test reports, screenshots, logs)
        risk_level: Risk classification (low, medium, high, critical)
        evidence_digest: SHA256 digest of all evidence
    """

    task_id: str = Field(..., description="Task identifier")
    agent_id: str = Field(..., description="Agent who completed work")
    branch: str = Field(..., description="Git branch")
    commit_sha: str = Field(..., description="Git commit SHA")
    changed_files: list[str] = Field(..., description="List of modified files")
    checks_run: list[dict] = Field(..., description="Command execution log")
    artifacts: list[str] = Field(..., description="Evidence artifacts produced")
    risk_level: str = Field(..., description="Risk level (low|medium|high|critical)")
    evidence_digest: str = Field(..., description="SHA256 digest of all evidence")
