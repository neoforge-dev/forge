"""Verification models for FORGE claims and evidence tracking."""

from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class VerificationType(str, Enum):
    """Types of verification claims."""

    TEST_RESULT = "test_result"
    FEATURE_COMPLETE = "feature_complete"
    REVIEW_PASSED = "review_passed"
    SECURITY_SCAN = "security_scan"
    PERFORMANCE_CHECK = "performance_check"


class VerificationStatus(str, Enum):
    """Status of a verification claim."""

    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
    EXPIRED = "expired"


class VerificationEvidence(BaseModel):
    """Evidence supporting a verification claim."""

    type: str = Field(..., description="Type of evidence")
    content: dict[str, Any] = Field(..., description="Evidence content")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str = Field(..., description="Source of evidence")


class VerificationClaim(BaseModel):
    """A claim that can be verified with evidence."""

    id: str = Field(..., description="Unique claim ID")
    type: VerificationType = Field(..., description="Type of claim")
    status: VerificationStatus = Field(
        default=VerificationStatus.PENDING, description="Current status"
    )
    evidence: list[VerificationEvidence] = Field(
        default_factory=list, description="Supporting evidence"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    verified_at: datetime | None = Field(None, description="When claim was verified")
    expires_at: datetime | None = Field(None, description="Optional expiration")
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "VerificationType",
    "VerificationStatus",
    "VerificationEvidence",
    "VerificationClaim",
]
