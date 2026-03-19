"""GDPR compliance models."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, EmailStr, Field, validator


class ConsentType(str, Enum):
    """Types of consent."""

    MARKETING = "marketing"
    ANALYTICS = "analytics"
    COOKIES = "cookies"
    DATA_PROCESSING = "data_processing"
    PROFILING = "profiling"


class DataExportFormat(str, Enum):
    """Data export format."""

    JSON = "json"
    CSV = "csv"
    XML = "xml"


class ConsentRecord(BaseModel):
    """Record of user consent."""

    id: str
    email: EmailStr
    consent_type: ConsentType
    given: bool
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    ip_address: str | None = None
    user_agent: str | None = None
    source: str = "unknown"
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @validator("source")
    def source_not_empty(cls, v):
        """Validate source is not empty."""
        if not v or not v.strip():
            raise ValueError("Source cannot be empty")
        return v


class DataExportRequest(BaseModel):
    """Request for data export."""

    id: str
    email: EmailStr
    format: DataExportFormat = DataExportFormat.JSON
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    status: str = "pending"  # pending, processing, completed, failed
    data_size_mb: float | None = None
    download_url: str | None = None
    expires_at: datetime | None = None
    error_message: str | None = None

    @validator("status")
    def valid_status(cls, v):
        """Validate status."""
        valid_statuses = {"pending", "processing", "completed", "failed"}
        if v not in valid_statuses:
            raise ValueError(f"Status must be one of {valid_statuses}")
        return v


class DeletionRequest(BaseModel):
    """Request for data deletion (right to be forgotten)."""

    id: str
    email: EmailStr
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    status: str = "pending"  # pending, processing, completed, failed
    reason: str | None = None
    verification_code: str | None = None
    verified_at: datetime | None = None
    error_message: str | None = None

    @validator("status")
    def valid_status(cls, v):
        """Validate status."""
        valid_statuses = {"pending", "processing", "completed", "failed"}
        if v not in valid_statuses:
            raise ValueError(f"Status must be one of {valid_statuses}")
        return v

    def is_verified(self) -> bool:
        """Check if deletion request is verified."""
        return self.verified_at is not None

    def is_completed(self) -> bool:
        """Check if deletion is completed."""
        return self.status == "completed"
